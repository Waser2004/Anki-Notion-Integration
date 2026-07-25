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
            snapshot_columns = _column_names(connection, "notion_toggle_snapshots")
            self.assertEqual(
                snapshot_columns,
                {"notion_block_id", "notion_page_id", "source_hash", "updated_at"},
            )
        finally:
            connection.close()

    def test_initialize_records_latest_schema_version(self) -> None:
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

    def test_initialize_is_idempotent_for_all_migrations(self) -> None:
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
            snapshot_columns = _column_names(connection, "notion_toggle_snapshots")
            self.assertIn("source_hash", snapshot_columns)
        finally:
            connection.close()

    def test_version_one_database_is_upgraded_with_toggle_snapshots(self) -> None:
        """The only released predecessor receives the version-two snapshot table."""
        connection = sqlite3.connect(self._db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO schema_migrations (version) VALUES (1);
                CREATE TABLE pages (
                    notion_page_id TEXT PRIMARY KEY,
                    anki_deck_name TEXT NOT NULL,
                    sync_enabled INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

        Database(self._db_path).initialize()

        connection = sqlite3.connect(self._db_path)
        try:
            latest_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            snapshot_columns = _column_names(
                connection,
                "notion_toggle_snapshots",
            )
        finally:
            connection.close()
        self.assertEqual(latest_version, 2)
        self.assertIn("source_hash", snapshot_columns)
