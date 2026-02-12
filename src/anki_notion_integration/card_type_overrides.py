"""Persistence helpers for per-card type overrides."""

from __future__ import annotations

import sqlite3

from .card_types import normalize_default_selectable_card_type
from .db import Database


class CardTypeOverrideStore:
    """Read/write per-card type overrides in `card_type_overrides`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_card_type_overrides_for_page(self, page_id: str) -> dict[str, str]:
        """Return `notion_block_id -> card_type` overrides for one page."""
        connection = self._db.connect()
        try:
            rows = connection.execute(
                """
                SELECT notion_block_id, card_type
                FROM card_type_overrides
                WHERE notion_page_id = ?
                """,
                (page_id,),
            ).fetchall()
        finally:
            connection.close()

        return {
            str(row["notion_block_id"]): normalize_default_selectable_card_type(str(row["card_type"]))
            for row in rows
        }

    def set_card_type_override(self, page_id: str, block_id: str, card_type: str | None) -> None:
        """Persist or clear one per-card override."""
        connection = self._db.connect()
        try:
            if card_type is None:
                connection.execute(
                    """
                    DELETE FROM card_type_overrides
                    WHERE notion_block_id = ?
                    """,
                    (block_id,),
                )
                connection.commit()
                return

            normalized = normalize_default_selectable_card_type(card_type)
            connection.execute(
                """
                INSERT INTO card_type_overrides (notion_block_id, notion_page_id, card_type, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(notion_block_id) DO UPDATE SET
                    notion_page_id = excluded.notion_page_id,
                    card_type = excluded.card_type,
                    updated_at = datetime('now')
                """,
                (block_id, page_id, normalized),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def clear_card_type_overrides_for_page(self, page_id: str) -> None:
        """Clear all per-card type overrides for one page id."""
        connection = self._db.connect()
        try:
            connection.execute(
                """
                DELETE FROM card_type_overrides
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
