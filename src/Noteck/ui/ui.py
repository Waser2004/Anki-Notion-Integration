"""UI shell for the Noteck add-on."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from typing import Any, Iterable

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

class UiSchemaError(RuntimeError):
    """Raised when ui.json cannot be loaded or validated."""


@dataclass(frozen=True)
class UiPageDefinition:
    """Metadata describing one top-level UI page."""

    key: str
    name: str
    module: str
    factory: str


class UiSchema:
    """Parsed UI schema with lookup helpers."""

    def __init__(self, pages: Iterable[UiPageDefinition]) -> None:
        self._pages = tuple(pages)
        self._pages_by_key = {page.key: page for page in self._pages}

    @property
    def pages(self) -> tuple[UiPageDefinition, ...]:
        """Return the ordered list of UI pages."""
        return self._pages

    def get_page(self, key: str) -> UiPageDefinition:
        """Return the page definition for the given key."""
        try:
            return self._pages_by_key[key]
        except KeyError as exc:
            raise UiSchemaError(f"Unknown page key: {key}") from exc


@dataclass(frozen=True)
class UiContext:
    """Shared context passed to page factories."""

    mw: Any
    profile_folder: Path
    db_path: Path


_DEFAULT_UI_PATH = Path(__file__).resolve().parents[1] / "docs" / "ui.json"
_DEFAULT_FACTORY = "build_page"
_initialized = False
_window: "NotionWindow | None" = None


def load_ui_schema(path: Path | None = None) -> UiSchema:
    """Load and validate the UI schema from ui.json."""
    schema_path = path or _DEFAULT_UI_PATH

    if not schema_path.exists():
        raise UiSchemaError(f"UI schema not found: {schema_path}")
    
    with schema_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    pages_payload = payload.get("pages")

    if not isinstance(pages_payload, list) or not pages_payload:
        raise UiSchemaError("UI schema must include a non-empty 'pages' list.")
    
    pages: list[UiPageDefinition] = []
    seen_keys: set[str] = set()
    for page_payload in pages_payload:
        if not isinstance(page_payload, dict):
            raise UiSchemaError("Each page entry must be an object.")
        
        key     = page_payload.get("key")
        name    = page_payload.get("name")
        module  = page_payload.get("module")
        factory = page_payload.get("factory", _DEFAULT_FACTORY)

        if not key or not name or not module:
            raise UiSchemaError("Each page requires 'key', 'name', and 'module'.")
        if not isinstance(factory, str) or not factory:
            raise UiSchemaError(f"Invalid factory for page '{key}'.")
        if key in seen_keys:
            raise UiSchemaError(f"Duplicate page key: {key}")
        
        seen_keys.add(key)
        pages.append(
            UiPageDefinition(
                key=str(key),
                name=str(name),
                module=str(module),
                factory=str(factory),
            )
        )
    
    return UiSchema(pages)


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

        self._update_refresh_button_visibility(page.key)

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

    def _update_refresh_button_visibility(self, page_key: str) -> None:
        """Show the footer refresh button only when the target page exposes `reload`."""
        # check for reload capability
        widget = self._page_widgets.get(page_key)
        reload_action = getattr(widget, "reload", None)
        visible = bool(widget is not None and callable(reload_action))

        # update button visibility
        self._refresh_button.setVisible(visible)
        if visible:
            self._refresh_button.setEnabled(True)

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
