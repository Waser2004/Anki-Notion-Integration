"""Cards page UI for per-card type overrides within one synced Notion page."""

from __future__ import annotations

import queue
import threading
from typing import Any

from aqt.qt import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QVBoxLayout,
    QWidget,
)

from ..modules.card_type_overrides import CardTypeOverrideStore
from ..modules.card_types import (
    CLOZE,
    DEFAULT_SELECTABLE_CARD_TYPES,
    card_type_label,
    normalize_card_type,
    normalize_default_selectable_card_type,
)
from ..modules.cards_store import CardsStore
from ..modules.db import Database
from ..modules.notion_client import NotionClient
from ..modules.pages import PagesStore, StoredPage
from ..modules.parser.cloze_card_parser import (
    ClozeCardParser,
    paragraph_has_cloze_marker,
)
from ..modules.settings import SettingsStore
from .context_menu_schema import ContextMenuEntry, load_context_menu_schema
from .ui import UiContext

_DB_FRONT_PLACEHOLDER = "Loading front text..."


class CardsPage(QWidget):
    """Qt widget for selecting per-card type overrides on one Notion page."""

    def __init__(self, parent: QWidget, context: UiContext) -> None:
        super().__init__(parent)
        self._context = context
        self._db = Database(context.db_path)
        self._pages_store = PagesStore(self._db)
        self._override_store = CardTypeOverrideStore(self._db)
        self._cards_store = CardsStore(self._db)
        self._pages_by_id: dict[str, StoredPage] = {}
        self._excluded_block_ids: set[str] = set()
        self._card_kind_by_block_id: dict[str, str] = {}
        self._context_menu_schema = load_context_menu_schema().cards
        self._load_generation = 0
        self._fetch_queue: queue.Queue[tuple[str, int, Any]] = queue.Queue()
        self._table_card_type_combo_size: tuple[int, int] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 11, 11, 11)

        self._status_label = QLabel("Select a synced page to configure per-card types.", self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        selector_row = QHBoxLayout()
        selector_row.setContentsMargins(0, 0, 0, 0)
        selector_row.setSpacing(0)
        layout.addLayout(selector_row)

        self._page_combo = QComboBox(self)
        self._page_combo.currentIndexChanged.connect(self._on_page_changed)
        selector_row.addWidget(self._page_combo, 1)
        selector_row.addSpacing(8)

        self._page_default_type_combo = QComboBox(self)
        self._page_default_type_combo.addItem("Default", None)
        for card_type in DEFAULT_SELECTABLE_CARD_TYPES:
            self._page_default_type_combo.addItem(card_type_label(card_type, abbreviation=True), card_type)
        self._configure_card_type_combo(self._page_default_type_combo)
        self._page_default_type_combo.currentIndexChanged.connect(self._on_page_default_card_type_changed)
        self._page_default_type_combo.setToolTip("Default card type for this deck/page.")
        self._sync_selector_combo_heights()
        selector_row.addWidget(self._page_default_type_combo)
        self._selector_scrollbar_spacer = QWidget(self)
        self._selector_scrollbar_spacer.setFixedWidth(0)
        selector_row.addWidget(self._selector_scrollbar_spacer)

        self._cards_table = QTableWidget(self)
        self._cards_table.setColumnCount(2)
        self._cards_table.setHorizontalHeaderLabels(("Card front", "Card type"))
        self._cards_table.horizontalHeader().setVisible(True)
        self._cards_table.setEditTriggers(self._table_edit_trigger_no_edit())
        self._cards_table.setSelectionMode(self._table_selection_mode_no_selection())
        self._cards_table.setFocusPolicy(self._focus_policy_no_focus())
        self._cards_table.setContextMenuPolicy(self._context_menu_policy_custom())
        self._cards_table.customContextMenuRequested.connect(self._show_cards_context_menu)
        self._cards_table.verticalHeader().setVisible(False)
        self._cards_table.setStyleSheet(
            "QTableWidget { border-radius: 3px; }"
            "QTableWidget::item { padding: 2px; border: none; }"
        )
        self._cards_table.resizeRowsToContents()
        header = self._cards_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._cards_table.verticalScrollBar().rangeChanged.connect(
            lambda _minimum, _maximum: self._sync_table_layout_alignment()
        )
        layout.addWidget(self._cards_table, 1)

        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(40)
        self._fetch_timer.timeout.connect(self._drain_fetch_queue)

        self.reload()

    def reload(self) -> None:
        """Reload pages dropdown and reset table state."""
        self._load_generation += 1
        self._fetch_timer.stop()
        self._set_loading_state(is_loading=False)
        self._reload_page_combo(autoload_cards=True)

    def showEvent(self, event: Any) -> None:
        """Refresh selected page default type from DB whenever the Cards tab is shown."""
        super().showEvent(event)
        self._sync_page_default_combo_for_selected_page()

    def on_navigation_payload(self, payload: dict[str, Any]) -> None:
        """Handle optional navigation payload from other tabs."""
        page_id = payload.get("page_id")
        if not isinstance(page_id, str) or not page_id:
            return

        target_index = self._index_for_page_id(page_id)
        if target_index < 0:
            self._reload_page_combo(autoload_cards=False)
            target_index = self._index_for_page_id(page_id)
            if target_index < 0:
                self._status_label.setText("Selected page is not available for Cards yet.")
                return

        self._page_combo.setCurrentIndex(target_index)
        self._sync_page_default_combo_for_selected_page()
        self._load_selected_page_cards()

    def _reload_page_combo(self, *, autoload_cards: bool) -> None:
        """Reload page dropdown from DB and optionally load first page cards."""
        self._page_combo.blockSignals(True)
        self._page_combo.clear()

        pages = self._pages_store.get_pages()
        self._pages_by_id = dict(pages)
        enabled_pages = [page for page in pages.values() if page.sync_enabled]
        enabled_pages.sort(key=lambda page: page.anki_deck_name)

        for page in enabled_pages:
            self._page_combo.addItem(page.anki_deck_name, page.notion_page_id)

        self._page_combo.blockSignals(False)

        if self._page_combo.count() == 0:
            self._status_label.setText("No synced pages available. Enable pages in the Pages tab first.")
            self._cards_table.setRowCount(0)
            self._excluded_block_ids = set()
            self._card_kind_by_block_id = {}
            self._sync_table_layout_alignment()
            self._set_page_default_combo_value(None)
            return

        self._page_combo.setCurrentIndex(0)
        self._sync_page_default_combo_for_selected_page()
        if autoload_cards:
            self._load_selected_page_cards()

    def _index_for_page_id(self, page_id: str) -> int:
        """Return dropdown index for one page id, or -1 when absent."""
        for index in range(self._page_combo.count()):
            if self._page_combo.itemData(index) == page_id:
                return index
        return -1

    def _on_page_changed(self, _index: int) -> None:
        """Load cards for selected page."""
        self._sync_page_default_combo_for_selected_page()
        self._load_selected_page_cards()

    def _load_selected_page_cards(self) -> None:
        """Load DB-backed rows immediately, then replace with live Notion rows."""
        self._load_generation += 1
        generation = self._load_generation
        self._fetch_timer.stop()
        self._set_loading_state(is_loading=False)

        page_id = self._selected_page_id()
        if page_id is None:
            self._status_label.setText("Select a page to view cards.")
            self._cards_table.setRowCount(0)
            self._excluded_block_ids = set()
            self._card_kind_by_block_id = {}
            self._sync_table_layout_alignment()
            return

        overrides = self._override_store.get_card_type_overrides_for_page(page_id)
        self._show_db_rows(page_id, overrides)
        self._status_label.setText("Loading cards from Notion...")
        self._set_loading_state(is_loading=True)
        self._fetch_timer.start()
        worker = threading.Thread(
            target=self._fetch_page_cards_worker,
            args=(generation, page_id),
            daemon=True,
        )
        worker.start()

    def _fetch_page_cards_worker(self, generation: int, page_id: str) -> None:
        """Fetch one page's root toggle and cloze cards in a worker thread."""
        try:
            db = Database(self._context.db_path)
            client = NotionClient.from_settings(db, profile_name=self._resolve_profile_name(self._context))
            settings = SettingsStore(db, profile_name=self._resolve_profile_name(self._context))
            enable_gray_toggle_cloze = bool(settings.get_value("enable_gray_toggle_cloze_parsing"))
            cloze_marker_colors = list(settings.get_value("cloze_marker_colors"))
            blocks = client.get_page_blocks_shallow(page_id)
            cloze_parser = ClozeCardParser(cloze_marker_colors)
            cards: list[dict[str, str]] = []
            for block in blocks:
                if block.block_type == "toggle":
                    card_kind = (
                        "cloze"
                        if cloze_parser.is_advanced_container(
                            block,
                            enable_gray_toggle_cloze=enable_gray_toggle_cloze,
                        )
                        else "toggle"
                    )
                    cards.append(
                        {
                            "notion_block_id": block.block_id,
                            "front_text": self._toggle_front_plain_text(block.raw),
                            "card_kind": card_kind,
                        }
                    )
                    continue
                if block.block_type == "paragraph" and paragraph_has_cloze_marker(
                    block.raw, cloze_marker_colors
                ):
                    cards.append(
                        {
                            "notion_block_id": block.block_id,
                            "front_text": self._paragraph_plain_text(block.raw),
                            "card_kind": "cloze",
                        }
                    )
            self._fetch_queue.put(("done", generation, {"page_id": page_id, "cards": cards}))
        except Exception as exc:
            self._fetch_queue.put(("error", generation, str(exc)))

    def _drain_fetch_queue(self) -> None:
        """Apply completed worker events on the Qt thread."""
        processed = 0
        while processed < 100:
            try:
                event_type, generation, payload = self._fetch_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if generation != self._load_generation:
                continue

            self._fetch_timer.stop()
            self._set_loading_state(is_loading=False)

            if event_type == "error":
                self._status_label.setText(f"Failed to load cards: {payload}")
                return
            if event_type == "done":
                self._apply_live_rows(payload)
                return

    def _show_db_rows(self, page_id: str, overrides: dict[str, str]) -> None:
        """Render quick DB-backed placeholder rows while live Notion data is loading."""
        connection = self._db.connect()
        try:
            rows = connection.execute(
                """
                SELECT notion_block_id, excluded
                     , card_type
                FROM cards
                WHERE notion_page_id = ?
                ORDER BY notion_block_id
                """,
                (page_id,),
            ).fetchall()
        finally:
            connection.close()

        excluded_by_block_id = {
            str(row["notion_block_id"]): bool(row["excluded"])
            for row in rows
        }
        self._excluded_block_ids = {
            block_id
            for block_id, is_excluded in excluded_by_block_id.items()
            if is_excluded
        }
        self._card_kind_by_block_id = {}
        for row in rows:
            block_id = str(row["notion_block_id"])
            self._card_kind_by_block_id[block_id] = (
                "cloze" if normalize_card_type(str(row["card_type"]), default="") == CLOZE else "toggle"
            )

        block_ids = set(excluded_by_block_id)
        block_ids.update(overrides.keys())
        ordered_block_ids = sorted(block_ids)

        self._cards_table.setRowCount(len(ordered_block_ids))
        for row_index, block_id in enumerate(ordered_block_ids):
            self._set_row_front_item(
                row_index=row_index,
                block_id=block_id,
                front_text=_DB_FRONT_PLACEHOLDER,
                is_excluded=block_id in self._excluded_block_ids,
            )
            self._set_row_card_type_widget(
                row_index=row_index,
                page_id=page_id,
                block_id=block_id,
                selected_override=overrides.get(block_id),
                is_excluded=block_id in self._excluded_block_ids,
            )
        self._sync_table_layout_alignment()

    def _apply_live_rows(self, payload: Any) -> None:
        """Render live Notion rows and merge persisted override selections."""
        if not isinstance(payload, dict):
            self._status_label.setText("No cards available for the selected page.")
            self._cards_table.setRowCount(0)
            return

        page_id = payload.get("page_id")
        if not isinstance(page_id, str):
            self._status_label.setText("No cards available for the selected page.")
            self._cards_table.setRowCount(0)
            return

        cards_payload = payload.get("cards")
        rows: list[dict[str, str]] = []
        if isinstance(cards_payload, list):
            for row in cards_payload:
                if not isinstance(row, dict):
                    continue
                block_id = row.get("notion_block_id")
                front_text = row.get("front_text")
                card_kind = row.get("card_kind")
                if not isinstance(block_id, str):
                    continue
                if not isinstance(front_text, str):
                    front_text = ""
                if card_kind not in {"toggle", "cloze"}:
                    card_kind = "toggle"
                rows.append({"notion_block_id": block_id, "front_text": front_text, "card_kind": card_kind})

        if not rows:
            self._status_label.setText("No toggle or cloze cards found for this page.")
            self._cards_table.setRowCount(0)
            self._excluded_block_ids = set()
            self._card_kind_by_block_id = {}
            self._sync_table_layout_alignment()
            return

        overrides = self._override_store.get_card_type_overrides_for_page(page_id)
        self._excluded_block_ids = self._cards_store.get_excluded_block_ids_for_page(page_id)
        self._card_kind_by_block_id = {
            row["notion_block_id"]: str(row["card_kind"])
            for row in rows
        }
        self._cards_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            block_id = row["notion_block_id"]
            row_kind = self._card_kind_by_block_id.get(block_id, "toggle")
            fallback_title = "Untitled cloze" if row_kind == "cloze" else "Untitled toggle"
            front_text = row["front_text"].strip() or fallback_title
            is_excluded = block_id in self._excluded_block_ids
            self._set_row_front_item(
                row_index=row_index,
                block_id=block_id,
                front_text=front_text,
                is_excluded=is_excluded,
            )
            self._set_row_card_type_widget(
                row_index=row_index,
                page_id=page_id,
                block_id=block_id,
                selected_override=overrides.get(block_id),
                is_excluded=is_excluded,
            )
        self._sync_table_layout_alignment()
        self._status_label.setText("Select a card type override per row as needed.")

    def _show_cards_context_menu(self, pos: Any) -> None:
        """Show schema-driven cards context menu for row/background clicks."""
        item = self._cards_table.itemAt(pos)
        row_index = int(item.row()) if isinstance(item, QTableWidgetItem) else int(self._cards_table.rowAt(pos.y()))
        if row_index < 0:
            row_index = -1
        block_id = self._block_id_for_row(row_index)

        menu = QMenu(self._cards_table)
        if block_id is not None:
            self._populate_cards_item_context_menu(menu, block_id)
            if self._context_menu_schema.on_background:
                menu.addSeparator()
        self._populate_cards_background_context_menu(menu)
        if menu.isEmpty():
            return
        self._menu_exec(menu, self._cards_table.viewport().mapToGlobal(pos))

    def _populate_cards_item_context_menu(self, menu: QMenu, block_id: str) -> None:
        """Populate row-specific card actions from schema entries."""
        for entry in self._context_menu_schema.on_item:
            if entry.type != "action":
                continue
            action_label = entry.label
            if entry.key == "toggle_card_excluded":
                action_label = "Unexclude Card" if block_id in self._excluded_block_ids else "Exclude Card"
            action = menu.addAction(action_label)
            action.triggered.connect(
                lambda _checked=False, e=entry, bid=block_id: self._run_cards_action_entry(e, bid)
            )

    def _populate_cards_background_context_menu(self, menu: QMenu) -> None:
        """Populate cards background actions from schema entries."""
        for entry in self._context_menu_schema.on_background:
            if entry.type != "action":
                continue
            action = menu.addAction(entry.label)
            action.triggered.connect(lambda _checked=False, e=entry: self._run_cards_action_entry(e, None))

    def _run_cards_action_entry(self, entry: ContextMenuEntry, block_id: str | None) -> None:
        """Dispatch one cards schema action key to implementation handlers."""
        if entry.key == "toggle_card_excluded":
            if block_id is None:
                return
            self._toggle_card_excluded(block_id)
            return
        if entry.key == "reset_all_toggle_overrides":
            self._reset_all_toggle_overrides()
            return

    def _toggle_card_excluded(self, block_id: str) -> None:
        """Toggle excluded state for one card row and update the rendered row."""
        page_id = self._selected_page_id()
        if page_id is None:
            return

        should_exclude = block_id not in self._excluded_block_ids
        self._cards_store.set_card_excluded(page_id, block_id, should_exclude)
        if should_exclude:
            self._excluded_block_ids.add(block_id)
        else:
            self._excluded_block_ids.discard(block_id)

        row_index = self._row_index_for_block_id(block_id)
        if row_index < 0:
            return

        front_item = self._cards_table.item(row_index, 0)
        front_text = front_item.text() if isinstance(front_item, QTableWidgetItem) else "Untitled toggle"
        self._set_row_front_item(
            row_index=row_index,
            block_id=block_id,
            front_text=front_text,
            is_excluded=should_exclude,
        )
        selected_override = self._override_store.get_card_type_overrides_for_page(page_id).get(block_id)
        self._set_row_card_type_widget(
            row_index=row_index,
            page_id=page_id,
            block_id=block_id,
            selected_override=selected_override,
            is_excluded=should_exclude,
        )
        self._sync_table_layout_alignment()

    def _reset_all_toggle_overrides(self) -> None:
        """Clear all toggle overrides for selected page, including excluded rows."""
        page_id = self._selected_page_id()
        if page_id is None:
            return

        self._override_store.clear_card_type_overrides_for_page(page_id)
        for row_index in range(self._cards_table.rowCount()):
            block_id = self._block_id_for_row(row_index)
            if block_id is None or block_id in self._excluded_block_ids:
                continue
            widget = self._cards_table.cellWidget(row_index, 1)
            if not isinstance(widget, QComboBox):
                continue
            previous = widget.blockSignals(True)
            try:
                widget.setCurrentIndex(0)
            finally:
                widget.blockSignals(previous)
        self._status_label.setText("Reset card type overrides to default for all toggles on this page.")

    def _set_row_front_item(self, *, row_index: int, block_id: str, front_text: str, is_excluded: bool) -> None:
        """Render the row front text and apply strike-through style for excluded rows."""
        front_item = QTableWidgetItem(front_text)
        front_item.setToolTip(block_id)
        front_item.setData(self._item_data_user_role(), block_id)
        font = front_item.font()
        font.setStrikeOut(is_excluded)
        front_item.setFont(font)
        self._cards_table.setItem(row_index, 0, front_item)

    def _set_row_card_type_widget(
        self,
        *,
        row_index: int,
        page_id: str,
        block_id: str,
        selected_override: str | None,
        is_excluded: bool,
    ) -> None:
        """Render row card-type cell as combo or empty placeholder for excluded rows."""
        existing_widget = self._cards_table.cellWidget(row_index, 1)
        if existing_widget is not None:
            self._cards_table.removeCellWidget(row_index, 1)
            existing_widget.deleteLater()

        if is_excluded:
            return
        if self._card_kind_by_block_id.get(block_id) == "cloze":
            return

        self._cards_table.setCellWidget(
            row_index,
            1,
            self._build_card_type_combo(
                page_id=page_id,
                block_id=block_id,
                selected_override=selected_override,
            ),
        )

    def _block_id_for_row(self, row_index: int) -> str | None:
        """Return block id stored on a table row, or None for missing rows."""
        if row_index < 0:
            return None
        front_item = self._cards_table.item(row_index, 0)
        if not isinstance(front_item, QTableWidgetItem):
            return None
        block_id = front_item.data(self._item_data_user_role())
        if not isinstance(block_id, str) or not block_id:
            return None
        return block_id

    def _row_index_for_block_id(self, block_id: str) -> int:
        """Return row index for one block id, or -1 when absent in current table."""
        for row_index in range(self._cards_table.rowCount()):
            if self._block_id_for_row(row_index) == block_id:
                return row_index
        return -1

    def _build_card_type_combo(
        self,
        *,
        page_id: str,
        block_id: str,
        selected_override: str | None,
    ) -> QComboBox:
        """Create one card-type combo bound to a page/block id."""
        combo = QComboBox(self._cards_table)
        combo.addItem("Default", None)
        for card_type in DEFAULT_SELECTABLE_CARD_TYPES:
            combo.addItem(card_type_label(card_type, abbreviation=True), card_type)

        selected_index = 0
        if selected_override is not None:
            normalized_override = normalize_default_selectable_card_type(selected_override)
            for index in range(combo.count()):
                if combo.itemData(index) == normalized_override:
                    selected_index = index
                    break
        combo.setCurrentIndex(selected_index)
        combo.currentIndexChanged.connect(
            lambda _index, pid=page_id, bid=block_id, widget=combo: self._on_card_type_selected(pid, bid, widget)
        )
        self._configure_card_type_combo(combo)
        self._apply_uniform_table_combo_size(combo)
        return combo

    def _apply_uniform_table_combo_size(self, combo: QComboBox) -> None:
        """Keep all table card-type combos at one shared width/height."""
        if self._table_card_type_combo_size is None:
            reference_combo = self._page_default_type_combo
            width = int(reference_combo.sizeHint().width())
            height = int(reference_combo.height() or reference_combo.sizeHint().height())
            self._table_card_type_combo_size = (width, height)

        width, height = self._table_card_type_combo_size
        combo.setFixedWidth(width)
        combo.setFixedHeight(height)

    def _configure_card_type_combo(self, combo: QComboBox) -> None:
        """Apply compact card-type combo styling and popup sizing behavior."""
        combo.setSizePolicy(self._size_policy_fixed(), self._size_policy_fixed())
        combo.setSizeAdjustPolicy(self._combo_adjust_to_contents_policy())
        combo.setMinimumContentsLength(0)
        combo.setStyleSheet(
            "QComboBox { padding: 0px 4px; }"
            "QComboBox:on { padding: 0px; }"
        )
        self._configure_card_type_combo_popup(combo)

    def _configure_card_type_combo_popup(self, combo: QComboBox) -> None:
        """Ensure expanded popup is wide enough for all card-type labels."""
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
        """Return popup width needed so the longest label is never clipped."""
        font_metrics = combo.fontMetrics()
        longest_label_width = 0
        for index in range(combo.count()):
            label_width = int(font_metrics.horizontalAdvance(combo.itemText(index)))
            if label_width > longest_label_width:
                longest_label_width = label_width

        popup_padding_width = 52
        collapsed_combo_width = int(combo.sizeHint().width())
        return max(collapsed_combo_width, longest_label_width + popup_padding_width)

    def _on_card_type_selected(self, page_id: str, block_id: str, combo: QComboBox) -> None:
        """Persist one per-card override update."""
        selected = combo.currentData()
        selected_type = None if selected is None else normalize_default_selectable_card_type(str(selected))
        self._override_store.set_card_type_override(page_id, block_id, selected_type)

    def _set_loading_state(self, *, is_loading: bool) -> None:
        """Disable page selector while background load is active."""
        self._page_combo.setEnabled(not is_loading)
        self._page_default_type_combo.setEnabled(not is_loading and self._selected_page_id() is not None)

    def _sync_page_default_combo_for_selected_page(self) -> None:
        """Sync page-default combo with latest DB value for the currently selected page."""
        page_id = self._selected_page_id()
        if page_id is None:
            self._set_page_default_combo_value(None)
            return

        default_card_type = self._page_default_card_type_from_db(page_id)
        self._set_page_default_combo_value(default_card_type)

    def _set_page_default_combo_value(self, card_type: str | None) -> None:
        """Set page-default combo value without triggering persistence."""
        previous = self._page_default_type_combo.blockSignals(True)
        try:
            selected_index = 0
            if card_type is not None:
                normalized_card_type = normalize_default_selectable_card_type(card_type)
                for index in range(self._page_default_type_combo.count()):
                    if self._page_default_type_combo.itemData(index) == normalized_card_type:
                        selected_index = index
                        break
            self._page_default_type_combo.setCurrentIndex(selected_index)
        finally:
            self._page_default_type_combo.blockSignals(previous)

    def _on_page_default_card_type_changed(self, _index: int) -> None:
        """Persist selected page default card type for the currently selected page."""
        page_id = self._selected_page_id()
        if page_id is None:
            return

        selected = self._page_default_type_combo.currentData()
        selected_type = None if selected is None else normalize_default_selectable_card_type(str(selected))
        self._pages_store.set_page_default_card_type(page_id, selected_type)
        self._update_cached_page_default_card_type(page_id, selected_type)

    def _update_cached_page_default_card_type(self, page_id: str, card_type: str | None) -> None:
        """Keep local page cache aligned with persisted page default type changes."""
        page = self._pages_by_id.get(page_id)
        if page is None:
            return
        self._pages_by_id[page_id] = StoredPage(
            notion_page_id=page.notion_page_id,
            anki_deck_name=page.anki_deck_name,
            anki_deck_id=page.anki_deck_id,
            sync_enabled=page.sync_enabled,
            last_synced_at=page.last_synced_at,
            parent_id=page.parent_id,
            parent_type=page.parent_type,
            default_card_type=card_type,
        )

    def _page_default_card_type_from_db(self, page_id: str) -> str | None:
        """Read one page default card type directly from DB and refresh local cache."""
        page = self._pages_store.get_pages().get(page_id)
        if page is None:
            return None
        self._pages_by_id[page_id] = page
        return page.default_card_type

    def _sync_table_layout_alignment(self) -> None:
        """Sync card-type column width and selector-row spacer after table updates."""
        self._sync_card_type_column_width()
        self._apply_selector_row_scrollbar_spacer_width()
        QTimer.singleShot(0, self._sync_table_layout_alignment_deferred)

    def _sync_table_layout_alignment_deferred(self) -> None:
        """Re-apply alignment one tick later so post-layout geometry is respected."""
        self._sync_card_type_column_width()
        self._apply_selector_row_scrollbar_spacer_width()

    def _sync_card_type_column_width(self) -> None:
        """Set the card-type column to the widest combo/header width."""
        font_metrics = self._cards_table.fontMetrics()
        header_text_width = int(font_metrics.horizontalAdvance("Card type")) + 20

        combo_width = 0
        for row_index in range(self._cards_table.rowCount()):
            widget = self._cards_table.cellWidget(row_index, 1)
            if isinstance(widget, QComboBox):
                combo_width = max(combo_width, int(widget.sizeHint().width()))

        target_width = max(header_text_width, combo_width)
        self._cards_table.setColumnWidth(1, target_width)

    def _apply_selector_row_scrollbar_spacer_width(self) -> None:
        """Apply current scrollbar-based spacer width after table geometry settles."""
        scrollbar = self._cards_table.verticalScrollBar()
        needs_scrollbar = scrollbar.isVisible() and scrollbar.maximum() > scrollbar.minimum()
        spacer_width = int(scrollbar.sizeHint().width()) if needs_scrollbar else 0
        self._selector_scrollbar_spacer.setFixedWidth(spacer_width)

    def _sync_selector_combo_heights(self) -> None:
        """Keep both top-row combo boxes at identical height."""
        combo_height = max(
            int(self._page_combo.sizeHint().height()),
            int(self._page_default_type_combo.sizeHint().height()),
        )
        self._page_combo.setFixedHeight(combo_height)
        self._page_default_type_combo.setFixedHeight(combo_height)

    def _selected_page_id(self) -> str | None:
        """Return selected page id from dropdown."""
        value = self._page_combo.currentData()
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _toggle_front_plain_text(raw_payload: dict[str, Any]) -> str:
        """Extract plain-text title from a raw Notion toggle payload."""
        toggle_payload = raw_payload.get("toggle")
        if not isinstance(toggle_payload, dict):
            return ""
        rich_text = toggle_payload.get("rich_text")
        if not isinstance(rich_text, list):
            return ""

        parts: list[str] = []
        for item in rich_text:
            if not isinstance(item, dict):
                continue
            plain_text = item.get("plain_text")
            if isinstance(plain_text, str) and plain_text:
                parts.append(plain_text)
        return "".join(parts)

    @staticmethod
    def _paragraph_plain_text(raw_payload: dict[str, Any]) -> str:
        """Extract plain-text content from a raw Notion paragraph payload."""
        paragraph_payload = raw_payload.get("paragraph")
        if not isinstance(paragraph_payload, dict):
            return ""
        rich_text = paragraph_payload.get("rich_text")
        if not isinstance(rich_text, list):
            return ""

        parts: list[str] = []
        for item in rich_text:
            if not isinstance(item, dict):
                continue
            plain_text = item.get("plain_text")
            if isinstance(plain_text, str) and plain_text:
                parts.append(plain_text)
        return "".join(parts)

    @staticmethod
    def _resolve_profile_name(context: UiContext) -> str | None:
        """Try to determine active profile name for keyring namespacing."""
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
    def _table_edit_trigger_no_edit() -> Any:
        """Return the table edit trigger that disables inline editing."""
        from aqt.qt import QAbstractItemView

        edit_trigger = getattr(QAbstractItemView, "EditTrigger", None)
        if edit_trigger is not None and hasattr(edit_trigger, "NoEditTriggers"):
            return getattr(edit_trigger, "NoEditTriggers")
        return getattr(QAbstractItemView, "NoEditTriggers")

    @staticmethod
    def _table_selection_mode_no_selection() -> Any:
        """Return table selection mode that disables row selection highlighting."""
        from aqt.qt import QAbstractItemView

        selection_mode = getattr(QAbstractItemView, "SelectionMode", None)
        if selection_mode is not None and hasattr(selection_mode, "NoSelection"):
            return getattr(selection_mode, "NoSelection")
        return getattr(QAbstractItemView, "NoSelection")

    @staticmethod
    def _focus_policy_no_focus() -> Any:
        """Return the Qt focus policy that disables focus on the table."""
        from aqt.qt import Qt

        focus_policy = getattr(Qt, "FocusPolicy", None)
        if focus_policy is not None and hasattr(focus_policy, "NoFocus"):
            return getattr(focus_policy, "NoFocus")
        return getattr(Qt, "NoFocus")

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
    def _item_data_user_role() -> int:
        """Return Qt user-data role used to store block ids on table items."""
        from aqt.qt import Qt

        item_data_role = getattr(Qt, "ItemDataRole", None)
        if item_data_role is not None and hasattr(item_data_role, "UserRole"):
            return int(getattr(item_data_role, "UserRole"))
        return int(getattr(Qt, "UserRole"))

    @staticmethod
    def _context_menu_policy_custom() -> Any:
        """Return Qt custom-context-menu policy in a version-safe way."""
        from aqt.qt import Qt

        context_menu_policy = getattr(Qt, "ContextMenuPolicy", None)
        if context_menu_policy is not None and hasattr(context_menu_policy, "CustomContextMenu"):
            return getattr(context_menu_policy, "CustomContextMenu")
        return getattr(Qt, "CustomContextMenu")

    @staticmethod
    def _menu_exec(menu: QMenu, global_pos: Any) -> None:
        """Execute a menu in a Qt5/Qt6 compatible way."""
        exec_method = getattr(menu, "exec", None)
        if callable(exec_method):
            exec_method(global_pos)
            return
        menu.exec_(global_pos)


def build_page(parent: QWidget, context: UiContext) -> QWidget:
    """Factory used by ui.json to build the Cards tab widget."""
    return CardsPage(parent=parent, context=context)
