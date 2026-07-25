"""Persistence helpers for cards-table metadata used by the Cards tab."""

from __future__ import annotations

import sqlite3

from .card_types import BASIC
from .db import Database


class CardsStore:
    """Read/write helpers for the `cards` table UI metadata fields."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_excluded_block_ids_for_page(self, page_id: str) -> set[str]:
        """Return excluded block ids for one page."""
        connection = self._db.connect()
        try:
            rows = connection.execute(
                """
                SELECT notion_block_id
                FROM cards
                WHERE notion_page_id = ? AND excluded = 1
                """,
                (page_id,),
            ).fetchall()
        finally:
            connection.close()

        return {str(row["notion_block_id"]) for row in rows}

    def set_card_excluded(self, page_id: str, block_id: str, excluded: bool) -> None:
        """Persist excluded state for one block id, creating a placeholder row when needed."""
        connection = self._db.connect()
        try:
            existing_row = connection.execute(
                """
                SELECT notion_block_id
                FROM cards
                WHERE notion_block_id = ?
                """,
                (block_id,),
            ).fetchone()

            if existing_row is None:
                # Placeholder rows let users pre-exclude toggles before first sync.
                connection.execute(
                    """
                    INSERT INTO cards (
                        notion_block_id,
                        notion_page_id,
                        anki_note_id,
                        card_type,
                        content_hash,
                        last_seen_notion_edit_time,
                        last_synced_at,
                        excluded
                    )
                    VALUES (?, ?, NULL, ?, '', NULL, NULL, ?)
                    """,
                    (block_id, page_id, BASIC, 1 if excluded else 0),
                )
            else:
                connection.execute(
                    """
                    UPDATE cards
                    SET notion_page_id = ?, excluded = ?
                    WHERE notion_block_id = ?
                    """,
                    (page_id, 1 if excluded else 0, block_id),
                )

            if not excluded:
                # Source hashes may have advanced for other cards while this
                # card was excluded. Invalidate both selective gates so the
                # newly eligible source is compared with its Anki payload.
                connection.execute(
                    """
                    DELETE FROM notion_toggle_snapshots
                    WHERE notion_page_id = ? AND notion_block_id = ?
                    """,
                    (page_id, block_id),
                )
                connection.execute(
                    """
                    UPDATE pages
                    SET content_hash = NULL
                    WHERE notion_page_id = ?
                    """,
                    (page_id,),
                )

            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()
