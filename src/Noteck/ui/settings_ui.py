"""Settings page UI for the Noteck add-on.

This module is lazy-loaded by the UI shell (`ui.ui.NotionWindow`)
when the user selects the "Settings" tab.

The UI is schema-driven: setting metadata (type/labels/defaults/storage) comes from
`src/Noteck/docs/settings.json` via `load_settings_schema()`.
Values are persisted via `SettingsStore` (SQLite + optional keyring for secrets).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aqt.qt import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStandardItem,
    QStandardItemModel,
    QTimer,
    Qt,
    QVBoxLayout,
    QWidget,
)

from ..modules.card_types import DEFAULT_SELECTABLE_CARD_TYPES, card_type_label
from ..modules.cards import (
    CARD_TEMPLATE_STATUS_CURRENT,
    CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE,
    CARD_TEMPLATE_STATUS_USER_MODIFIED,
    card_template_status,
    restore_default_card_templates,
    set_review_tags_visible,
)
from ..modules.db import Database
from ..modules.pages import (
    PAGE_SELECTION_BEHAVIORS,
    PAGE_SELECTION_BEHAVIOR_DETAILS,
    page_selection_behavior_description,
    page_selection_behavior_tooltip,
)
from ..modules.settings import (
    SettingDefinition,
    SettingsError,
    SettingsSchema,
    SettingsStore,
    load_settings_schema,
)
from ..modules.sync import (
    cloze_marker_colors_need_sync,
    register_sync_done_callback,
    run_notion_sync_with_progress,
    sync_notion_to_anki,
    unregister_sync_done_callback,
)
from .ui import UiContext


@dataclass(frozen=True)
class _WidgetBinding:
    """Pair a setting definition with its input widget for save/reload operations."""

    definition: SettingDefinition
    widget: QWidget


class _MultiSelectDropdown(QComboBox):
    """Native combo box whose popup contains independently checkable options."""

    def __init__(self, options: tuple[str, ...], parent: QWidget) -> None:
        super().__init__(parent)
        self._options = options
        self._callbacks: list[Any] = []
        self._keep_popup_open = False
        model = QStandardItemModel(self)
        self.setModel(model)
        for option in options:
            item = QStandardItem(option.title())
            item.setData(option, Qt.ItemDataRole.UserRole)
            item.setCheckable(True)
            item.setCheckState(Qt.CheckState.Unchecked)
            model.appendRow(item)

        self.view().pressed.connect(self._toggle_index)
        # Reset the combo's transient current item after Qt processes a popup click.
        self.activated.connect(lambda _index: QTimer.singleShot(0, self._refresh_text))
        self._refresh_text()

    def _toggle_index(self, index: Any) -> None:
        """Toggle one popup row and keep the popup open for further choices."""
        item = self.model().itemFromIndex(index)
        if item is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)
        self._keep_popup_open = True
        self._refresh_text()
        for callback in self._callbacks:
            callback()

    def hidePopup(self) -> None:
        """Do not close the popup immediately after toggling an option."""
        if self._keep_popup_open:
            self._keep_popup_open = False
            return
        super().hidePopup()

    def showPopup(self) -> None:
        """Keep the popup at least as wide as the native combo box."""
        longest_label = max(
            (self.fontMetrics().horizontalAdvance(option.title()) for option in self._options),
            default=0,
        )
        self.view().setMinimumWidth(max(self.width(), longest_label + 58))
        super().showPopup()

    def selected_values(self) -> list[str]:
        """Return checked values in schema order."""
        model = self.model()
        return [
            str(model.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(model.rowCount())
            if model.item(row).checkState() == Qt.CheckState.Checked
        ]

    def set_selected_values(self, values: Any) -> None:
        """Apply a stored selection without depending on menu display labels."""
        selected = {str(value) for value in values} if isinstance(values, (list, tuple, set)) else set()
        model = self.model()
        for row in range(model.rowCount()):
            item = model.item(row)
            item.setCheckState(
                Qt.CheckState.Checked
                if str(item.data(Qt.ItemDataRole.UserRole)) in selected
                else Qt.CheckState.Unchecked
            )
        self._refresh_text()

    def connect_changed(self, callback: Any) -> None:
        """Invoke callback after any option is toggled."""
        self._callbacks.append(callback)

    def _refresh_text(self, _checked: bool = False) -> None:
        selected = self.selected_values()
        if not selected:
            text = "No colors selected"
        elif len(selected) == len(self._options):
            text = "All colors"
        else:
            text = ", ".join(value.title() for value in selected)
        self.setPlaceholderText(text)
        self.setCurrentIndex(-1)


class SettingsPage(QWidget):
    """Settings tab widget driven by the JSON settings schema."""

    def __init__(self, parent: QWidget, context: UiContext) -> None:
        super().__init__(parent)
        self._context = context
        self._schema: SettingsSchema = load_settings_schema()

        # The DB is initialized on profile open; keep this lightweight and just bind to it.
        self._db = Database(context.db_path)

        # Use the active Anki profile name (if available) to namespace keyring secrets.
        profile_name = self._resolve_profile_name(context)
        self._store = SettingsStore(self._db, profile_name=profile_name, schema=self._schema)

        self._bindings: dict[str, _WidgetBinding] = {}
        self._description_labels: dict[str, QLabel] = {}
        self._cloze_marker_sync_warning_callout: QWidget | None = None
        self._is_loading = False

        # Keep a stable bound-method reference so it can be unregistered when
        # Qt destroys this lazily loaded Settings page.
        self._sync_done_callback = self._on_notion_sync_done
        register_sync_done_callback(self._sync_done_callback)
        self.destroyed.connect(
            lambda _object=None: unregister_sync_done_callback(self._sync_done_callback)
        )

        # build the UI
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(11, 11, 11, 11)
        root_layout.addWidget(self._build_scroll_area(), 1)

    def _build_scroll_area(self) -> QScrollArea:
        """Build the scrollable content area containing settings categories."""
        # set up a frameless, transparent scroll area
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )

        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # build a group box for each settings category
        for category in self._schema.categories:
            group, form_layout = self._build_category_group(category.name, category.description)

            # build input widgets for each setting in the category
            for setting in category.settings:
                input_widget = self._build_setting_widget(setting)
                self._bindings[setting.key] = _WidgetBinding(definition=setting, widget=input_widget)
                
                # add checkbox
                if isinstance(input_widget, QCheckBox):
                    input_widget.setText(setting.name)
                    form_layout.addRow(input_widget)
                # add button
                elif isinstance(input_widget, QPushButton):
                    form_layout.addRow(input_widget)
                # add other input types
                else:
                    label = QLabel(setting.name, group)
                    label.setToolTip(self._setting_tooltip(setting))
                    form_layout.addRow(label, input_widget)

                    # add an extra description label for settings that need one
                    if setting.key == "page_selection_behavior":
                        description_label = QLabel("", group)
                        description_label.setWordWrap(True)
                        description_label.setStyleSheet("font-style: italic;")
                        self._description_labels[setting.key] = description_label
                        form_layout.addRow(description_label)
                    # add a warning callout for cloze marker colors that need a Notion sync
                    elif setting.key == "cloze_marker_colors":
                        warning_callout = self._build_cloze_marker_sync_warning(group)
                        warning_callout.setVisible(False)
                        self._cloze_marker_sync_warning_callout = warning_callout
                        form_layout.addRow(warning_callout)

                self._wire_autosave(setting.key, input_widget)

            content_layout.addWidget(group)

        # Stretch at the bottom keeps group boxes pinned to the top.
        content_layout.addStretch(1)
        scroll.setWidget(content)

        return scroll

    def _build_category_group(self, name: str, description: str) -> tuple[QGroupBox, QFormLayout]:
        """Create a category `QGroupBox` with a description and a form layout."""
        group = QGroupBox(name, self)
        layout = QVBoxLayout(group)

        # add description if provided
        if description:
            description_label = QLabel(description, group)
            description_label.setWordWrap(True)
            description_label.setContentsMargins(0, 0, 0, 7)
            layout.addWidget(description_label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)

        return group, form

    def _build_cloze_marker_sync_warning(self, parent: QWidget) -> QWidget:
        """Build a Notion-style yellow warning callout with icon and message."""
        dark_theme = self.palette().window().color().lightness() < 128
        background = "#494327" if dark_theme else "#fbf3db"
        foreground = "#f5f5f5" if dark_theme else "#2f2f2f"

        callout = QWidget(parent)
        callout.setStyleSheet(
            f"background-color: {background}; color: {foreground}; border-radius: 5px;"
        )
        layout = QHBoxLayout(callout)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)

        icon = QLabel("⚠️", callout)
        icon.setStyleSheet("background: transparent; font-size: 18px;")
        alignment_flag = getattr(Qt, "AlignmentFlag", None)
        align_top = (
            alignment_flag.AlignTop
            if alignment_flag is not None
            else getattr(Qt, "AlignTop")
        )
        icon.setAlignment(align_top)
        layout.addWidget(icon, 0)

        message = QLabel(
            "Marker-color changes apply to existing cards only after syncing Notion again.",
            callout,
        )
        message.setWordWrap(True)
        message.setStyleSheet(f"background: transparent; color: {foreground};")
        layout.addWidget(message, 1)
        return callout

    def _build_setting_widget(self, setting: SettingDefinition) -> QWidget:
        """Return an input widget for a setting definition."""
        if setting.type in {"checkbox", "boolean"}:
            widget = QCheckBox(self)
            widget.setToolTip(self._setting_tooltip(setting))

            return widget

        if setting.type == "button":
            widget = QPushButton(setting.name, self)
            widget.setToolTip(self._setting_tooltip(setting))
            if setting.key == "restore_default_card_templates":
                widget.setVisible(False)
            return widget

        if setting.type == "dropdown":
            widget = QComboBox(self)
            widget.setToolTip(self._setting_tooltip(setting))
            if setting.key == "default_card_type":
                for card_type in DEFAULT_SELECTABLE_CARD_TYPES:
                    widget.addItem(card_type_label(card_type, abbreviation=False), card_type)
            elif setting.key == "page_selection_behavior":
                for behavior in PAGE_SELECTION_BEHAVIORS:
                    name, _ = PAGE_SELECTION_BEHAVIOR_DETAILS[behavior]
                    widget.addItem(name, behavior)
                    widget.setItemData(
                        widget.count() - 1,
                        page_selection_behavior_tooltip(behavior),
                        self._item_data_role_tooltip(),
                    )
            elif setting.options:
                widget.addItems(list(setting.options))

            return widget

        if setting.type == "multiselect":
            widget = _MultiSelectDropdown(setting.options or (), self)
            widget.setToolTip(self._setting_tooltip(setting))
            return widget

        if setting.type == "text":
            widget = QLineEdit(self)
            widget.setToolTip(self._setting_tooltip(setting))

            # Secrets are stored in keyring; keep the input masked by default.
            if setting.storage == "keyring":
                widget.setEchoMode(self._password_echo_mode())
                widget.setPlaceholderText("Stored securely in your OS keychain (leave empty to clear).")
            else:
                widget.setPlaceholderText("" if setting.default is None else str(setting.default))

            return widget

        # The schema loader validates types, but keep a defensive fallback in the UI.
        fallback = QLabel(f"Unsupported setting type: {setting.type}", self)
        fallback.setWordWrap(True)
        return fallback

    def _setting_tooltip(self, setting: SettingDefinition) -> str:
        """Return short hover text for a setting."""
        return setting.tooltip or setting.description

    def showEvent(self, event: Any) -> None:
        """Reload values whenever the Settings tab becomes visible."""
        super().showEvent(event)
        self.reload_values()

    def _wire_autosave(self, key: str, widget: QWidget) -> None:
        """Connect widget change signals to auto-save the updated value."""
        if isinstance(widget, QCheckBox):
            widget.stateChanged.connect(lambda _state, k=key: self._autosave_setting(k))
        elif isinstance(widget, _MultiSelectDropdown):
            widget.connect_changed(lambda k=key: self._autosave_setting(k))
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(lambda _index, k=key: self._on_dropdown_changed(k))
        elif isinstance(widget, QLineEdit):
            # Saving on every keystroke is noisy (and for secrets may be undesirable);
            # `editingFinished` persists when the user leaves the field or presses Enter.
            widget.editingFinished.connect(lambda k=key: self._autosave_setting(k))
        elif isinstance(widget, QPushButton):
            widget.clicked.connect(
                lambda _checked=False, k=key, button=widget: self._trigger_button_action(k, button)
            )

    def reload_values(self) -> None:
        """Reload persisted values into the input widgets."""
        self._is_loading = True
        try:
            for key, binding in self._bindings.items():
                setting = binding.definition
                widget = binding.widget
                if isinstance(widget, QPushButton):
                    continue
                try:
                    value = self._store.get_value(key)
                except SettingsError as exc:
                    self._show_error(f"Failed to load setting '{key}'.\n\n{exc}")
                    continue

                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, _MultiSelectDropdown):
                    widget.set_selected_values(value)
                elif isinstance(widget, QComboBox):
                    # If the stored value is invalid/missing, fall back to the default option.
                    text = str(value) if value is not None else str(setting.default or "")
                    index = -1
                    for option_index in range(widget.count()):
                        if widget.itemData(option_index) is not None:
                            index = widget.findData(text)
                            break
                    if index < 0:
                        index = widget.findText(text)
                    widget.setCurrentIndex(index if index >= 0 else 0)
                    self._refresh_setting_description(key)
                elif isinstance(widget, QLineEdit):
                    widget.setText("" if value is None else str(value))
        finally:
            self._is_loading = False
        self._refresh_cloze_marker_sync_warning()
        self._refresh_card_template_action()

    def _on_dropdown_changed(self, key: str) -> None:
        """Refresh dropdown-dependent UI text and persist the selected value."""
        self._refresh_setting_description(key)
        self._autosave_setting(key)

    def _refresh_setting_description(self, key: str) -> None:
        """Update the extra description label for settings that need one."""
        description_label = self._description_labels.get(key)
        binding = self._bindings.get(key)
        if description_label is None or binding is None:
            return

        widget = binding.widget
        if key != "page_selection_behavior" or not isinstance(widget, QComboBox):
            return

        selected_behavior = widget.currentData()
        description_label.setText(page_selection_behavior_description(str(selected_behavior)))

    def _autosave_setting(self, key: str) -> None:
        """Persist a single setting based on its current widget value."""
        if self._is_loading:
            return

        binding = self._bindings.get(key)
        if binding is None:
            return

        # extract the updated value from the widget
        widget = binding.widget
        if isinstance(widget, QCheckBox):
            new_value: Any = bool(widget.isChecked())
        elif isinstance(widget, _MultiSelectDropdown):
            new_value = widget.selected_values()
        elif isinstance(widget, QComboBox):
            selected_data = widget.currentData()
            new_value = selected_data if selected_data is not None else widget.currentText()
        elif isinstance(widget, QLineEdit):
            new_value = widget.text()
        else:
            return

        # save the updated value
        try:
            self._store.set_value(key, new_value)
        except SettingsError as exc:
            self._show_error(f"Failed to save setting '{key}'.\n\n{exc}")
            return

        # apply review visibility without requiring a Notion sync
        if key == "show_tags_during_review":
            set_review_tags_visible(self._context.mw, bool(new_value))
        if key == "cloze_marker_colors":
            self._refresh_cloze_marker_sync_warning()

    def _refresh_cloze_marker_sync_warning(self) -> None:
        """Show whether marker-color changes still need a successful Notion sync."""
        callout = self._cloze_marker_sync_warning_callout
        binding = self._bindings.get("cloze_marker_colors")
        if callout is None or binding is None or not isinstance(binding.widget, _MultiSelectDropdown):
            return
        callout.setVisible(
            cloze_marker_colors_need_sync(self._db, binding.widget.selected_values())
        )

    def _on_notion_sync_done(self, _result: Any) -> None:
        """Refresh the callout after startup, Anki-button, or manual Notion sync."""
        self._refresh_cloze_marker_sync_warning()

    def _trigger_action(self, key: str) -> None:
        """Execute non-persistent action settings that are rendered as buttons."""
        if key == "sync_notion_now":
            self._run_manual_notion_sync()
            return
        if key == "restore_default_card_templates":
            self._restore_default_card_templates()
            return

        QMessageBox.information(self, "Settings", f"No action is registered for '{key}'.")

    def _trigger_button_action(self, key: str, button: QPushButton) -> None:
        """Run a button action, then remove mouse-click focus while preserving tab focus."""
        try:
            self._trigger_action(key)
        finally:
            QTimer.singleShot(0, button.clearFocus)

    @staticmethod
    def _item_data_role_tooltip() -> Any:
        """Return the Qt tooltip role in a Qt-version-safe way."""
        from aqt.qt import Qt

        item_data_role = getattr(Qt, "ItemDataRole", None)
        if item_data_role is not None and hasattr(item_data_role, "ToolTipRole"):
            return getattr(item_data_role, "ToolTipRole")
        return getattr(Qt, "ToolTipRole")

    def _restore_default_card_templates(self) -> None:
        """Reset Noteck note type templates after explicit user confirmation."""
        status = card_template_status(self._context.mw)
        if not self._confirm_restore_default_card_templates(status):
            return

        try:
            restore_default_card_templates(self._context.mw)
            set_review_tags_visible(self._context.mw, bool(self._store.get_value("show_tags_during_review")))
        except Exception as exc:
            self._show_error(f"Failed to restore default card templates.\n\n{exc}")
            return

        QMessageBox.information(
            self,
            "Settings",
            "Card templates have been updated.",
        )
        self._refresh_card_template_action()

    def _refresh_card_template_action(self) -> None:
        """Show the restore action only when installed card templates differ."""
        binding = self._bindings.get("restore_default_card_templates")
        button = binding.widget if binding is not None else None
        if not isinstance(button, QPushButton):
            return

        try:
            status = card_template_status(self._context.mw)
        except Exception as exc:
            button.setVisible(True)
            button.setEnabled(False)
            button.setToolTip(f"Could not inspect card templates: {exc}")
            return

        should_show = status != CARD_TEMPLATE_STATUS_CURRENT
        button.setVisible(should_show)
        button.setEnabled(should_show)
        if status == CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE:
            button.setText("Update card templates")
            button.setToolTip("Install the latest bundled Noteck card templates.")
        else:
            button.setText(binding.definition.name)
            button.setToolTip(binding.definition.description)

    def _confirm_restore_default_card_templates(self, status: str) -> bool:
        """Ask before overwriting user-customized Noteck card templates."""
        title = "Update card templates"
        message = "This will update Noteck card templates to the latest bundled version. Continue?"
        if status == CARD_TEMPLATE_STATUS_USER_MODIFIED:
            title = "Restore default card templates"
            message = (
                "This will overwrite the HTML and styling for Noteck card templates "
                "with the bundled defaults. Continue?"
            )

        result = QMessageBox.question(
            self,
            title,
            message,
        )
        standard_button = getattr(QMessageBox, "StandardButton", None)
        yes_value = standard_button.Yes if standard_button is not None else QMessageBox.Yes
        return result == yes_value

    def _run_manual_notion_sync(self) -> None:
        """Trigger a manual Notion refresh through the Pages tab when available."""
        binding = self._bindings.get("sync_notion_now")
        button = binding.widget if binding is not None else None
        if not isinstance(button, QPushButton):
            self._show_error("Manual sync button is not available.")
            return

        button.setEnabled(False)
        original_text = button.text()
        button.setText("Syncing...")

        def finish_manual_sync(result: Any) -> None:
            # Always restore button state, regardless of success/failure/cancel.
            button.setEnabled(True)
            button.setText(original_text)
            self._refresh_cloze_marker_sync_warning()
            _ = result

        started = run_notion_sync_with_progress(
            mw=self._context.mw,
            db_path=self._context.db_path,
            on_done=finish_manual_sync,
            parent=self,
        )
        if started:
            return

        result = sync_notion_to_anki(
            mw=self._context.mw,
            db_path=self._context.db_path,
        )
        finish_manual_sync(result)

    def _show_error(self, message: str) -> None:
        """Show an error message box."""
        QMessageBox.critical(self, "Settings error", message)

    @staticmethod
    def _resolve_profile_name(context: UiContext) -> str | None:
        """Try to determine the current Anki profile name for secret namespacing."""
        pm = getattr(context.mw, "pm", None)
        if pm is None:
            return None
        
        name_attr = getattr(pm, "name", None)
        if callable(name_attr):
            try:
                return str(name_attr())
            except Exception:
                return None
        if isinstance(name_attr, str):
            return name_attr
        
        return None

    @staticmethod
    def _password_echo_mode() -> Any:
        """Return the best-effort password echo mode for the installed Qt binding."""
        # Anki uses Qt via aqt.qt; depending on the Qt/PyQt version, the constant is exposed
        # either as QLineEdit.EchoMode.Password (Qt6-style) or QLineEdit.Password (Qt5-style).
        echo_mode = getattr(QLineEdit, "EchoMode", None)
        if echo_mode is not None and hasattr(echo_mode, "Password"):
            return echo_mode.Password
        return getattr(QLineEdit, "Password")


def build_page(parent: QWidget, context: UiContext) -> QWidget:
    """Factory used by ui.json to create the Settings page widget."""
    return SettingsPage(parent, context)
