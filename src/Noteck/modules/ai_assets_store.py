"""Persistence helpers for cached AI variants and generated audio references."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from .db import Database


@dataclass(frozen=True)
class CardAiAsset:
    """One cached AI asset row keyed by block id and direction."""

    notion_block_id: str
    direction: str
    source_hash: str
    variant_settings_hash: str
    tts_settings_hash: str
    variants: list[str]
    audio_files: list[str]


class CardAiAssetsStore:
    """Read/write helpers for the `card_ai_assets` cache table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_asset(self, notion_block_id: str, direction: str) -> CardAiAsset | None:
        """Return one cached asset entry when available."""
        connection = self._db.connect()
        try:
            row = connection.execute(
                """
                SELECT
                    notion_block_id,
                    direction,
                    source_hash,
                    variant_settings_hash,
                    tts_settings_hash,
                    variants_json,
                    audio_files_json
                FROM card_ai_assets
                WHERE notion_block_id = ? AND direction = ?
                """,
                (notion_block_id, direction),
            ).fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        return CardAiAsset(
            notion_block_id=str(row["notion_block_id"]),
            direction=str(row["direction"]),
            source_hash=str(row["source_hash"] or ""),
            variant_settings_hash=str(row["variant_settings_hash"] or ""),
            tts_settings_hash=str(row["tts_settings_hash"] or ""),
            variants=self._decode_list(row["variants_json"]),
            audio_files=self._decode_list(row["audio_files_json"]),
        )

    def upsert_asset(
        self,
        *,
        notion_block_id: str,
        direction: str,
        source_hash: str,
        variant_settings_hash: str,
        tts_settings_hash: str,
        variants: list[str],
        audio_files: list[str],
    ) -> None:
        """Persist one AI asset cache row."""
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO card_ai_assets (
                    notion_block_id,
                    direction,
                    source_hash,
                    variant_settings_hash,
                    tts_settings_hash,
                    variants_json,
                    audio_files_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(notion_block_id, direction) DO UPDATE SET
                    source_hash = excluded.source_hash,
                    variant_settings_hash = excluded.variant_settings_hash,
                    tts_settings_hash = excluded.tts_settings_hash,
                    variants_json = excluded.variants_json,
                    audio_files_json = excluded.audio_files_json,
                    updated_at = datetime('now')
                """,
                (
                    notion_block_id,
                    direction,
                    source_hash,
                    variant_settings_hash,
                    tts_settings_hash,
                    json.dumps(variants, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(audio_files, ensure_ascii=True, separators=(",", ":")),
                ),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_assets_for_block(self, notion_block_id: str) -> None:
        """Delete cached assets for one Notion block id."""
        connection = self._db.connect()
        try:
            connection.execute(
                """
                DELETE FROM card_ai_assets
                WHERE notion_block_id = ?
                """,
                (notion_block_id,),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _decode_list(raw_json: object) -> list[str]:
        """Parse one JSON list field into a safe list of strings."""
        if not isinstance(raw_json, str) or not raw_json:
            return []
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, str)]
