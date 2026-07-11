"""Focused parser service for toggle-based Anki card types."""

from __future__ import annotations

from typing import Any

from . import parser as shared
from ..card_types import BASIC_REVERSED, INPUT
from ..cards import MODEL_NAME_BASIC, MODEL_NAME_BASIC_REVERSED, MODEL_NAME_INPUT
from ..notion_client import NotionBlock

class BasicCardParser:
    """Build Basic, Basic+Reversed, and Input payloads from Notion toggles."""

    def parse(self, page_id: str, block: NotionBlock, card_type: str) -> shared.ToggleCardPayload:
        """Parse one top-level toggle using the selected non-cloze card type."""
        front_html = self._render_toggle_front(block)
        back_html  = shared.render_blocks(block.children)
        fields     = self._build_fields(block.block_id, front_html, back_html, block.children, card_type)
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
                "paragraph": {"rich_text": shared._block_rich_text(block)},
            },
            children     = (),
        )
        return shared.render_blocks([front_block])

    @staticmethod
    def _build_fields(
        block_id:    str,
        front_html:  str,
        back_html:   str,
        back_blocks: Any,
        card_type:   str,
    ) -> dict[str, str]:
        """Build fields required by the selected Anki note type."""
        if card_type == INPUT:
            return {
                "Front": front_html,
                "Back": back_html,
                "Expected Answer": shared._raw_text_from_blocks(back_blocks),
                "Notion Block ID": block_id,
            }
        
        return {"Front": front_html, "Back": back_html, "Notion Block ID": block_id}
