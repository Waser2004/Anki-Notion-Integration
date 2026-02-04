"""Settings page UI for the Anki-Notion integration add-on.

This module is lazy-loaded by the UI shell (`anki_notion_integration.ui.ui.NotionWindow`)
when the user selects the "Settings" tab.

The UI is schema-driven: setting metadata (type/labels/defaults/storage) comes from
`src/anki_notion_integration/docs/settings.json` via `load_settings_schema()`.
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
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_notion_integration.db import Database
from anki_notion_integration.settings import (
    SettingDefinition,
    SettingsError,
    SettingsSchema,
    SettingsStore,
    load_settings_schema,
)
from anki_notion_integration.sync import run_notion_sync_with_progress, sync_notion_to_anki
from anki_notion_integration.ui.ui import UiContext


@dataclass(frozen=True)
class _WidgetBinding:
    """Pair a setting definition with its input widget for save/reload operations."""

    definition: SettingDefinition
    widget: QWidget


class SettingsPage(QWidget):
    """Settings tab widget driven by the JSON settings schema."""

    def __init__(self, parent: QWidget, context: UiContext) -> None:
        super().__init__(parent)
        self._context = context
        self._schema: SettingsSchema = load_settings_schema()

        # The DB is initialized on profile open; keep this lightweight and just bind to it.
        db = Database(context.db_path)

        # Use the active Anki profile name (if available) to namespace keyring secrets.
        profile_name = self._resolve_profile_name(context)
        self._store = SettingsStore(db, profile_name=profile_name, schema=self._schema)

        self._bindings: dict[str, _WidgetBinding] = {}
        self._is_loading = False

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
                    label.setToolTip(setting.description)
                    form_layout.addRow(label, input_widget)

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

    def _build_setting_widget(self, setting: SettingDefinition) -> QWidget:
        """Return an input widget for a setting definition."""
        if setting.type in {"checkbox", "boolean"}:
            widget = QCheckBox(self)
            widget.setToolTip(setting.description)

            return widget

        if setting.type == "button":
            widget = QPushButton(setting.name, self)
            widget.setToolTip(setting.description)
            return widget

        if setting.type == "dropdown":
            widget = QComboBox(self)
            widget.setToolTip(setting.description)
            if setting.options:
                widget.addItems(list(setting.options))

            return widget

        if setting.type == "text":
            widget = QLineEdit(self)
            widget.setToolTip(setting.description)

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

    def showEvent(self, event: Any) -> None:
        """Reload values whenever the Settings tab becomes visible."""
        super().showEvent(event)
        self.reload_values()

    def _wire_autosave(self, key: str, widget: QWidget) -> None:
        """Connect widget change signals to auto-save the updated value."""
        if isinstance(widget, QCheckBox):
            widget.stateChanged.connect(lambda _state, k=key: self._autosave_setting(k))
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(lambda _index, k=key: self._autosave_setting(k))
        elif isinstance(widget, QLineEdit):
            # Saving on every keystroke is noisy (and for secrets may be undesirable);
            # `editingFinished` persists when the user leaves the field or presses Enter.
            widget.editingFinished.connect(lambda k=key: self._autosave_setting(k))
        elif isinstance(widget, QPushButton):
            widget.clicked.connect(lambda _checked=False, k=key: self._trigger_action(k))

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
                elif isinstance(widget, QComboBox):
                    # If the stored value is invalid/missing, fall back to the default option.
                    text = str(value) if value is not None else str(setting.default or "")
                    index = widget.findText(text)
                    widget.setCurrentIndex(index if index >= 0 else 0)
                elif isinstance(widget, QLineEdit):
                    widget.setText("" if value is None else str(value))
        finally:
            self._is_loading = False

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
        elif isinstance(widget, QComboBox):
            new_value = widget.currentText()
        elif isinstance(widget, QLineEdit):
            new_value = widget.text()
        else:
            return

        # save the updated value
        try:
            self._store.set_value(key, new_value)
        except SettingsError as exc:
            self._show_error(f"Failed to save setting '{key}'.\n\n{exc}")

    def _trigger_action(self, key: str) -> None:
        """Execute non-persistent action settings that are rendered as buttons."""
        if key == "sync_notion_now":
            self._run_manual_notion_sync()
            return

        QMessageBox.information(self, "Settings", f"No action is registered for '{key}'.")

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
