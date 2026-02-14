"""Tests for database schema migrations."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.db import Database


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    """Return all column names for a table."""
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


class DatabaseMigrationTests(unittest.TestCase):
    """Validate migrations for the pages table schema updates."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db_path = Path(self._temp_dir.name) / "migrations.db"

    def test_initialize_creates_latest_pages_columns(self) -> None:
        db = Database(self._db_path)
        db.initialize()

        connection = sqlite3.connect(self._db_path)
        try:
            page_columns = _column_names(connection, "pages")
            self.assertIn("anki_deck_id", page_columns)
            self.assertIn("content_hash", page_columns)
            self.assertIn("last_seen_notion_edit_time", page_columns)
            self.assertIn("parent_id", page_columns)
            self.assertIn("parent_type", page_columns)
            self.assertIn("default_card_type", page_columns)
            override_columns = _column_names(connection, "card_type_overrides")
            self.assertIn("notion_block_id", override_columns)
            self.assertIn("notion_page_id", override_columns)
            self.assertIn("card_type", override_columns)
            self.assertIn("updated_at", override_columns)
            ai_asset_columns = _column_names(connection, "card_ai_assets")
            self.assertIn("notion_block_id", ai_asset_columns)
            self.assertIn("direction", ai_asset_columns)
            self.assertIn("source_hash", ai_asset_columns)
            self.assertIn("variant_settings_hash", ai_asset_columns)
            self.assertIn("tts_settings_hash", ai_asset_columns)
            self.assertIn("variants_json", ai_asset_columns)
            self.assertIn("audio_files_json", ai_asset_columns)
        finally:
            connection.close()

    def test_initialize_records_schema_version_2(self) -> None:
        db = Database(self._db_path)
        db.initialize()

        connection = sqlite3.connect(self._db_path)
        try:
            latest_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            self.assertEqual(latest_version, 2)
        finally:
            connection.close()

    def test_initialize_is_idempotent_for_single_baseline_migration(self) -> None:
        db = Database(self._db_path)
        db.initialize()
        db.initialize()

        connection = sqlite3.connect(self._db_path)
        try:
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual([int(row[0]) for row in versions], [1, 2])

            page_columns = _column_names(connection, "pages")
            self.assertIn("anki_deck_id", page_columns)
            self.assertIn("content_hash", page_columns)
            self.assertIn("last_seen_notion_edit_time", page_columns)
            self.assertIn("parent_id", page_columns)
            self.assertIn("parent_type", page_columns)
            self.assertIn("default_card_type", page_columns)
        finally:
            connection.close()
