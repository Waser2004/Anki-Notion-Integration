"""Pages tab UI for selecting Notion pages that should sync to Anki."""

from __future__ import annotations

from pathlib import Path
import queue
import threading
from typing import Any

from aqt.qt import (
    QComboBox,
    QCursor,
    QEvent,
    QGroupBox,
    QHeaderView,
    QMenu,
    QIcon,
    QLabel,
    QPushButton,
    QSize,
    QSizePolicy,
    QTimer,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..modules.card_types import (
    DEFAULT_SELECTABLE_CARD_TYPES,
    card_type_label,
    normalize_default_selectable_card_type,
)
from ..modules.db import Database
from ..modules.notion_client import NotionApiError, NotionClient, NotionPage, NotionTransportError
from ..modules.pages import (
    PagesStore,
    StoredPage,
    apply_default_card_type_rule,
    apply_selection_rule,
    build_children_map_from_pages,
    build_deck_names_from_pages,
    get_descendant_ids,
)
from .ui import navigate_to_page
from .ui import UiContext
from .context_menu_schema import ContextMenuEntry, load_context_menu_schema


class PagesPage(QWidget):
    """Qt widget that renders and persists the hierarchical Notion page tree."""
    def __init__(self, parent: QWidget, context: UiContext) -> None:
        super().__init__(parent)
        self._context = context

        self._db = Database(context.db_path)
        self._store = PagesStore(self._db)
        self._children_map: dict[str, tuple[str, ...]] = {}
        self._deck_names_by_page_id: dict[str, str] = {}
        self._page_default_card_types: dict[str, str | None] = {}
        self._ordered_child_ids_by_parent: dict[str, tuple[str, ...]] = {}
        self._items_by_id: dict[str, QTreeWidgetItem] = {}
        self._pages_by_id: dict[str, NotionPage] = {}
        self._selected_ids: set[str] = set()
        self._cascade_selected_parent_ids: set[str] = set()
        self._cascade_card_type_parent_types: dict[str, str | None] = {}
        self._page_count = 0
        self._upsert_batch_size = 25
        self._load_generation = 0
        self._is_loading = False
        self._fetch_queue: queue.Queue[tuple[str, int, Any]] = queue.Queue()
        self._suspend_item_events = False
        self._received_page_ids: set[str] = set()
        self._expanded_page_ids: set[str] = set()
        self._has_expansion_snapshot = False
        self._hovered_page_id: str | None = None
        self._context_menu_schema = load_context_menu_schema().pages

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(11, 11, 11, 11)

        self._groupbox = QGroupBox("Loading Notion pages...", self)
        groupbox_layout = QVBoxLayout(self._groupbox)
        root_layout.addWidget(self._groupbox)

        # Error details stay inside the group box and are only shown on failure.
        self._error_label = QLabel("", self._groupbox)
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        groupbox_layout.addWidget(self._error_label)

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(4)
        self._tree.setHeaderHidden(True)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setMouseTracking(True)
        self._tree.viewport().setMouseTracking(True)
        self._tree.setSelectionMode(self._selection_mode_no_selection())
        self._tree.setFocusPolicy(self._focus_policy_no_focus())
        self._tree.setStyleSheet("""
            QTreeWidget {
                background-color: transparent;
                border: none;
                selection-background-color: transparent;
            }
            QTreeWidget::item {
                height: 20px;
                margin-top: 2px;
                margin-bottom: 3px;
            }
        """)
        self._tree.installEventFilter(self)
        self._tree.viewport().installEventFilter(self)
        self._tree.setContextMenuPolicy(self._context_menu_policy_custom())
        self._tree.viewport().setContextMenuPolicy(self._context_menu_policy_custom())
        self._tree.customContextMenuRequested.connect(
            lambda pos: self._show_tree_context_menu(pos, from_viewport=False)
        )
        self._tree.viewport().customContextMenuRequested.connect(
            lambda pos: self._show_tree_context_menu(pos, from_viewport=True)
        )
        self._tree.itemChanged.connect(self._on_item_changed)
        groupbox_layout.addWidget(self._tree, 1)

        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(40)
        self._fetch_timer.timeout.connect(self._drain_fetch_queue)
        # Cache action icons and refresh them when palette/theme changes.
        self._image_button_icon = QIcon()
        self._cards_button_icon = QIcon()
        self._reload_action_icons()

        self.reload()

    def reload(self) -> None:
        """Load pages progressively and update the tree while data is being fetched."""
        self._load_generation += 1
        generation = self._load_generation
        self._is_loading = True
        self._page_count = 0
        self._received_page_ids = set()
        self._expanded_page_ids = self._collect_expanded_ids()
        # Track whether this reload should preserve expansion state, including
        # the case where the user intentionally collapsed everything.
        self._has_expansion_snapshot = bool(self._items_by_id)
        self._suspend_item_events = True

        # Reset state and render cached pages from the DB before refreshing.
        try:
            self._tree.clear()
            self._items_by_id.clear()
            self._pages_by_id.clear()
            self._hovered_page_id = None
            self._children_map = {}
            self._deck_names_by_page_id = {}
            self._page_default_card_types = {}
            self._ordered_child_ids_by_parent = {}
            self._selected_ids = self._store.get_selected_page_ids()
            # Cascade tracking is session-local user intent. Persisted DB state
            # should be restored exactly and must not auto-select descendants.
            self._cascade_selected_parent_ids = set()
            self._cascade_card_type_parent_types = {}
            self._error_label.clear()
            self._error_label.hide()
            self._preload_cached_pages()
        finally:
            self._suspend_item_events = False

        cached_count = len(self._pages_by_id)
        if cached_count > 0:
            self._groupbox.setTitle(f"Refreshing pages...")
        else:
            self._groupbox.setTitle("Loading Notion pages...")
        
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
            pages_by_id: dict[str, NotionPage] = {}
            for page in client.iter_pages():
                pages_by_id[page.page_id] = page
                self._fetch_queue.put(("page", generation, page))

            ordered_child_ids_by_parent: dict[str, tuple[str, ...]] = {}
            try:
                ordered_child_ids_by_parent = client.build_child_page_order_map(pages_by_id)
            except (NotionApiError, NotionTransportError):
                # Sibling order is best-effort metadata. Keep loading even if
                # order lookups fail so page selection remains usable.
                ordered_child_ids_by_parent = {}
            self._fetch_queue.put(("order_map", generation, ordered_child_ids_by_parent))
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
            if event_type == "order_map":
                self._handle_loaded_order_map(payload)
                continue
            if event_type == "error":
                self._finish_reload(error_message=str(payload))
                return
            if event_type == "done":
                self._finish_reload(error_message=None)
                return

    def _handle_loaded_order_map(self, ordered_child_ids_by_parent: dict[str, tuple[str, ...]]) -> None:
        """Apply finalized sibling-order hints gathered from Notion block ordering."""
        self._ordered_child_ids_by_parent = dict(ordered_child_ids_by_parent)
        self._sync_tree_items()

    def _handle_loaded_page(self, page: NotionPage) -> None:
        """Create/update one page item and refresh derived tree metadata."""
        previous_suspend_state = self._suspend_item_events
        self._suspend_item_events = True
        try:
            # Prevent transient unchecked item states from triggering `itemChanged`
            # while the tree is still being rebuilt during incremental loading.
            self._pages_by_id[page.page_id] = page
            self._received_page_ids.add(page.page_id)
            self._children_map = build_children_map_from_pages(self._pages_by_id)
            self._deck_names_by_page_id = build_deck_names_from_pages(self._pages_by_id)
            self._sync_tree_items()
            self._apply_cascade_card_type_to_page(page.page_id)

            # Re-apply selection state with cascade rules.
            self._selected_ids = self._apply_cascade_selection(self._selected_ids)
            self._apply_selected_ids(self._selected_ids)
        finally:
            self._suspend_item_events = previous_suspend_state

        # Update progress label.
        self._page_count += 1
        self._groupbox.setTitle(f"Refreshing pages... loaded {self._page_count}")
        if self._page_count % self._upsert_batch_size == 0:
            self._persist_selection_state()

    def _sync_tree_items(self) -> None:
        """Ensure all known pages have tree items with correct parent and metadata."""
        stale_item_ids = [page_id for page_id in self._items_by_id if page_id not in self._pages_by_id]
        for page_id in stale_item_ids:
            self._remove_tree_item(page_id)

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

        self._reorder_tree_items()
        self._apply_expanded_ids()
        self._sync_hovered_row_actions()

    def _attach_item_actions(self, item: QTreeWidgetItem, page_id: str) -> None:
        """Attach row actions for image/cards navigation and page card-type selection."""
        image_button = self._tree.itemWidget(item, 1)
        if not isinstance(image_button, QPushButton):
            image_button = QPushButton(self._tree)
            image_button.clicked.connect(
                lambda _checked=False, pid=page_id: self._open_image_occlusion_page(pid)
            )
            self._tree.setItemWidget(item, 1, image_button)
        self._configure_action_button(image_button, self._image_button_icon)
        image_button.setToolTip("Open Image Occlusion page for this Notion page.")

        cards_button = self._tree.itemWidget(item, 2)
        if not isinstance(cards_button, QPushButton):
            cards_button = QPushButton(self._tree)
            cards_button.clicked.connect(
                lambda _checked=False, pid=page_id: self._open_cards_page(pid)
            )
            self._tree.setItemWidget(item, 2, cards_button)
        self._configure_action_button(cards_button, self._cards_button_icon)
        cards_button.setToolTip("Open Cards page for this Notion page.")

        type_combo = self._tree.itemWidget(item, 3)
        if not isinstance(type_combo, QComboBox):
            type_combo = QComboBox(self._tree)
            type_combo.addItem("Default", None)
            for card_type in DEFAULT_SELECTABLE_CARD_TYPES:
                type_combo.addItem(card_type_label(card_type,  abbreviation=True), card_type)
            type_combo.currentIndexChanged.connect(
                lambda _index, pid=page_id, combo=type_combo: self._on_card_type_selected(pid, combo)
            )
            self._tree.setItemWidget(item, 3, type_combo)
        self._configure_card_type_combo(type_combo)

        default_card_type = self._page_default_card_types.get(page_id)
        self._set_card_type_combo_value(type_combo, default_card_type)
        if default_card_type:
            type_combo.setToolTip(f"Page card type override: {card_type_label(default_card_type,  abbreviation=False)}")
        else:
            type_combo.setToolTip("Use global card type default for this page.")

    def _detach_item_actions(self, item: QTreeWidgetItem) -> None:
        """Remove row action widgets so only hovered rows render controls."""
        image_button = self._tree.itemWidget(item, 1)
        if isinstance(image_button, QPushButton):
            self._tree.removeItemWidget(item, 1)
            image_button.hide()
            image_button.deleteLater()

        cards_button = self._tree.itemWidget(item, 2)
        if isinstance(cards_button, QPushButton):
            self._tree.removeItemWidget(item, 2)
            cards_button.hide()
            cards_button.deleteLater()

        type_combo = self._tree.itemWidget(item, 3)
        if isinstance(type_combo, QComboBox):
            self._tree.removeItemWidget(item, 3)
            type_combo.hide()
            type_combo.deleteLater()

    def _sync_hovered_row_actions(self) -> None:
        """Ensure only the currently hovered and selected row has action widgets."""
        if self._hovered_page_id is None:
            return

        item = self._items_by_id.get(self._hovered_page_id)
        if item is None:
            self._hovered_page_id = None
            return

        if self._is_page_selected(self._hovered_page_id):
            self._attach_item_actions(item, self._hovered_page_id)
            return

        self._detach_item_actions(item)

    def _is_page_selected(self, page_id: str) -> bool:
        """Return whether the page is currently selected in the tree."""
        item = self._items_by_id.get(page_id)
        if item is None:
            return False
        return item.checkState(0) == self._check_state_checked()

    def _configure_action_button(self, button: QPushButton, icon: QIcon) -> None:
        """Apply compact 20x20 icon-button styling for one tree action control."""
        # Force icon-only controls so row action columns remain fixed and minimal.
        button.setText("")
        button.setIcon(icon)
        button.setFixedSize(20, 20)
        button.setIconSize(QSize(14, 14))
        button.setStyleSheet("padding: 0px;")

    def _configure_card_type_combo(self, combo: QComboBox) -> None:
        """Apply compact sizing so per-row combo boxes only use their needed width."""
        combo.setSizePolicy(self._size_policy_fixed(), self._size_policy_fixed())
        combo.setSizeAdjustPolicy(self._combo_adjust_to_contents_policy())
        combo.setMinimumContentsLength(0)
        combo.setStyleSheet(
            "QComboBox { padding: 0px 4px; }"
            "QComboBox:on { padding: 0px; }"
        )
        self._configure_card_type_combo_popup(combo)

    def _configure_card_type_combo_popup(self, combo: QComboBox) -> None:
        """Ensure the expanded popup is wide enough for full card-type labels."""
        from aqt.qt import Qt

        popup_width = self._card_type_combo_popup_width(combo)
        popup_view = combo.view()
        popup_view.setMinimumWidth(popup_width)
        text_elide_mode = getattr(Qt, "TextElideMode", None)
        if text_elide_mode is not None and hasattr(text_elide_mode, "ElideNone"):
            popup_view.setTextElideMode(getattr(text_elide_mode, "ElideNone"))
        else:
            popup_view.setTextElideMode(getattr(Qt, "ElideNone"))

    def _card_type_combo_popup_width(self, combo: QComboBox) -> int:
        """Return popup width needed to show the longest combo entry without clipping."""
        font_metrics = combo.fontMetrics()
        longest_label_width = 0
        for index in range(combo.count()):
            label_width = int(font_metrics.horizontalAdvance(combo.itemText(index)))
            if label_width > longest_label_width:
                longest_label_width = label_width

        # Add room for checkmark, popup padding, and a potential scrollbar.
        popup_padding_width = 52
        collapsed_combo_width = int(combo.sizeHint().width())
        return max(collapsed_combo_width, longest_label_width + popup_padding_width)

    def _set_card_type_combo_value(self, combo: QComboBox, card_type: str | None) -> None:
        """Set one combo to the stored card type without re-triggering persistence."""
        previous_block_state = combo.blockSignals(True)
        try:
            selected_index = 0
            if card_type:
                for index in range(combo.count()):
                    if combo.itemData(index) == card_type:
                        selected_index = index
                        break
            combo.setCurrentIndex(selected_index)
        finally:
            combo.blockSignals(previous_block_state)

    def _on_card_type_selected(self, page_id: str, combo: QComboBox) -> None:
        """Persist one page's card-type override and cascade it to descendants."""
        selected = combo.currentData()
        selected_type = None if selected is None else normalize_default_selectable_card_type(str(selected))
        updated_card_types = apply_default_card_type_rule(
            page_id,
            selected_type,
            page_default_card_types=self._page_default_card_types,
            children_map=self._children_map,
        )
        affected_page_ids = {
            affected_page_id
            for affected_page_id, updated_card_type in updated_card_types.items()
            if self._page_default_card_types.get(affected_page_id) != updated_card_type
        }
        if not affected_page_ids:
            return

        # Ensure rows exist for known pages before applying per-page overrides.
        self._persist_selection_state()
        self._store.set_page_default_card_types(affected_page_ids, selected_type)
        for affected_page_id in affected_page_ids:
            self._page_default_card_types[affected_page_id] = selected_type

        # Latest direct user action should define cascade intent for this subtree.
        descendant_ids = get_descendant_ids(page_id, self._children_map)
        for descendant_id in descendant_ids:
            self._cascade_card_type_parent_types.pop(descendant_id, None)
        self._cascade_card_type_parent_types[page_id] = selected_type

        if selected_type:
            combo.setToolTip(f"Page card type override: {card_type_label(selected_type, abbreviation=False)}")
        else:
            combo.setToolTip("Use global card type default for this page.")

    def _reload_action_icons(self) -> None:
        """Load light/dark button icons that match the current application palette."""
        variant = "dark" if self._is_dark_palette() else "light"
        self._image_button_icon = QIcon(self._docs_icon_path(f"image_{variant}.svg"))
        self._cards_button_icon = QIcon(self._docs_icon_path(f"card_{variant}.svg"))

    def _refresh_action_icons_in_tree(self) -> None:
        """Re-apply action icons for the currently rendered hover-row actions."""
        self._sync_hovered_row_actions()

    def _open_image_occlusion_page(self, page_id: str) -> None:
        """Open the Image Occlusion tab and preselect the clicked page."""
        # Ensure the target page is persisted as sync-enabled before tab navigation.
        self._persist_selection_state()
        navigate_to_page("image_occlusion", {"page_id": page_id})

    def _open_cards_page(self, page_id: str) -> None:
        """Open the Cards tab and preselect the clicked page."""
        # Ensure the target page is persisted as sync-enabled before tab navigation.
        self._persist_selection_state()
        navigate_to_page("cards", {"page_id": page_id})

    def _remove_tree_item(self, page_id: str) -> None:
        """Detach and remove one tree item by page id."""
        item = self._items_by_id.pop(page_id, None)
        if item is None:
            return

        self._detach_item_actions(item)
        if self._hovered_page_id == page_id:
            self._hovered_page_id = None

        parent = item.parent()
        if parent is None:
            top_level_index = self._tree.indexOfTopLevelItem(item)
            if top_level_index >= 0:
                self._tree.takeTopLevelItem(top_level_index)
        else:
            child_index = parent.indexOfChild(item)
            if child_index >= 0:
                parent.takeChild(child_index)

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

    def _reorder_tree_items(self) -> None:
        """Reorder roots and siblings to match the current Notion page sequence."""
        root_page_ids = self._ordered_root_page_ids()
        self._reorder_child_items(parent_item=None, ordered_page_ids=root_page_ids)

        for parent_page_id in self._pages_by_id:
            parent_item = self._items_by_id.get(parent_page_id)
            if parent_item is None:
                continue
            child_page_ids = self._ordered_child_page_ids(parent_page_id)
            self._reorder_child_items(parent_item=parent_item, ordered_page_ids=child_page_ids)

    def _ordered_root_page_ids(self) -> list[str]:
        """Return root page ids in the current fetch order from Notion."""
        root_page_ids: list[str] = []
        for page_id, page in self._pages_by_id.items():
            parent_id = page.parent_id if page.parent_type == "page_id" else None
            if parent_id and parent_id in self._pages_by_id:
                continue
            root_page_ids.append(page_id)
        return root_page_ids

    def _ordered_child_page_ids(self, parent_page_id: str) -> list[str]:
        """Return one parent's child ids in preferred order with stable fallback."""
        current_child_page_ids = list(self._children_map.get(parent_page_id, ()))
        if not current_child_page_ids:
            return []

        ordered_child_page_ids = self._ordered_child_ids_by_parent.get(parent_page_id)
        if not ordered_child_page_ids:
            return current_child_page_ids

        known_child_page_ids = set(current_child_page_ids)
        ordered_known_child_page_ids = [
            child_page_id
            for child_page_id in ordered_child_page_ids
            if child_page_id in known_child_page_ids
        ]

        ordered_known_child_page_id_set = set(ordered_known_child_page_ids)
        merged_order = list(ordered_known_child_page_ids)
        for child_page_id in current_child_page_ids:
            if child_page_id in ordered_known_child_page_id_set:
                continue
            merged_order.append(child_page_id)

        return merged_order

    def _reorder_child_items(self, parent_item: QTreeWidgetItem | None, ordered_page_ids: list[str]) -> None:
        """Move child items so visible row order follows `ordered_page_ids`."""
        if parent_item is None:
            child_count = self._tree.topLevelItemCount()
            get_child = self._tree.topLevelItem
            index_of_child = self._tree.indexOfTopLevelItem
            take_child = self._tree.takeTopLevelItem
            insert_child = self._tree.insertTopLevelItem
        else:
            child_count = parent_item.childCount()
            get_child = parent_item.child
            index_of_child = parent_item.indexOfChild
            take_child = parent_item.takeChild
            insert_child = parent_item.insertChild

        if child_count <= 1:
            return

        child_items_by_id: dict[str, QTreeWidgetItem] = {}
        for index in range(child_count):
            child_item = get_child(index)
            child_page_id = child_item.data(0, self._item_role_user())
            if child_page_id:
                child_items_by_id[str(child_page_id)] = child_item

        desired_page_ids = [
            page_id
            for page_id in ordered_page_ids
            if page_id in child_items_by_id
        ]
        if len(desired_page_ids) <= 1:
            return

        current_page_ids: list[str] = []
        for index in range(child_count):
            child_item = get_child(index)
            child_page_id = child_item.data(0, self._item_role_user())
            if child_page_id and str(child_page_id) in child_items_by_id:
                current_page_ids.append(str(child_page_id))
        if current_page_ids == desired_page_ids:
            return

        # Move in reverse and insert at position 0 to preserve desired ordering.
        for page_id in reversed(desired_page_ids):
            child_item = child_items_by_id[page_id]
            current_index = index_of_child(child_item)
            if current_index < 0:
                continue
            take_child(current_index)
            insert_child(0, child_item)

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
            self._remove_stale_pages_after_successful_refresh()
            self._groupbox.setTitle(f"Loaded {self._page_count} pages.")
            self._error_label.clear()
            self._error_label.hide()
            if not self._has_expansion_snapshot:
                self._tree.expandToDepth(0)
            else:
                self._apply_expanded_ids()
        
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

        self._store.upsert_page_selection(
            self._deck_names_by_page_id,
            selected_known_ids,
            pages_by_id=self._pages_by_id,
        )

    def _preload_cached_pages(self) -> None:
        """Render currently stored pages from the DB before the live refresh starts."""
        stored_pages = self._store.get_pages()
        if not stored_pages:
            return

        self._deck_names_by_page_id = {
            page_id: page.anki_deck_name
            for page_id, page in stored_pages.items()
        }
        self._page_default_card_types = {
            page_id: page.default_card_type
            for page_id, page in stored_pages.items()
        }
        self._pages_by_id = {
            page_id: self._page_from_stored_row(page_id, stored_page)
            for page_id, stored_page in stored_pages.items()
        }
        self._children_map = build_children_map_from_pages(self._pages_by_id)
        self._sync_tree_items()
        self._apply_selected_ids(self._selected_ids)

    def _remove_stale_pages_after_successful_refresh(self) -> None:
        """Drop cached-only pages that were not returned by the completed refresh."""
        stale_ids = {
            page_id
            for page_id in self._pages_by_id
            if page_id not in self._received_page_ids
        }
        if not stale_ids:
            self._store.delete_pages_not_in(set(self._pages_by_id))
            return

        for page_id in stale_ids:
            self._pages_by_id.pop(page_id, None)
            self._selected_ids.discard(page_id)
            self._cascade_selected_parent_ids.discard(page_id)
            self._page_default_card_types.pop(page_id, None)

        self._children_map = build_children_map_from_pages(self._pages_by_id)
        self._deck_names_by_page_id = build_deck_names_from_pages(self._pages_by_id)
        self._sync_tree_items()
        self._apply_selected_ids(self._selected_ids)
        self._store.delete_pages_not_in(set(self._pages_by_id))

        # Remove stale expansion ids after stale page cleanup.
        self._expanded_page_ids.intersection_update(self._pages_by_id)

    @staticmethod
    def _page_from_stored_row(page_id: str, stored_page: StoredPage) -> NotionPage:
        """Build a lightweight `NotionPage` model from one stored DB row."""
        title = PagesPage._title_from_deck_name(stored_page.anki_deck_name)
        parent_type = str(stored_page.parent_type) if stored_page.parent_type else None
        parent_id = str(stored_page.parent_id) if stored_page.parent_id else None
        return NotionPage(
            page_id=page_id,
            title=title,
            icon=None,
            parent_id=parent_id,
            parent_type=parent_type,
            raw={},
        )

    @staticmethod
    def _title_from_deck_name(deck_name: str) -> str:
        """Extract a display title from a persisted deck name path."""
        if not deck_name:
            return "Untitled"

        segment = str(deck_name).split("::")[-1].strip()
        if not segment:
            return "Untitled"
        return segment.replace("∷", "::")

    def _collect_expanded_ids(self) -> set[str]:
        """Capture expanded item ids from the current tree state."""
        expanded_ids: set[str] = set()
        for page_id, item in self._items_by_id.items():
            if item.isExpanded():
                expanded_ids.add(page_id)
        return expanded_ids

    def _apply_expanded_ids(self) -> None:
        """Re-apply captured expanded item ids to known tree items."""
        if not self._expanded_page_ids:
            return

        valid_ids = self._expanded_page_ids.intersection(self._items_by_id)
        for page_id in valid_ids:
            item = self._items_by_id.get(page_id)
            if item is not None:
                item.setExpanded(True)

    def _apply_cascade_selection(self, selected_ids: set[str]) -> set[str]:
        """Ensure descendants of cascade-selected parents are selected when they appear."""
        updated = set(selected_ids)

        for page_id in self._cascade_selected_parent_ids:
            if page_id not in updated:
                continue

            updated.update(get_descendant_ids(page_id, self._children_map))

        return updated

    def _apply_cascade_card_type_to_page(self, page_id: str) -> None:
        """Apply one pending card-type cascade to a newly loaded page, if needed."""
        has_cascade_match, cascaded_card_type = self._resolve_cascaded_card_type(page_id)
        if not has_cascade_match:
            return
        if self._page_default_card_types.get(page_id) == cascaded_card_type:
            return

        # Ensure known rows exist before applying the override update.
        self._persist_selection_state()
        self._store.set_page_default_card_type(page_id, cascaded_card_type)
        self._page_default_card_types[page_id] = cascaded_card_type

    def _resolve_cascaded_card_type(self, page_id: str) -> tuple[bool, str | None]:
        """Return nearest cascade-intent card type for one page, including explicit `None`."""
        seen_page_ids: set[str] = set()
        current_page_id: str | None = page_id

        while current_page_id:
            if current_page_id in seen_page_ids:
                break
            seen_page_ids.add(current_page_id)

            if current_page_id in self._cascade_card_type_parent_types:
                return True, self._cascade_card_type_parent_types[current_page_id]

            page = self._pages_by_id.get(current_page_id)
            if page is None or page.parent_type != "page_id" or not page.parent_id:
                break
            current_page_id = page.parent_id

        return False, None

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """Toggle checkboxes when users click a tree row, and consume the click event."""
        if watched is self._tree and event is not None:
            if event.type() == self._event_type_leave():
                self._hide_actions_if_cursor_outside_tree()
                return False

        if watched is self._tree.viewport() and event is not None:
            if event.type() == self._event_type_mouse_move():
                self._update_hovered_actions(event.pos())
                return False
            if event.type() == self._mouse_button_release_event_type():
                if self._mouse_button(event) == self._mouse_button_left():
                    if self._toggle_item_from_click(event.pos()):
                        return True
        
        return super().eventFilter(watched, event)

    def _hide_actions_if_cursor_outside_tree(self) -> None:
        """Hide row actions only when the cursor leaves the tree and combo popup is closed."""
        if self._is_hovered_combo_popup_open():
            return
        if self._is_cursor_inside_tree():
            return
        self._update_hovered_actions(None)

    def _is_cursor_inside_tree(self) -> bool:
        """Return whether the pointer is currently inside the tree viewport."""
        cursor_pos = QCursor.pos()
        local_pos = self._tree.viewport().mapFromGlobal(cursor_pos)
        return self._tree.viewport().rect().contains(local_pos)

    def _is_hovered_combo_popup_open(self) -> bool:
        """Return whether the currently hovered card-type combo popup is open."""
        if self._hovered_page_id is None:
            return False

        item = self._items_by_id.get(self._hovered_page_id)
        if item is None:
            return False

        type_combo = self._tree.itemWidget(item, 3)
        if not isinstance(type_combo, QComboBox):
            return False

        combo_popup = type_combo.view()
        return bool(combo_popup and combo_popup.isVisible())

    def _update_hovered_actions(self, mouse_pos: Any | None) -> None:
        """Show row actions only for the currently hovered and selected item."""
        hovered_page_id: str | None = None
        if mouse_pos is not None:
            hovered_item = self._tree.itemAt(mouse_pos)
            if hovered_item is not None:
                hovered_data = hovered_item.data(0, self._item_role_user())
                hovered_page_id = str(hovered_data) if hovered_data else None

        if hovered_page_id == self._hovered_page_id:
            return

        previous_hovered_id = self._hovered_page_id
        self._hovered_page_id = hovered_page_id

        if previous_hovered_id:
            previous_item = self._items_by_id.get(previous_hovered_id)
            if previous_item is not None:
                self._detach_item_actions(previous_item)
        if hovered_page_id and self._is_page_selected(hovered_page_id):
            hovered_item = self._items_by_id.get(hovered_page_id)
            if hovered_item is not None:
                self._attach_item_actions(hovered_item, hovered_page_id)

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
        self._sync_hovered_row_actions()

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

    def _show_tree_context_menu(self, pos: Any, *, from_viewport: bool) -> None:
        """Show schema-driven tree context menu for row or background clicks."""
        viewport_pos = pos if from_viewport else self._tree.viewport().mapFrom(self._tree, pos)
        item = self._tree.itemAt(viewport_pos)
        page_id = self._item_page_id(item)

        menu = QMenu(self._tree)
        if page_id is not None:
            self._populate_page_item_context_menu(menu, page_id)
            if self._context_menu_schema.on_background:
                menu.addSeparator()
            self._populate_page_background_context_menu(menu)
        else:
            self._populate_page_background_context_menu(menu)

        if menu.isEmpty():
            return
        global_pos = self._tree.viewport().mapToGlobal(viewport_pos)
        self._menu_exec(menu, global_pos)

    def _populate_page_item_context_menu(self, menu: QMenu, page_id: str) -> None:
        """Populate item-specific page actions from schema entries."""
        for entry in self._context_menu_schema.on_item:
            if entry.type == "action":
                action = menu.addAction(self._page_action_label(entry, page_id))
                action.triggered.connect(
                    lambda _checked=False, e=entry, pid=page_id: self._run_page_action_entry(e, pid)
                )
                continue

            if entry.type == "card_type_select":
                submenu = menu.addMenu(entry.label)
                self._populate_page_card_type_submenu(submenu, entry, page_id)

    def _populate_page_background_context_menu(self, menu: QMenu) -> None:
        """Populate background page actions from schema entries."""
        for entry in self._context_menu_schema.on_background:
            if entry.type != "action":
                continue
            action = menu.addAction(entry.label)
            action.triggered.connect(lambda _checked=False, e=entry: self._run_page_action_entry(e, None))

    def _populate_page_card_type_submenu(self, submenu: QMenu, entry: ContextMenuEntry, page_id: str) -> None:
        """Populate one card-type submenu with default-selectable card type options."""
        default_action = submenu.addAction("Default")
        default_action.triggered.connect(
            lambda _checked=False, e=entry, pid=page_id: self._run_page_card_type_entry(e, pid, None)
        )
        submenu.addSeparator()
        for card_type in DEFAULT_SELECTABLE_CARD_TYPES:
            label = card_type_label(card_type, abbreviation=False)
            action = submenu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, e=entry, pid=page_id, ct=card_type: self._run_page_card_type_entry(e, pid, ct)
            )

    def _page_action_label(self, entry: ContextMenuEntry, page_id: str) -> str:
        """Return one pages action label adjusted to current target selection state."""
        if entry.key == "toggle_page":
            return "Unselect Page" if page_id in self._selected_ids else "Select Page"

        if entry.key == "toggle_page_and_children":
            subtree_page_ids = {page_id, *get_descendant_ids(page_id, self._children_map)}
            is_subtree_fully_selected = all(
                child_page_id in self._selected_ids
                for child_page_id in subtree_page_ids
            )
            if is_subtree_fully_selected:
                return "Unselect Page and All Children"
            return "Select Page and All Children"

        return entry.label

    def _run_page_action_entry(self, entry: ContextMenuEntry, page_id: str | None) -> None:
        """Dispatch one schema action entry to the matching page-state operation."""
        if entry.key == "toggle_page":
            if page_id is None:
                return
            self._toggle_page_only(page_id)
            return
        if entry.key == "toggle_page_and_children":
            if page_id is None:
                return
            self._toggle_page_and_children(page_id)
            return
        if entry.key == "unselect_all_pages":
            self._set_selected_ids_and_persist(set())
            self._cascade_selected_parent_ids.clear()
            return
        if entry.key == "reset_all_page_default_types":
            self._reset_all_page_default_types()
            return

    def _run_page_card_type_entry(
        self,
        entry: ContextMenuEntry,
        page_id: str,
        card_type: str | None,
    ) -> None:
        """Dispatch one schema card-type selection entry for a page/subtree target."""
        if entry.key == "set_page_default_type":
            self._set_page_default_card_type_for_targets({page_id}, card_type)
            return
        if entry.key == "set_page_and_children_default_type":
            target_page_ids = {page_id, *get_descendant_ids(page_id, self._children_map)}
            self._set_page_default_card_type_for_targets(target_page_ids, card_type)
            return

    def _toggle_page_only(self, page_id: str) -> None:
        """Toggle one page selection without affecting descendants."""
        updated_selected_ids = set(self._selected_ids)
        if page_id in updated_selected_ids:
            updated_selected_ids.discard(page_id)
            self._cascade_selected_parent_ids.discard(page_id)
            self._cascade_selected_parent_ids.difference_update(self._get_ancestor_ids(page_id))
        else:
            updated_selected_ids.add(page_id)
            self._cascade_selected_parent_ids.discard(page_id)
        self._set_selected_ids_and_persist(updated_selected_ids)

    def _toggle_page_and_children(self, page_id: str) -> None:
        """Toggle one page and all descendants as one explicit subtree operation."""
        subtree_page_ids = {page_id, *get_descendant_ids(page_id, self._children_map)}
        updated_selected_ids = set(self._selected_ids)
        is_subtree_fully_selected = all(child_page_id in updated_selected_ids for child_page_id in subtree_page_ids)
        if is_subtree_fully_selected:
            updated_selected_ids.difference_update(subtree_page_ids)
        else:
            updated_selected_ids.update(subtree_page_ids)
        self._cascade_selected_parent_ids.difference_update(subtree_page_ids)
        self._set_selected_ids_and_persist(updated_selected_ids)

    def _set_selected_ids_and_persist(self, selected_ids: set[str]) -> None:
        """Apply selected ids to tree state and persist result to the pages table."""
        self._selected_ids = set(selected_ids)
        self._apply_selected_ids(self._selected_ids)
        self._sync_hovered_row_actions()
        self._persist_selection_state()

    def _set_page_default_card_type_for_targets(self, page_ids: set[str], card_type: str | None) -> None:
        """Persist one card-type value for a target page-id set and refresh UI state."""
        if not page_ids:
            return

        normalized_card_type = (
            None if card_type is None else normalize_default_selectable_card_type(card_type)
        )
        self._persist_selection_state()
        self._store.set_page_default_card_types(page_ids, normalized_card_type)
        for page_id in page_ids:
            self._page_default_card_types[page_id] = normalized_card_type
            self._cascade_card_type_parent_types.pop(page_id, None)
        self._sync_hovered_row_actions()

    def _reset_all_page_default_types(self) -> None:
        """Reset every page card type override to inherited global default."""
        self._persist_selection_state()
        self._store.reset_all_page_default_card_types()
        for page_id in self._page_default_card_types:
            self._page_default_card_types[page_id] = None
        self._cascade_card_type_parent_types.clear()
        self._sync_hovered_row_actions()

    def _item_page_id(self, item: QTreeWidgetItem | None) -> str | None:
        """Return page id for one tree item, or None when unavailable."""
        if item is None:
            return None
        page_id = item.data(0, self._item_role_user())
        if not page_id:
            return None
        return str(page_id)

    def _clear_tree_current(self) -> None:
        """Clear the current item index to avoid focus/selection highlight artifacts."""
        self._tree.setCurrentIndex(self._empty_model_index())

    def closeEvent(self, event: Any) -> None:
        """Invalidate pending background results when the widget closes."""
        self._load_generation += 1
        self._is_loading = False
        self._fetch_timer.stop()
        super().closeEvent(event)

    def changeEvent(self, event: Any) -> None:
        """Refresh icon set when Qt notifies this widget about palette changes."""
        if event is not None:
            event_type = event.type()
            if event_type in (
                self._event_type_palette_change(),
                self._event_type_application_palette_change(),
            ):
                self._reload_action_icons()
                self._refresh_action_icons_in_tree()
        super().changeEvent(event)

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
    def _docs_icon_path(filename: str) -> str:
        """Return absolute path for one icon file stored in the repository docs folder."""
        return str(Path(__file__).resolve().parents[1] / "docs" / filename)

    def _is_dark_palette(self) -> bool:
        """Return whether the active window background is dark enough for light icons."""
        color = self.palette().window().color()
        return int(color.lightness()) < 128

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
    def _context_menu_policy_custom() -> Any:
        """Return Qt custom-context-menu policy in a version-safe way."""
        from aqt.qt import Qt

        context_menu_policy = getattr(Qt, "ContextMenuPolicy", None)
        if context_menu_policy is not None and hasattr(context_menu_policy, "CustomContextMenu"):
            return getattr(context_menu_policy, "CustomContextMenu")
        return getattr(Qt, "CustomContextMenu")

    @staticmethod
    def _mouse_button_release_event_type() -> Any:
        """Return the mouse-button-release event type value."""
        event_type = getattr(QEvent, "Type", None)
        if event_type is not None and hasattr(event_type, "MouseButtonRelease"):
            return getattr(event_type, "MouseButtonRelease")
        return getattr(QEvent, "MouseButtonRelease")

    @staticmethod
    def _event_type_palette_change() -> Any:
        """Return the Qt event type value for widget palette changes."""
        event_type = getattr(QEvent, "Type", None)
        if event_type is not None and hasattr(event_type, "PaletteChange"):
            return getattr(event_type, "PaletteChange")
        return getattr(QEvent, "PaletteChange")

    @staticmethod
    def _event_type_application_palette_change() -> Any:
        """Return the Qt event type value for application palette changes."""
        event_type = getattr(QEvent, "Type", None)
        if event_type is not None and hasattr(event_type, "ApplicationPaletteChange"):
            return getattr(event_type, "ApplicationPaletteChange")
        return getattr(QEvent, "ApplicationPaletteChange")

    @staticmethod
    def _event_type_mouse_move() -> Any:
        """Return the Qt event type value for mouse move events."""
        event_type = getattr(QEvent, "Type", None)
        if event_type is not None and hasattr(event_type, "MouseMove"):
            return getattr(event_type, "MouseMove")
        return getattr(QEvent, "MouseMove")

    @staticmethod
    def _event_type_leave() -> Any:
        """Return the Qt event type value for pointer leave events."""
        event_type = getattr(QEvent, "Type", None)
        if event_type is not None and hasattr(event_type, "Leave"):
            return getattr(event_type, "Leave")
        return getattr(QEvent, "Leave")

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

    @staticmethod
    def _combo_adjust_to_contents_policy() -> Any:
        """Return the QComboBox policy that sizes to the current contents."""
        adjust_policy = getattr(QComboBox, "SizeAdjustPolicy", None)
        if adjust_policy is not None and hasattr(adjust_policy, "AdjustToContents"):
            return getattr(adjust_policy, "AdjustToContents")
        return getattr(QComboBox, "AdjustToContents")

    @staticmethod
    def _size_policy_fixed() -> Any:
        """Return the QSizePolicy fixed policy in a Qt-version-safe way."""
        size_policy = getattr(QSizePolicy, "Policy", None)
        if size_policy is not None and hasattr(size_policy, "Fixed"):
            return getattr(size_policy, "Fixed")
        return getattr(QSizePolicy, "Fixed")

    @staticmethod
    def _menu_exec(menu: QMenu, global_pos: Any) -> None:
        """Execute a menu in a Qt5/Qt6 compatible way."""
        exec_method = getattr(menu, "exec", None)
        if callable(exec_method):
            exec_method(global_pos)
            return
        menu.exec_(global_pos)


def build_page(parent: QWidget, context: UiContext) -> QWidget:
    """Factory used by the UI shell to build the Pages tab widget."""
    return PagesPage(parent=parent, context=context)
