"""Focused parser service for toggle-based Anki card types."""

from __future__ import annotations

from typing import Any

from . import parser as shared
from ..card_types import BASIC_REVERSED, INPUT
from ..cards import (
    MODEL_NAME_BASIC,
    MODEL_NAME_BASIC_REVERSED,
    MODEL_NAME_INPUT,
    NOTION_CARD_BACKGROUND_FIELD,
    NOTION_PAGE_ID_FIELD,
)
from ..notion_client import NotionBlock

class BasicCardParser:
    """Build Basic, Basic+Reversed, and Input payloads from Notion toggles."""

    def parse(self, page_id: str, block: NotionBlock, card_type: str) -> shared.ToggleCardPayload:
        """Parse one top-level toggle using the selected non-cloze card type."""
        front_html = self._render_toggle_front(block)
        back_html  = shared.render_blocks(block.children)
        if not shared._rich_text_to_plain(shared._block_rich_text(block)).strip():
            raise ValueError("empty_toggle_title")
        if not shared._has_usable_card_content(back_html):
            raise ValueError("empty_toggle_content")

        fields = self._build_fields(
            page_id,
            block.block_id,
            front_html,
            back_html,
            block.children,
            card_type,
            shared._block_background_color(block),
        )
        model_name = self.model_name_for(card_type)

        return shared.ToggleCardPayload(
            notion_page_id   = page_id,
            notion_block_id  = block.block_id,
            front_html       = front_html,
            back_html        = back_html,
            card_type        = card_type,
            model_name       = model_name,
            fields           = fields,
            content_hash     = shared._compute_payload_content_hash(
                page_id=page_id,
                block_id=block.block_id,
                card_type=card_type,
                model_name=model_name,
                fields=fields,
            ),
            last_edited_time = shared._as_optional_string(block.raw.get("last_edited_time")),
        )

    @staticmethod
    def model_name_for(card_type: str) -> str:
        """Return the registered Anki model name for a canonical card type."""
        if card_type == BASIC_REVERSED:
            return MODEL_NAME_BASIC_REVERSED
        if card_type == INPUT:
            return MODEL_NAME_INPUT
        
        return MODEL_NAME_BASIC

    @staticmethod
    def _render_toggle_front(block: NotionBlock) -> str:
        """Render a toggle title through the shared block renderer."""
        from . import parser as shared

        front_block = NotionBlock(
            block_id     = block.block_id,
            block_type   = "paragraph",
            has_children = False,
            parent_id    = block.parent_id,
            parent_type  = block.parent_type,
            raw          = {
                "id": block.block_id,
                "type": "paragraph",
                # Root backgrounds belong to the card surface; only foreground
                # colors style the title rendered inside that surface.
                "paragraph": {
                    "rich_text": shared._block_rich_text(block),
                    "color": shared._block_foreground_color(block),
                },
            },
            children     = (),
        )
        return shared.render_blocks([front_block])

    @staticmethod
    def _build_fields(
        page_id:     str,
        block_id:    str,
        front_html:  str,
        back_html:   str,
        back_blocks: Any,
        card_type:   str,
        card_background: str,
    ) -> dict[str, str]:
        """Build fields required by the selected Anki note type."""
        if card_type == INPUT:
            return {
                "Front": front_html,
                "Back": back_html,
                "Expected Answer": shared._raw_text_from_blocks(back_blocks),
                "Notion Block ID": block_id,
                NOTION_PAGE_ID_FIELD: page_id,
                NOTION_CARD_BACKGROUND_FIELD: card_background,
            }
        
        return {
            "Front": front_html,
            "Back": back_html,
            "Notion Block ID": block_id,
            NOTION_PAGE_ID_FIELD: page_id,
            NOTION_CARD_BACKGROUND_FIELD: card_background,
        }
