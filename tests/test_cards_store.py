"""Tests for cards-table persistence helpers used by Cards UI."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from anki_notion_integration.cards_store import CardsStore
from anki_notion_integration.db import Database


class CardsStoreTests(unittest.TestCase):
    """Validate excluded-state persistence and placeholder row behavior."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db = Database(Path(self._temp_dir.name) / "cards_store.db")
        self._db.initialize()
        self._store = CardsStore(self._db)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO pages (notion_page_id, anki_deck_name, sync_enabled)
                VALUES (?, ?, ?)
                """,
                ("page-a", "Notion::A", 1),
            )
            connection.commit()
        finally:
            connection.close()

    def test_set_card_excluded_creates_placeholder_row_when_missing(self) -> None:
        self._store.set_card_excluded("page-a", "block-1", True)

        connection = self._db.connect()
        try:
            row = connection.execute(
                """
                SELECT notion_page_id, card_type, content_hash, excluded
                FROM cards
                WHERE notion_block_id = ?
                """,
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()

        self.assertIsNotNone(row)
        self.assertEqual(str(row["notion_page_id"]), "page-a")
        self.assertEqual(str(row["card_type"]), "basic")
        self.assertEqual(str(row["content_hash"]), "")
        self.assertEqual(int(row["excluded"]), 1)

    def test_set_card_excluded_updates_existing_row(self) -> None:
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id,
                    notion_page_id,
                    anki_note_id,
                    card_type,
                    content_hash,
                    excluded
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("block-1", "page-a", None, "input", "hash", 0),
            )
            connection.commit()
        finally:
            connection.close()

        self._store.set_card_excluded("page-a", "block-1", True)
        self.assertEqual(self._store.get_excluded_block_ids_for_page("page-a"), {"block-1"})

        self._store.set_card_excluded("page-a", "block-1", False)
        self.assertEqual(self._store.get_excluded_block_ids_for_page("page-a"), set())
