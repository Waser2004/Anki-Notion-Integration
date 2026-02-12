"""Tests for per-card override persistence."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.card_type_overrides import CardTypeOverrideStore
from Noteck.modules.db import Database


class CardTypeOverrideStoreTests(unittest.TestCase):
    """Validate read/write behavior for card type overrides."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db = Database(Path(self._temp_dir.name) / "overrides.db")
        self._db.initialize()
        self._store = CardTypeOverrideStore(self._db)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO pages (notion_page_id, anki_deck_name, sync_enabled)
                VALUES (?, ?, ?), (?, ?, ?)
                """,
                ("page-a", "Notion::A", 1, "page-b", "Notion::B", 1),
            )
            connection.commit()
        finally:
            connection.close()

    def test_set_and_get_round_trip(self) -> None:
        self._store.set_card_type_override("page-a", "block-1", "input")
        overrides = self._store.get_card_type_overrides_for_page("page-a")
        self.assertEqual(overrides, {"block-1": "input"})

    def test_set_none_deletes_override(self) -> None:
        self._store.set_card_type_override("page-a", "block-1", "input")
        self._store.set_card_type_override("page-a", "block-1", None)
        overrides = self._store.get_card_type_overrides_for_page("page-a")
        self.assertEqual(overrides, {})

    def test_multiple_overrides_for_one_page(self) -> None:
        self._store.set_card_type_override("page-a", "block-1", "input")
        self._store.set_card_type_override("page-a", "block-2", "basic_reversed")
        overrides = self._store.get_card_type_overrides_for_page("page-a")
        self.assertEqual(overrides["block-1"], "input")
        self.assertEqual(overrides["block-2"], "basic_reversed")

    def test_deleting_page_cascades_overrides(self) -> None:
        self._store.set_card_type_override("page-a", "block-1", "input")
        self._store.set_card_type_override("page-b", "block-2", "basic_reversed")

        connection = self._db.connect()
        try:
            connection.execute("DELETE FROM pages WHERE notion_page_id = ?", ("page-a",))
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(self._store.get_card_type_overrides_for_page("page-a"), {})
        self.assertEqual(self._store.get_card_type_overrides_for_page("page-b"), {"block-2": "basic_reversed"})

    def test_clear_card_type_overrides_for_page(self) -> None:
        self._store.set_card_type_override("page-a", "block-1", "input")
        self._store.set_card_type_override("page-a", "block-2", "basic_reversed")
        self._store.set_card_type_override("page-b", "block-3", "input")

        self._store.clear_card_type_overrides_for_page("page-a")

        self.assertEqual(self._store.get_card_type_overrides_for_page("page-a"), {})
        self.assertEqual(self._store.get_card_type_overrides_for_page("page-b"), {"block-3": "input"})
