"""Tests for database schema migrations."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "anki_notion_integration"))

from db import Database


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
        finally:
            connection.close()

    def test_initialize_upgrades_existing_version_1_database(self) -> None:
        # Create a version-1 shape database to simulate a real user upgrade.
        connection = sqlite3.connect(self._db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE pages (
                    notion_page_id TEXT PRIMARY KEY,
                    anki_deck_name TEXT NOT NULL,
                    sync_enabled INTEGER NOT NULL DEFAULT 1,
                    last_synced_at TEXT
                );

                INSERT INTO schema_migrations (version) VALUES (1);
                """
            )
            connection.commit()
        finally:
            connection.close()

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

            latest_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            self.assertEqual(latest_version, 4)
        finally:
            connection.close()
