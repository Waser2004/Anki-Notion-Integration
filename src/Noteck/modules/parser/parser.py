"""Parse Notion block trees into deterministic card payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import html
import re
from typing import Any, Collection, Iterable, Mapping

from ..card_types import BASIC, normalize_default_selectable_card_type
from ..cards import MODEL_NAME_BASIC
from .basic_card_parser import BasicCardParser
from .cloze_card_parser import ClozeCardParser
from ..notion_client import NotionBlock
from .renderer import (
    _block_payload,
    _block_rich_text,
    _extract_caption_items,
    _extract_image_url,
    _rich_text_to_plain,
    render_blocks,
    render_rich_text,
)

@dataclass(frozen=True)
class ToggleCardPayload:
    """Represents one parsed Notion card payload."""

    notion_page_id:   str
    notion_block_id:  str
    front_html:       str            = ""
    back_html:        str            = ""
    card_type:        str            = BASIC
    model_name:       str            = MODEL_NAME_BASIC
    fields:           dict[str, str] = field(default_factory=dict)
    content_hash:     str            = ""
    last_edited_time: str | None     = None


@dataclass(frozen=True)
class ImageOcclusionCandidate:
    """Represents one image candidate for Image Occlusion workflow."""

    notion_block_id: str
    image_url:       str
    caption_html:    str
    caption_plain:   str


def extract_root_toggle_blocks(blocks: Iterable[NotionBlock]) -> list[NotionBlock]:
    """Return only top-level toggle blocks."""
    return [block for block in blocks if block.block_type == "toggle"]


def parse_page_to_cards(
    page_id:                  str,
    blocks:                   Iterable[NotionBlock],
    *,
    default_card_type:        str = BASIC,
    card_type_overrides:      Mapping[str, str] | None = None,
    enable_cloze:             bool = False,
    enable_gray_toggle_cloze: bool = True,
    cloze_marker_colors:      Collection[str] | None = None,
    include_block_ids:        Collection[str] | None = None,
) -> list[ToggleCardPayload]:
    """Coordinate the focused card parser services for one Notion page."""
    resolved_default_card_type           = normalize_default_selectable_card_type(default_card_type)
    normalized_include_block_ids         = {str(block_id) for block_id in include_block_ids} if include_block_ids else None
    top_level_blocks                     = list(blocks)
    normalized_overrides: dict[str, str] = {}

    if card_type_overrides:
        normalized_overrides = {
            str(block_id): normalize_default_selectable_card_type(card_type)
            for block_id, card_type in card_type_overrides.items()
        }
    
    basic_parser = BasicCardParser()
    cloze_parser = ClozeCardParser(cloze_marker_colors)
    payloads: list[ToggleCardPayload] = []

    # Parse top-level toggles
    for block in extract_root_toggle_blocks(top_level_blocks):
        # Skip excluded/non-target blocks before rendering payload fields.
        if normalized_include_block_ids is not None and block.block_id not in normalized_include_block_ids:
            continue
        
        # Recognized cloze toggles are parsed only when the single cloze option is enabled.
        is_cloze_toggle = cloze_parser.is_advanced_container(
            block,
            enable_gray_toggle_cloze=enable_gray_toggle_cloze,
        )
        if is_cloze_toggle:
            if enable_cloze:
                payloads.append(cloze_parser.parse_advanced(page_id, block))
            
            continue

        # Apply a card-specific card type override and parse the toggle with the selected non-cloze card type.
        resolved_card_type = normalized_overrides.get(block.block_id, resolved_default_card_type)
        payloads.append(basic_parser.parse(page_id, block, resolved_card_type))

    # parse top-level paragraphs for cloze
    if enable_cloze:
        payloads.extend(
            cloze_parser.parse_top_level_paragraphs(
                page_id,
                top_level_blocks,
                include_block_ids=normalized_include_block_ids,
            )
        )

    return payloads


def normalize_typed_answer(value: str) -> str:
    """Normalize a text/HTML answer for stable typed-answer matching."""
    without_tags   = re.sub(r"<[^>]+>", " ", value or "") # Remove HTML tags
    decoded        = html.unescape(without_tags).lower()  # Decode HTML entities and lowercase
    stripped_punct = re.sub(r"[\W_]+", " ", decoded)      # Replace non-alphanumeric characters with spaces

    return " ".join(stripped_punct.split())


def _raw_text_from_blocks(blocks: Iterable[NotionBlock]) -> str:
    """Extract deterministic raw text from blocks while preserving equation syntax."""
    parts: list[str] = []
    for block in blocks:
        # parse blocks own text
        own_text = _raw_text_from_single_block(block)
        if own_text:
            parts.append(own_text)
        
        # parse blocks children text
        if block.children:
            child_text = _raw_text_from_blocks(block.children)
            if child_text:
                parts.append(child_text)

    return "\n".join(parts)


def _compute_payload_content_hash(
    *,
    page_id: str,
    block_id: str,
    card_type: str,
    model_name: str,
    fields: dict[str, str],
) -> str:
    """Compute a deterministic content hash for any typed payload."""
    ordered_fields = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    payload = f"{page_id}\n{block_id}\n{card_type}\n{model_name}\n{ordered_fields}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _as_optional_string(value: Any) -> str | None:
    """Return a non-empty string value or ``None``."""
    return value if isinstance(value, str) and value else None


def _raw_text_from_single_block(block: NotionBlock) -> str:
    """Extract the plain-text content directly represented by one block."""
    if block.block_type == "equation":
        payload    = _block_payload(block)
        expression = payload.get("expression")

        if isinstance(expression, str):
            return expression
        return ""

    if block.block_type == "table_row":
        payload = _block_payload(block)
        cells   = payload.get("cells")

        if not isinstance(cells, list):
            return ""
        
        # Extract plain text for each column cell
        cell_parts: list[str] = []
        for cell in cells:
            if not isinstance(cell, list):
                continue

            rich_text_items = [item for item in cell if isinstance(item, dict)]
            plain           = _rich_text_to_plain(rich_text_items)
            if plain:
                cell_parts.append(plain)

        return " | ".join(cell_parts)

    if block.block_type == "image":
        payload       = _block_payload(block)
        caption_items = _extract_caption_items(payload.get("caption"))

        return _rich_text_to_plain(caption_items)

    return _rich_text_to_plain(_block_rich_text(block))


def collect_image_occlusion_candidates(blocks: Iterable[NotionBlock]) -> list[ImageOcclusionCandidate]:
    """Collect image blocks outside of toggle trees for image occlusion."""
    candidates: list[ImageOcclusionCandidate] = []

    def walk(items: Iterable[NotionBlock], *, inside_toggle: bool) -> None:
        for block in items:
            next_inside_toggle = inside_toggle or block.block_type == "toggle"
            if block.block_type == "image" and not next_inside_toggle:
                payload = _block_payload(block)
                image_url = _extract_image_url(payload)
                if image_url:
                    caption_items = _extract_caption_items(payload.get("caption"))
                    candidates.append(
                        ImageOcclusionCandidate(
                            notion_block_id=block.block_id,
                            image_url=image_url,
                            caption_html=render_rich_text(caption_items),
                            caption_plain=_rich_text_to_plain(caption_items),
                        )
                    )
            if block.children:
                walk(block.children, inside_toggle=next_inside_toggle)

    walk(list(blocks), inside_toggle=False)
    return candidates
