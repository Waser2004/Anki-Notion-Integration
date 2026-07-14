"""UI shell for the Noteck add-on."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Any

# Import Anki/Qt modules only when running inside Anki.
from aqt import gui_hooks, mw
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QShowEvent,
    QTabWidget,
    QVBoxLayout,
    QTimer,
    QWidget,
)

from .release_notes_ui import show_release_notes

from .ui_schema import UiPageDefinition, UiSchema, UiSchemaError, load_ui_schema


@dataclass(frozen=True)
class UiContext:
    """Shared context passed to page factories."""

    mw: Any
    profile_folder: Path
    db_path: Path


_initialized = False
_window: "NotionWindow | None" = None


def initialize_ui() -> None:
    """Register the toolbar link that opens the Notion window."""
    global _initialized
    if _initialized:
        return
    schema = load_ui_schema()

    def add_toolbar_link(links: list[str], toolbar: Any) -> None:
        # Inject a top-toolbar link via the supported GUI hook.
        links.insert(
            -1, # Before the Sync button.
            toolbar.create_link(
                cmd="notion",
                label="Notion",
                func=lambda: _show_window(schema),
                tip="Open Notion add-on window",
                id="notion",
            )
        )

    gui_hooks.top_toolbar_did_init_links.append(add_toolbar_link)
    # Redraw immediately so the link appears without a restart.
    if hasattr(mw, "toolbar"):
        mw.toolbar.draw()
    _initialized = True


def _show_window(schema: UiSchema) -> None:
    """Create or reuse the Notion window and bring it to the front."""
    global _window
    # build the window on first use
    if _window is None:
        context = _build_context()
        _window = NotionWindow(schema, context, parent=mw)

    # show and focus the window - Automatically loads the active tab.
    _window.show()
    _window.raise_()
    _window.activateWindow()


def navigate_to_page(page_key: str, payload: dict[str, Any] | None = None) -> None:
    """Navigate the open Notion window to a page key and optionally pass payload."""
    if _window is None:
        return
    _window.navigate_to_page(page_key, payload=payload)


def _build_context() -> UiContext:
    """Build the shared context passed to page factories."""
    profile_folder = Path(mw.pm.profileFolder())
    db_path = profile_folder / "Noteck" / "db" / "notion_integration.db"

    return UiContext(mw=mw, profile_folder=profile_folder, db_path=db_path)


class NotionWindow(QDialog):
    """Dedicated add-on window with top navigation and content area."""

    def __init__(self, schema: UiSchema, context: UiContext, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._schema = schema
        self._context = context
        self._page_widgets: dict[str, QWidget] = {}
        self._placeholders: dict[str, QWidget] = {}
        self.setWindowTitle("Notion")
        self.setMinimumSize(520, 620)

        self._tabs = QTabWidget(self)
        self._tabs.currentChanged.connect(self._on_tab_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 11, 11, 11)
        layout.addWidget(self._tabs)

        # Bottom-right close button for users who prefer a visible "Close" action.
        button_row = QDialogButtonBox(self)

        # Add this first because Qt displays action-role buttons in reverse insertion
        # order on Windows; Refresh then appears to its left and Close to its right.
        self._release_notes_button = button_row.addButton(
            "Release Notes",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self._release_notes_button.setAutoDefault(False)
        self._release_notes_button.setDefault(False)
        self._release_notes_button.clicked.connect(
            lambda _checked=False: show_release_notes(
                self,
                content_mode="all",
                db_path=self._context.db_path,
            )
        )
        self._release_notes_button.hide()

        # Refresh button for pages that support reloading.
        self._refresh_button = button_row.addButton("Refresh", QDialogButtonBox.ButtonRole.ActionRole)
        self._refresh_button.setAutoDefault(False)
        self._refresh_button.setDefault(False)
        self._refresh_button.clicked.connect(self._refresh_current_page)
        self._refresh_button.hide()

        close_button = button_row.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        close_button.setAutoDefault(False)
        close_button.setDefault(False)
        close_button.clicked.connect(self.close)

        layout.addWidget(button_row)

        # Build tabs without triggering change signals during construction.
        self._tabs.blockSignals(True)
        self._build_tabs()
        self._tabs.blockSignals(False)

        # Defer loading of the initial tab until after the event loop ticks.
        QTimer.singleShot(0, self._load_initial_tab)

    def _load_initial_tab(self) -> None:
        """Load the current tab after the widget has been laid out."""
        self._on_tab_changed(self._tabs.currentIndex())

    def navigate_to_page(self, page_key: str, payload: dict[str, Any] | None = None) -> None:
        """Switch to a specific page key and optionally notify the target widget."""
        page_index = None
        for index, page in enumerate(self._schema.pages):
            if page.key == page_key:
                page_index = index
                break
        if page_index is None:
            return

        self._tabs.setCurrentIndex(page_index)
        self._on_tab_changed(page_index)
        widget = self._page_widgets.get(page_key)
        if widget is None:
            return

        on_navigation_payload = getattr(widget, "on_navigation_payload", None)
        if callable(on_navigation_payload):
            on_navigation_payload(payload or {})

    def showEvent(self, event: QShowEvent) -> None:
        """Handle window show events to ensure tabs have focus."""
        super().showEvent(event)
        self._tabs.setFocus()

    def _build_tabs(self) -> None:
        """Create a tab and placeholder slot for every page."""
        for page in self._schema.pages:
            placeholder = self._build_placeholder_widget("Loading...")
            self._placeholders[page.key] = placeholder
            self._tabs.addTab(placeholder, page.name)

    def _on_tab_changed(self, index: int) -> None:
        """Load the selected page on demand and show it."""
        if index < 0 or index >= len(self._schema.pages):
            return
        
        page = self._schema.pages[index]
        if page.key not in self._page_widgets:
            # Load the page widget.
            widget = self._load_page_widget(page)
            self._page_widgets[page.key] = widget
            placeholder = self._placeholders.pop(page.key, None)

            # Replace the placeholder widget with the real page widget.
            self._replace_tab(index, widget, page.name, placeholder)

        self._update_footer_button_visibility(page.key)

    def _refresh_current_page(self) -> None:
        """Call `reload` on the currently visible page widget."""
        widget = self._tabs.currentWidget()
        reload_action = getattr(widget, "reload", None)

        if not callable(reload_action):
            return

        self._refresh_button.setEnabled(False)
        try:
            reload_action()
        finally:
            self._refresh_button.setEnabled(True)
            self._tabs.setFocus()

    def _update_footer_button_visibility(self, page_key: str) -> None:
        """Show page-specific footer actions for the currently selected tab."""
        # check for reload capability
        widget = self._page_widgets.get(page_key)
        reload_action = getattr(widget, "reload", None)
        visible = bool(widget is not None and callable(reload_action))

        # update button visibility
        self._refresh_button.setVisible(visible)
        if visible:
            self._refresh_button.setEnabled(True)

        # Release notes are a settings-level action and should not appear while the
        # user works in Pages, Cards, or Image Occlusion.
        self._release_notes_button.setVisible(page_key == "settings")

    def _load_page_widget(self, page: UiPageDefinition) -> QWidget:
        """Import the page module and build its widget."""
        try:
            # Dynamically import the module and get the factory function.
            if page.module.startswith("."):
                module = importlib.import_module(page.module, package=__package__)
            else:
                module = importlib.import_module(page.module)
            factory = getattr(module, page.factory, None)
            if factory is None:
                raise UiSchemaError(
                    f"Page '{page.key}' missing factory '{page.factory}'."
                )
            
            # Parent the page widget to the tab widget so it renders inside the tab.
            widget = factory(self._tabs, self._context)
            if QWidget is None or not isinstance(widget, QWidget):
                raise UiSchemaError(
                    f"Page '{page.key}' factory did not return a QWidget."
                )
            return widget
        
        # pragma: no cover - visual fallback in Anki.
        except Exception as exc:
            return self._build_error_widget(
                f"Failed to load page '{page.name}'.\n{exc}"
            )

    def _replace_tab(
        self,
        index: int,
        widget: QWidget,
        label: str,
        placeholder: QWidget | None,
    ) -> None:
        """Swap the placeholder tab widget with the real page widget."""
        # delete placesholder and add new widget without flicker
        self._tabs.blockSignals(True)
        self._tabs.setUpdatesEnabled(False)
        self._tabs.removeTab(index)
        self._tabs.insertTab(index, widget, label)
        self._tabs.setCurrentIndex(index)
        self._tabs.setUpdatesEnabled(True)
        self._tabs.blockSignals(False)

        # update appearance
        self._tabs.update()
        self._tabs.tabBar().update()
        
        # delete the placeholder to free resources
        if placeholder is not None:
            placeholder.deleteLater()

    def _build_placeholder_widget(self, text: str) -> QWidget:
        """Create a basic placeholder label widget."""
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setMargin(16)
        return label

    def _build_error_widget(self, message: str) -> QWidget:
        """Create a visible error widget for failed page loads."""
        return self._build_placeholder_widget(message)
