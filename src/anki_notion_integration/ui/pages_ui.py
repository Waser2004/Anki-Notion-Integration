"""Pages tab UI for selecting Notion pages that should sync to Anki."""

from __future__ import annotations

import queue
import threading
from typing import Any

from aqt.qt import (
    QEvent,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QTimer,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_notion_integration.db import Database
from anki_notion_integration.notion_client import NotionApiError, NotionClient, NotionPage, NotionTransportError
from anki_notion_integration.pages import (
    PagesStore,
    apply_selection_rule,
    build_children_map_from_pages,
    build_deck_names_from_pages,
    get_descendant_ids,
)
from anki_notion_integration.ui.ui import UiContext


class PagesPage(QWidget):
    """Qt widget that renders and persists the hierarchical Notion page tree."""
    def __init__(self, parent: QWidget, context: UiContext) -> None:
        super().__init__(parent)
        self._context = context

        self._db = Database(context.db_path)
        self._store = PagesStore(self._db)
        self._children_map: dict[str, tuple[str, ...]] = {}
        self._deck_names_by_page_id: dict[str, str] = {}
        self._items_by_id: dict[str, QTreeWidgetItem] = {}
        self._pages_by_id: dict[str, NotionPage] = {}
        self._selected_ids: set[str] = set()
        self._cascade_selected_parent_ids: set[str] = set()
        self._page_count = 0
        self._upsert_batch_size = 25
        self._load_generation = 0
        self._is_loading = False
        self._fetch_queue: queue.Queue[tuple[str, int, Any]] = queue.Queue()
        self._suspend_item_events = False

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(11, 11, 11, 11)

        self._info_label = QLabel("Select Notion pages to convert to Anki decks.", self)
        self._info_label.setWordWrap(True)
        root_layout.addWidget(self._info_label)

        self._groupbox = QGroupBox("Loading Notion pages...", self)
        groupbox_layout = QVBoxLayout(self._groupbox)
        root_layout.addWidget(self._groupbox)

        # Error details stay inside the group box and are only shown on failure.
        self._error_label = QLabel("", self._groupbox)
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        groupbox_layout.addWidget(self._error_label)

        self._tree = QTreeWidget(self)
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(self._selection_mode_no_selection())
        self._tree.setFocusPolicy(self._focus_policy_no_focus())
        self._tree.setStyleSheet("""
            QTreeWidget {
                background-color: transparent;
                border: none;
                selection-background-color: transparent;
            }
            QTreeWidget::item {
                margin-top: 5px;
                margin-bottom: 5px;
            }
        """)
        self._tree.viewport().installEventFilter(self)
        self._tree.itemChanged.connect(self._on_item_changed)
        groupbox_layout.addWidget(self._tree, 1)

        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(40)
        self._fetch_timer.timeout.connect(self._drain_fetch_queue)

        self.reload()

    def reload(self) -> None:
        """Load pages progressively and update the tree while data is being fetched."""
        self._load_generation += 1
        generation = self._load_generation
        self._is_loading = True
        self._page_count = 0
        self._suspend_item_events = True

        # Clear existing state.
        try:
            self._tree.clear()
            self._items_by_id.clear()
            self._pages_by_id.clear()
            self._children_map = {}
            self._deck_names_by_page_id = {}
            self._selected_ids = self._store.get_selected_page_ids()
            # Cascade tracking is session-local user intent. Persisted DB state
            # should be restored exactly and must not auto-select descendants.
            self._cascade_selected_parent_ids = set()
            self._groupbox.setTitle("Loading Notion pages...")
            self._error_label.clear()
            self._error_label.hide()
        finally:
            self._suspend_item_events = False
        
        # Start background fetch.
        self._fetch_timer.start()
        worker = threading.Thread(
            target=self._fetch_pages_worker,
            args=(generation,),
            daemon=True,
        )
        worker.start()

    def _fetch_pages_worker(self, generation: int) -> None:
        """Fetch pages in a worker thread and push events to the UI queue."""
        profile_name = self._resolve_profile_name(self._context)
        try:
            client = NotionClient.from_settings(self._db, profile_name=profile_name)
            for page in client.iter_pages():
                self._fetch_queue.put(("page", generation, page))
            self._fetch_queue.put(("done", generation, None))
        except NotionApiError as exc:
            self._fetch_queue.put(("error", generation, str(exc)))
        except NotionTransportError as exc:
            self._fetch_queue.put(("error", generation, str(exc)))
        except Exception as exc:
            self._fetch_queue.put(("error", generation, str(exc)))

    def _drain_fetch_queue(self) -> None:
        """Drain queued worker events and apply them on the Qt thread."""
        processed = 0
        while processed < 200:
            try:
                event_type, generation, payload = self._fetch_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if generation != self._load_generation:
                continue
            if event_type == "page":
                self._handle_loaded_page(payload)
                continue
            if event_type == "error":
                self._finish_reload(error_message=str(payload))
                return
            if event_type == "done":
                self._finish_reload(error_message=None)
                return

    def _handle_loaded_page(self, page: NotionPage) -> None:
        """Create/update one page item and refresh derived tree metadata."""
        previous_suspend_state = self._suspend_item_events
        self._suspend_item_events = True
        try:
            # Prevent transient unchecked item states from triggering `itemChanged`
            # while the tree is still being rebuilt during incremental loading.
            self._pages_by_id[page.page_id] = page
            self._children_map = build_children_map_from_pages(self._pages_by_id)
            self._deck_names_by_page_id = build_deck_names_from_pages(self._pages_by_id)
            self._sync_tree_items()

            # Re-apply selection state with cascade rules.
            self._selected_ids = self._apply_cascade_selection(self._selected_ids)
            self._apply_selected_ids(self._selected_ids)
        finally:
            self._suspend_item_events = previous_suspend_state

        # Update progress label.
        self._page_count += 1
        self._groupbox.setTitle(f"Loaded {self._page_count} pages...")
        if self._page_count % self._upsert_batch_size == 0:
            self._persist_selection_state()

    def _sync_tree_items(self) -> None:
        """Ensure all known pages have tree items with correct parent and metadata."""
        for page_id, page in self._pages_by_id.items():
            item = self._items_by_id.get(page_id)

            # Create item if missing.
            if item is None:
                item = QTreeWidgetItem(self._tree)
                item.setData(0, self._item_role_user(), page_id)
                item.setFlags(item.flags() | self._item_flag_user_checkable())
                self._items_by_id[page_id] = item
            
            # Update item metadata.
            item.setText(0, page.title or "Untitled")
            item.setToolTip(0, self._deck_names_by_page_id.get(page_id, "Notion::Untitled"))
            self._reparent_item(page_id)

    def _reparent_item(self, page_id: str) -> None:
        """Move one item to the correct parent if parent information changed."""
        item = self._items_by_id.get(page_id)
        page = self._pages_by_id.get(page_id)
        if item is None or page is None:
            return

        # Determine target parent.
        target_parent_id = page.parent_id if page.parent_type == "page_id" else None
        target_parent    = self._items_by_id.get(target_parent_id) if target_parent_id else None
        current_parent   = item.parent()

        # item is root page (is already top-lavel)
        if current_parent is target_parent:
            if current_parent is None and self._tree.indexOfTopLevelItem(item) < 0:
                self._tree.addTopLevelItem(item)
            return

        # Detach from current parent.
        if current_parent is None:
            top_level_index = self._tree.indexOfTopLevelItem(item)
            if top_level_index >= 0:
                self._tree.takeTopLevelItem(top_level_index)
        
        # Detach from current parent's children.
        else:
            child_index = current_parent.indexOfChild(item)
            if child_index >= 0:
                current_parent.takeChild(child_index)

        # Attach to target parent.
        if target_parent is None:
            self._tree.addTopLevelItem(item)
            return
        target_parent.addChild(item)

    def _finish_reload(self, error_message: str | None) -> None:
        """Finalize one reload cycle and persist the currently known selection state."""
        if not self._is_loading:
            return
        
        self._is_loading = False
        self._fetch_timer.stop()

        # Final UI updates.
        if error_message:
            self._groupbox.setTitle("Failed to load pages.")
            self._error_label.setText(error_message)
            self._error_label.show()
        else:
            self._groupbox.setTitle(f"Loaded {self._page_count} pages.")
            self._error_label.clear()
            self._error_label.hide()
            self._tree.expandToDepth(0)
        
        self._persist_selection_state()

    def _persist_selection_state(self) -> None:
        """Persist known pages and currently selected ids to the pages table."""
        if not self._deck_names_by_page_id:
            return
        
        selected_known_ids = {
            page_id
            for page_id in self._selected_ids
            if page_id in self._deck_names_by_page_id
        }

        self._store.upsert_page_selection(self._deck_names_by_page_id, selected_known_ids)

    def _apply_cascade_selection(self, selected_ids: set[str]) -> set[str]:
        """Ensure descendants of cascade-selected parents are selected when they appear."""
        updated = set(selected_ids)

        for page_id in self._cascade_selected_parent_ids:
            if page_id not in updated:
                continue

            updated.update(get_descendant_ids(page_id, self._children_map))

        return updated

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """Toggle checkboxes when users click a tree row, and consume the click event."""
        if watched is self._tree.viewport() and event is not None:
            if event.type() == self._mouse_button_release_event_type():
                if self._mouse_button(event) == self._mouse_button_left():
                    if self._toggle_item_from_click(event.pos()):
                        return True
        
        return super().eventFilter(watched, event)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Apply selection rules on user toggle and persist the resulting selection."""
        if self._suspend_item_events or column != 0:
            return

        page_id = item.data(0, self._item_role_user())
        if not page_id:
            return

        checked = item.checkState(0) == self._check_state_checked()
        page_id = str(page_id)
        selected_ids = self._collect_selected_ids()
        descendants = get_descendant_ids(page_id, self._children_map)
        has_selected_descendant = any(descendant in selected_ids for descendant in descendants)
        ancestor_ids = self._get_ancestor_ids(page_id)

        # Manage cascade-selected parents.
        if checked and not has_selected_descendant:
            self._cascade_selected_parent_ids.add(page_id)
        elif not checked:
            self._cascade_selected_parent_ids.discard(page_id)
            # Manual deselection inside a subtree should stop parent auto-cascade.
            self._cascade_selected_parent_ids.difference_update(ancestor_ids)
        
        updated_selected_ids = apply_selection_rule(
            page_id,
            checked      = checked,
            selected_ids = selected_ids,
            children_map = self._children_map,
        )
        updated_selected_ids = self._apply_cascade_selection(updated_selected_ids)
        self._selected_ids = set(updated_selected_ids)

        if updated_selected_ids != selected_ids:
            self._apply_selected_ids(updated_selected_ids)

        self._persist_selection_state()

    def _get_ancestor_ids(self, page_id: str) -> set[str]:
        """Return ancestor page ids for one page, guarding against cycles."""
        ancestors: set[str] = set()
        current_id = page_id

        while True:
            page = self._pages_by_id.get(current_id)
            if page is None or page.parent_type != "page_id" or not page.parent_id:
                break

            parent_id = page.parent_id
            if parent_id in ancestors:
                break

            ancestors.add(parent_id)
            current_id = parent_id

        return ancestors

    def _collect_selected_ids(self) -> set[str]:
        """Collect currently checked pages from the tree widget."""
        return {
            page_id
            for page_id, item in self._items_by_id.items()
            if item.checkState(0) == self._check_state_checked()
        }

    def _apply_selected_ids(self, selected_ids: set[str]) -> None:
        """Apply a selected-id set to all tree items without recursive signal loops."""
        previous_suspend_state = self._suspend_item_events
        self._suspend_item_events = True
        try:
            for page_id, item in self._items_by_id.items():
                item.setCheckState(
                    0,
                    self._check_state_checked() if page_id in selected_ids else self._check_state_unchecked(),
                )
        finally:
            self._suspend_item_events = previous_suspend_state

    def _toggle_item_from_click(self, click_pos: Any) -> bool:
        """Toggle the row's checkbox for valid clicks and return whether the event was handled."""
        if self._suspend_item_events:
            return False

        item = self._tree.itemAt(click_pos)
        if item is None:
            return False

        # Skip branch-indicator clicks so expand/collapse behavior stays intact.
        if not self._tree.visualItemRect(item).contains(click_pos):
            return False
        if self._tree.columnAt(click_pos.x()) != 0:
            return False

        item.setCheckState(
            0,
            self._check_state_unchecked()
            if item.checkState(0) == self._check_state_checked()
            else self._check_state_checked(),
        )
        self._clear_tree_current()
        return True

    def _clear_tree_current(self) -> None:
        """Clear the current item index to avoid focus/selection highlight artifacts."""
        self._tree.setCurrentIndex(self._empty_model_index())

    def closeEvent(self, event: Any) -> None:
        """Invalidate pending background results when the widget closes."""
        self._load_generation += 1
        self._is_loading = False
        self._fetch_timer.stop()
        super().closeEvent(event)

    @staticmethod
    def _resolve_profile_name(context: UiContext) -> str | None:
        """Try to determine the active profile name for keyring namespacing."""
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
    def _item_role_user() -> int:
        """Return the Qt user-data role in a Qt-version-safe way."""
        from aqt.qt import Qt

        item_data_role = getattr(Qt, "ItemDataRole", None)
        if item_data_role is not None and hasattr(item_data_role, "UserRole"):
            return int(getattr(item_data_role, "UserRole"))
        return int(getattr(Qt, "UserRole"))

    @staticmethod
    def _selection_mode_no_selection() -> Any:
        """Return the Qt selection mode that disables row selection."""
        from aqt.qt import QAbstractItemView

        selection_mode = getattr(QAbstractItemView, "SelectionMode", None)
        if selection_mode is not None and hasattr(selection_mode, "NoSelection"):
            return getattr(selection_mode, "NoSelection")
        return getattr(QAbstractItemView, "NoSelection")

    @staticmethod
    def _mouse_button_release_event_type() -> Any:
        """Return the mouse-button-release event type value."""
        event_type = getattr(QEvent, "Type", None)
        if event_type is not None and hasattr(event_type, "MouseButtonRelease"):
            return getattr(event_type, "MouseButtonRelease")
        return getattr(QEvent, "MouseButtonRelease")

    @staticmethod
    def _mouse_button(event: Any) -> Any:
        """Return the mouse button value from a Qt mouse event."""
        return event.button()

    @staticmethod
    def _mouse_button_left() -> Any:
        """Return the Qt left-mouse-button enum value."""
        from aqt.qt import Qt

        mouse_button = getattr(Qt, "MouseButton", None)
        if mouse_button is not None and hasattr(mouse_button, "LeftButton"):
            return getattr(mouse_button, "LeftButton")
        return getattr(Qt, "LeftButton")

    @staticmethod
    def _focus_policy_no_focus() -> Any:
        """Return the Qt focus policy that disables focus on the widget."""
        from aqt.qt import Qt

        focus_policy = getattr(Qt, "FocusPolicy", None)
        if focus_policy is not None and hasattr(focus_policy, "NoFocus"):
            return getattr(focus_policy, "NoFocus")
        return getattr(Qt, "NoFocus")

    @staticmethod
    def _empty_model_index() -> Any:
        """Return an empty Qt model index for clearing the current item."""
        from aqt.qt import QModelIndex

        return QModelIndex()

    @staticmethod
    def _item_flag_user_checkable() -> Any:
        """Return the Qt flag used to make tree items checkable."""
        from aqt.qt import Qt

        item_flag = getattr(Qt, "ItemFlag", None)
        if item_flag is not None and hasattr(item_flag, "ItemIsUserCheckable"):
            return getattr(item_flag, "ItemIsUserCheckable")
        return getattr(Qt, "ItemIsUserCheckable")

    @staticmethod
    def _check_state_checked() -> Any:
        """Return the Qt check-state constant for checked items."""
        from aqt.qt import Qt

        check_state = getattr(Qt, "CheckState", None)
        if check_state is not None and hasattr(check_state, "Checked"):
            return getattr(check_state, "Checked")
        return getattr(Qt, "Checked")

    @staticmethod
    def _check_state_unchecked() -> Any:
        """Return the Qt check-state constant for unchecked items."""
        from aqt.qt import Qt

        check_state = getattr(Qt, "CheckState", None)
        if check_state is not None and hasattr(check_state, "Unchecked"):
            return getattr(check_state, "Unchecked")
        return getattr(Qt, "Unchecked")


def build_page(parent: QWidget, context: UiContext) -> QWidget:
    """Factory used by the UI shell to build the Pages tab widget."""
    return PagesPage(parent=parent, context=context)
