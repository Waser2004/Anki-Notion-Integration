"""SQLite persistence layer for Noteck."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable, Optional


@dataclass(frozen=True)
class Migration:
    """Represents a schema migration with ordered SQL statements."""

    version: int
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    # First public release baseline:
    # keep one canonical schema migration for new installs.
    # Compatibility with pre-release schema variants is intentionally unsupported.
    Migration(
        version=1,
        statements=(
            """
            CREATE TABLE IF NOT EXISTS pages (
                notion_page_id TEXT PRIMARY KEY,
                anki_deck_name TEXT NOT NULL,
                sync_enabled INTEGER NOT NULL DEFAULT 1,
                last_synced_at TEXT,
                anki_deck_id INTEGER,
                content_hash TEXT,
                last_seen_notion_edit_time TEXT,
                parent_id TEXT,
                parent_type TEXT,
                default_card_type TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cards (
                notion_block_id TEXT PRIMARY KEY,
                notion_page_id TEXT NOT NULL,
                anki_note_id INTEGER UNIQUE,
                card_type TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                last_seen_notion_edit_time TEXT,
                last_synced_at TEXT,
                excluded INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(notion_page_id)
                    REFERENCES pages(notion_page_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS card_type_overrides (
                notion_block_id TEXT PRIMARY KEY,
                notion_page_id TEXT NOT NULL,
                card_type TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(notion_page_id)
                    REFERENCES pages(notion_page_id)
                    ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_cards_page ON cards(notion_page_id)",
            "CREATE INDEX IF NOT EXISTS idx_card_type_overrides_page ON card_type_overrides(notion_page_id)",
        ),
    ),
    Migration(
        version=2,
        statements=(
            """
            CREATE TABLE IF NOT EXISTS notion_toggle_snapshots (
                notion_block_id TEXT PRIMARY KEY,
                notion_page_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(notion_page_id)
                    REFERENCES pages(notion_page_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_notion_toggle_snapshots_page
            ON notion_toggle_snapshots(notion_page_id)
            """,
        ),
    ),
    Migration(
        version=3,
        statements=(
            """
            CREATE TABLE IF NOT EXISTS notion_card_controls (
                notion_block_id TEXT PRIMARY KEY,
                notion_page_id TEXT NOT NULL REFERENCES pages(notion_page_id) ON DELETE CASCADE,
                excluded INTEGER NOT NULL DEFAULT 0,
                filtered INTEGER NOT NULL DEFAULT 0,
                card_type TEXT,
                warning TEXT NOT NULL DEFAULT '',
                marker_icons TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_notion_card_controls_page ON notion_card_controls(notion_page_id)",
        ),
    ),
)


class Database:
    """Provides access to the local SQLite database and schema setup."""

    def __init__(self, db_path: str | Path) -> None:
        """Create a database helper bound to the given path."""
        self._db_path = Path(db_path)

    @property
    def path(self) -> Path:
        """Return the resolved database path."""
        return self._db_path

    def connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with foreign keys and row access by name."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")

        return connection

    def initialize(self) -> None:
        """Create schema and apply any pending migrations."""
        connection = self.connect()
        try:
            self._apply_migrations(connection)
            connection.commit()
        finally:
            connection.close()

    def get_setting(self, key: str) -> Optional[str]:
        """Return a settings value by key, or None if missing."""
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            connection.close()
        
        return None if row is None else row["value"]

    def set_setting(self, key: str, value: str) -> None:
        """Insert or update a settings entry."""
        connection = self.connect()
        try:
            connection.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = datetime('now')
                """,
                (key, value),
            )
            connection.commit()
        finally:
            connection.close()

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        """Apply any migrations not yet recorded in schema_migrations."""
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        rows = connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
        applied_versions = {int(row["version"]) for row in rows}

        # apply missing migrations even when a newer version was recorded by another build
        for migration in self._pending_migrations(applied_versions):
            for statement in migration.statements:
                connection.execute(statement)
                
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (migration.version,),
            )

    def _pending_migrations(self, applied_versions: set[int]) -> Iterable[Migration]:
        """Yield known migrations that have not been recorded yet."""
        return tuple(migration for migration in MIGRATIONS if migration.version not in applied_versions)
