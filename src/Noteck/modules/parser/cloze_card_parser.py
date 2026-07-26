"""Cloze-card parser and validator for Anki-compatible payloads."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from . import parser as shared
from ..card_types import CLOZE
from ..cards import MODEL_NAME_CLOZE, NOTION_CARD_BACKGROUND_FIELD
from ..notion_client import NotionBlock
from .renderer import (
    TABLE_CELL_CLOZE_COLOR_KEY,
    _render_rich_text_item,
    render_blocks_with_renderer,
    render_rich_text,
)


_CLOZE_CONTAINER_PREFIX_RE = re.compile(r"^\s*(?:cloze\s*:|\[cloze\])(?:\s|$)", re.IGNORECASE) # search for "cloze:" or "[cloze]" in a toggle title
_EXTRA_PREFIX_RE           = re.compile(r"^\s*(?:extra\s*:|\[extra\])(?:\s|$)", re.IGNORECASE) # search for "extra:" or "[extra]" in a toggle title and top level paragraph

_CLOZE_NUMBERS = {
    "yellow": 1,
    "green":  2,
    "blue":   3,
    "purple": 4,
    "pink":   5,
    "orange": 6,
    "red":    7,
    "brown":  8
}
CLOZE_MARKER_COLORS = tuple(_CLOZE_NUMBERS)

_CLOZE_START_RE    = re.compile(r"\{\{c([1-9]\d*)::")
_HTML_TAG_RE       = re.compile(r"<[^>]*>")
_MAX_NESTING_DEPTH = 3
_COLORABLE_RENDERED_BLOCK_TYPES = frozenset(
    {
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "callout",
        "toggle",
    }
)
# This private payload/item key exists only on temporary copies prepared for
# rendering a colored callout.  It lets the parent callout hide every textual
# descendant without mutating the source Notion block or its original styling.
_INHERITED_CALLOUT_CLOZE_COLOR_KEY = "_noteck_inherited_callout_cloze_color"
_CALLOUT_TEXTUAL_DESCENDANT_BLOCK_TYPES = _COLORABLE_RENDERED_BLOCK_TYPES | frozenset(
    {"code", "equation", "image"}
)


def paragraph_has_cloze_marker(
    raw_payload: dict[str, Any],
    marker_colors: Iterable[str],
) -> bool:
    """Return whether a raw paragraph has a configured inline cloze marker."""
    paragraph_payload = raw_payload.get("paragraph")
    if not isinstance(paragraph_payload, dict):
        return False

    selected_colors = {
        str(color).strip().lower().removesuffix("_background")
        for color in marker_colors
    }
    rich_text = paragraph_payload.get("rich_text")
    if not isinstance(rich_text, list):
        return False

    for item in rich_text:
        if not isinstance(item, dict):
            continue
        annotations = item.get("annotations")
        if not isinstance(annotations, dict):
            continue
        color = str(annotations.get("color") or "").strip().lower()
        background_color = str(
            annotations.get("background_color") or ""
        ).strip().lower()
        if (
            color.endswith("_background")
            and color.removesuffix("_background") in selected_colors
        ) or background_color.removesuffix("_background") in selected_colors:
            return True
    return False


@dataclass(frozen=True)
class ClozeValidationResult:
    """Result of checking one payload against Anki cloze field requirements."""
    is_valid: bool
    errors:   tuple[str, ...] = ()


@dataclass(frozen=True)
class _AdvancedExtraSection:
    """One ordinary-rendered Extra group with an optional toggle color scope."""
    blocks: tuple[NotionBlock, ...]
    color:  str = "default"


class ClozeCardParser:
    """Build normal and advanced cloze payloads from Notion blocks."""

    def __init__(self, marker_colors: Iterable[str] | None = None) -> None:
        """Limit cloze conversion to the selected marker colors."""
        selected = CLOZE_MARKER_COLORS if marker_colors is None else marker_colors
        self._marker_colors = {
            str(color).strip().lower().removesuffix("_background")
            for color in selected
        }

    # Normal paragraph clozes -------------------------------------------------

    def parse_top_level_paragraphs(self, page_id: str, blocks: list[NotionBlock], *, include_block_ids: set[str] | None = None) -> list["shared.ToggleCardPayload"]:
        """Parse marked top-level paragraphs and consume an adjacent ``Extra:`` paragraph."""
        payloads:         list["shared.ToggleCardPayload"] = []
        consumed_indices: set[int] = set()

        for index, block in enumerate(blocks):
            if index in consumed_indices or block.block_type != "paragraph":
                continue
            if include_block_ids is not None and block.block_id not in include_block_ids:
                continue
            
            # Top-level paragraphs require an inline marker; a block-level
            # background alone has no context and is not a useful card.
            rich_text = self._rich_text(block)
            if not any(self._cloze_number(item) is not None for item in rich_text):
                continue
            
            # Convert only the marked rich-text runs. Block-level colors remain
            # available for descendants of advanced cloze containers.
            text = self._render_top_level_cloze_text(block, rich_text)
            if not text.strip():
                continue

            # Check if the next paragraph is an "Extra:" paragraph and consume it if so.
            extra = ""
            if index + 1 < len(blocks) and self._is_extra_paragraph(blocks[index + 1]):
                extra = self._render_extra_without_prefix(blocks[index + 1])
                consumed_indices.add(index + 1)
            
            payloads.append(self._payload_for_fields(page_id, block, {"Text": text, "Extra": extra, "Notion Block ID": block.block_id}))
        
        return payloads

    def _is_extra_paragraph(self, block: NotionBlock) -> bool:
        return block.block_type == "paragraph" and _EXTRA_PREFIX_RE.search(self._plain_text(self._rich_text(block))) is not None

    def _render_extra_without_prefix(self, block: NotionBlock) -> str:
        stripped_block = self._without_extra_prefix(block)

        has_block_color = bool(
            shared._block_background_color(stripped_block)
            or shared._block_foreground_color(stripped_block) != "default"
        )
        if has_block_color:
            return shared.render_blocks([stripped_block])
        
        return render_rich_text(self._rich_text(stripped_block))

    def _render_top_level_cloze_text(
        self,
        block: NotionBlock,
        rich_text: Iterable[dict[str, Any]],
    ) -> str:
        """Render a paragraph cloze, adding a semantic wrapper only for foreground color."""
        text = self._rich_text_to_cloze_text(rich_text)
        foreground_color = shared._block_foreground_color(block)
        if foreground_color == "default":
            return text

        # Root backgrounds belong to the card surface. Render only a validated
        # foreground color on the visible paragraph text, matching basic titles.
        raw     = dict(block.raw)
        payload = dict(self._payload(block))
        payload["color"] = foreground_color
        raw[block.block_type] = payload
        foreground_block = replace(block, raw=raw, has_children=False, children=())
        return render_blocks_with_renderer(
            [foreground_block],
            rich_text_renderer=self._rich_text_to_cloze_text,
        )

    def _rich_text_to_cloze_text(self, rich_text: Iterable[dict[str, Any]]) -> str:
        parts:  list[str]  = []  
        run:    list[str]  = []
        number: int | None = None

        def flush() -> None:
            if run:
                rendered = "".join(run)
                parts.append(f"{{{{c{number}::{rendered}}}}}" if number is not None else rendered)
                run.clear()

        for item in rich_text:
            item_number = self._cloze_number(item)
            fragment = (
                self._cloze_fragment(item)
                if item_number is not None or item.get("type") == "equation"
                else _render_rich_text_item(item)
            )
            if not fragment:
                continue
            if run and number != item_number:
                flush()

            number = item_number
            run.append(fragment)

        flush()
        return "".join(parts)

    # Advanced toggle clozes --------------------------------------------------

    def is_advanced_container(self, block: NotionBlock, *, enable_gray_toggle_cloze: bool = True) -> bool:
        """Return whether a toggle uses the advanced cloze convention."""
        if block.block_type != "toggle":
            return False

        # Check for the "[cloze]" prefix in the toggle title.
        title = self._plain_text(self._rich_text(block))
        if _CLOZE_CONTAINER_PREFIX_RE.search(title):
            return True

        # Check for the "gray_background" color in the toggle payload.
        color = self._payload(block).get("color")
        is_gray_toggle = self._normalize_background_color(color) == "gray" if isinstance(color, str) else False
        return enable_gray_toggle_cloze and is_gray_toggle

    def parse_advanced(self, page_id: str, block: NotionBlock) -> "shared.ToggleCardPayload":
        """Parse one advanced cloze toggle into an Anki cloze payload."""
        text_blocks, extra_sections = self._split_advanced_children(block.children)
        text_blocks = self._prepare_advanced_blocks(text_blocks)
        text = render_blocks_with_renderer(
            text_blocks,
            rich_text_renderer=self._rich_text_to_advanced_cloze_html,
            block_text_override=self._render_block_level_cloze_html,
            table_cell_override=self._render_table_cell_cloze_html,
        )
        fields = {
            "Text":            self._wrap_advanced_root_foreground(block, text),
            "Extra":           self._render_advanced_extra(extra_sections),
            "Notion Block ID": block.block_id,
        }
        return self._payload_for_fields(page_id, block, fields)

    def _wrap_advanced_root_foreground(self, block: NotionBlock, text: str) -> str:
        """Apply a root toggle foreground to its visible advanced-cloze content."""
        foreground_color = shared._block_foreground_color(block)
        if foreground_color == "default":
            return text

        # The advanced toggle title is intentionally hidden, so its validated
        # foreground becomes the semantic color scope for the visible Text field.
        return (
            '<div class="notion-cloze-root-foreground notion-block-color '
            f'notion-block-color-{foreground_color}">{text}</div>'
        )

    def _split_advanced_children(
        self,
        children: Iterable[NotionBlock],
    ) -> tuple[list[NotionBlock], list[_AdvancedExtraSection]]:
        """Separate advanced-cloze text from paragraph and toggle extras."""
        text_blocks:    list[NotionBlock]           = []
        extra_sections: list[_AdvancedExtraSection] = []

        for child in children:
            # Direct Extra paragraphs keep the established concise convention.
            if self._is_extra_paragraph(child):
                extra_sections.append(_AdvancedExtraSection((self._without_extra_prefix(child),)))
                continue

            # Extract nexted Extra toggles from the visible text content while preserving their color scope.
            text_block, nested_extras = self._extract_extra_toggles(child)
            if text_block is not None:
                text_blocks.append(text_block)
            extra_sections.extend(nested_extras)

        return text_blocks, extra_sections

    def _extract_extra_toggles(
        self,
        block: NotionBlock,
    ) -> tuple[NotionBlock | None, list[_AdvancedExtraSection]]:
        """Remove marked nested toggles from Text and return only their contents."""
        # if toggle is extra toggle extract foreground and background color and return
        if self._is_extra_toggle(block):
            background = shared._block_background_color(block)
            foreground = shared._block_foreground_color(block)
            color = background or foreground
            return None, [_AdvancedExtraSection(block.children, color)]

        # block has no children, return block and empty list for extra sections
        if not block.children:
            return block, []

        # recursively extract extra toggles from children and return block with retained children and extra sections
        retained_children: list[NotionBlock] = []
        extra_sections:    list[_AdvancedExtraSection] = []
        for child in block.children:
            retained_child, child_extras = self._extract_extra_toggles(child)
            if retained_child is not None:
                retained_children.append(retained_child)
            extra_sections.extend(child_extras)

        # Preserve any non-extra wrapper and its styling while removing the marked toggle subtree from the front-side content.
        retained_tuple = tuple(retained_children)
        return (
            replace(
                block,
                children     = retained_tuple,
                has_children = bool(retained_tuple),
            ),
            extra_sections,
        )

    def _render_advanced_extra(
        self,
        sections: Iterable[_AdvancedExtraSection],
    ) -> str:
        """Render Extra groups while retaining a marked toggle's block color."""
        parts: list[str] = []
        for section in sections:
            rendered = shared.render_blocks(section.blocks)
            if not rendered:
                continue
            if section.color == "default":
                parts.append(rendered)
                continue

            # apply block color classes to the rendered extra section
            classes = [
                "notion-cloze-extra-color",
                "notion-block-color",
                f"notion-block-color-{section.color}",
            ]
            if section.color.endswith("_background"):
                classes.append("notion-block-color-background")
            parts.append(f'<div class="{" ".join(classes)}">{rendered}</div>')

        return "".join(parts)

    def _is_extra_toggle(self, block: NotionBlock) -> bool:
        """Recognize nested Extra toggles by either supported title prefix."""
        if block.block_type != "toggle":
            return False
        
        return _EXTRA_PREFIX_RE.search(
            self._plain_text(self._rich_text(block)),
        ) is not None

    def _rich_text_to_advanced_cloze_html(self, rich_text: Iterable[dict[str, Any]]) -> str:
        parts:  list[str]  = []
        run:    list[str]  = []
        number: int | None = None

        def flush() -> None:
            if run:
                rendered = "".join(run)
                parts.append(f"{{{{c{number}::{rendered}}}}}" if number is not None else rendered)
                run.clear()
            
        for item in rich_text:
            # get cloze number and remove the color marker from the item if it exists
            item_number = self._cloze_number(item)
            render_item = self._remove_marker_color(item) if item_number is not None else item
            fragment    = _render_rich_text_item(render_item)
            if not fragment:
                continue
            if run and number != item_number:
                flush()

            number = item_number
            run.append(fragment)
        
        flush()
        return "".join(parts)

    def _render_block_level_cloze_html(self, block: NotionBlock) -> str | None:
        """Return one cloze marker for a configured background-colored block."""
        number = self._block_cloze_number(block)
        if number is None:
            return None

        if block.block_type == "equation":
            expression = self._payload(block).get("expression")
            expression_text = expression.strip() if isinstance(expression, str) else ""
            content = f"\\({html.escape(expression_text)}\\)" if expression_text else ""
        else:
            content = self._rich_text_to_html_without_marker_colors(self._block_cloze_rich_text(block))

        return self._cloze_markup(number, content)

    def _render_table_cell_cloze_html(
        self,
        cell_rich_text: list[dict[str, Any]],
    ) -> str | None:
        """Replace a fully colored table cell with one cloze marker."""
        number = self._table_cell_cloze_number(cell_rich_text)
        if number is None:
            return None

        content = self._rich_text_to_html_without_marker_colors(cell_rich_text)
        marker = self._cloze_markup(number, content)
        return marker or None

    def _rich_text_to_html_without_marker_colors(self, rich_text: Iterable[dict[str, Any]]) -> str:
        """Render complete block content without allowing inner marker colors to nest clozes."""
        rendered: list[str] = []
        for item in rich_text:
            render_item = self._remove_marker_color(item) if self._cloze_number(item) is not None else item
            rendered.append(_render_rich_text_item(render_item))
        return "".join(rendered)

    def _block_cloze_rich_text(self, block: NotionBlock) -> list[dict[str, Any]]:
        """Return the direct text a supported renderer displays for a marked block."""
        if block.block_type == "image":
            caption = self._payload(block).get("caption")
            return [item for item in caption if isinstance(item, dict)] if isinstance(caption, list) else []
        return self._rich_text(block)

    def _prepare_advanced_blocks(self, blocks: Iterable[NotionBlock]) -> list[NotionBlock]:
        """Clone marked-callout descendants so their structure survives while text is hidden."""
        return [self._prepare_advanced_block(block, inherited_callout_color=None) for block in blocks]

    def _prepare_advanced_block(
        self,
        block: NotionBlock,
        *,
        inherited_callout_color: str | None,
    ) -> NotionBlock:
        """Apply a marked callout's color to descendant text without changing the source tree."""
        explicit_block_color = self._explicit_block_cloze_color(block)
        # A nested, explicitly colored callout starts a new marker scope.  Its
        # own direct content and all of its descendants use that closer color.
        own_callout_color = explicit_block_color if block.block_type == "callout" else None
        effective_callout_color = own_callout_color or inherited_callout_color
        payload = self._payload(block)
        prepared_payload = payload
        payload_changed = False

        if (
            inherited_callout_color is not None
            and explicit_block_color is None
            and block.block_type in _CALLOUT_TEXTUAL_DESCENDANT_BLOCK_TYPES
        ):
            prepared_payload = dict(payload)
            prepared_payload[_INHERITED_CALLOUT_CLOZE_COLOR_KEY] = inherited_callout_color
            payload_changed = True

        if inherited_callout_color is not None and block.block_type == "table_row":
            prepared_payload = dict(prepared_payload)
            prepared_payload["cells"] = self._mark_table_cells(
                prepared_payload.get("cells"),
                inherited_callout_color,
            )
            payload_changed = True

        prepared_children = tuple(
            self._prepare_advanced_block(child, inherited_callout_color=effective_callout_color)
            for child in block.children
        )
        children_changed = any(
            prepared is not original
            for prepared, original in zip(prepared_children, block.children)
        )
        if not payload_changed and not children_changed:
            return block

        raw = dict(block.raw)
        if payload_changed:
            raw[block.block_type] = prepared_payload
        return replace(block, raw=raw, children=prepared_children)

    def _mark_table_cells(self, raw_cells: Any, marker_color: str) -> list[Any]:
        """Apply one inherited callout marker to every rich-text table cell."""
        if not isinstance(raw_cells, list):
            return []
        return [
            self._mark_rich_text_items(raw_cell, marker_color)
            if isinstance(raw_cell, list)
            else raw_cell
            for raw_cell in raw_cells
        ]

    def _mark_rich_text_items(self, raw_items: Iterable[Any], marker_color: str) -> list[Any]:
        """Mark copied items without overwriting their original annotations."""
        marked_items: list[Any] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                marked_items.append(raw_item)
                continue
            item = dict(raw_item)
            item[_INHERITED_CALLOUT_CLOZE_COLOR_KEY] = marker_color
            marked_items.append(item)
        return marked_items

    @staticmethod
    def _cloze_markup(number: int, content: str) -> str:
        """Wrap non-empty rendered content in valid Anki cloze markup."""
        return f"{{{{c{number}::{content}}}}}" if content else ""

    # Helpers shared by paragraph and toggle clozes --------------------------

    def validate(self, payload: "shared.ToggleCardPayload") -> ClozeValidationResult:
        """Validate requirements Anki needs to generate cards from a cloze note."""
        errors: list[str] = []
        if payload.card_type != CLOZE or payload.model_name != MODEL_NAME_CLOZE:
            errors.append("Cloze cards must use the registered Notion (Cloze) note type.")
        
        # Validate the Text field is a non-empty string.
        text = payload.fields.get("Text")
        if not isinstance(text, str) or not text.strip():
            errors.append("The Cloze Text field must not be empty.")
            return ClozeValidationResult(False, tuple(errors))
        
        # Validate the cloze markup is balanced and properly nested.
        marker_count, syntax_errors = self._validate_cloze_markup(text)
        errors.extend(syntax_errors)

        # Validate that there is at least one cloze deletion in the Text field.
        if marker_count == 0:
            errors.append("The Cloze Text field must contain at least one deletion such as {{c1::text}}.")

        return ClozeValidationResult(not errors, tuple(errors))

    def _payload_for_fields(self, page_id: str, block: NotionBlock, fields: dict[str, str]) -> "shared.ToggleCardPayload":
        payload_fields = dict(fields)
        payload_fields[NOTION_CARD_BACKGROUND_FIELD] = shared._block_background_color(block)
        
        return shared.ToggleCardPayload(
            notion_page_id   = page_id,
            notion_block_id  = block.block_id,
            card_type        = CLOZE,
            model_name       = MODEL_NAME_CLOZE,
            fields           = payload_fields,
            content_hash     = shared._compute_payload_content_hash(
                page_id    = page_id,
                block_id   = block.block_id,
                card_type  = CLOZE,
                model_name = MODEL_NAME_CLOZE,
                fields     = payload_fields,
            ),
            last_edited_time = shared._as_optional_string(block.raw.get("last_edited_time")),
        )

    @staticmethod
    def _payload(block: NotionBlock) -> dict[str, Any]:
        payload = block.raw.get(block.block_type)
        return payload if isinstance(payload, dict) else {}

    def _block_cloze_color(self, block: NotionBlock) -> str | None:
        """Return a configured explicit or inherited marker color for rendering."""
        explicit_color = self._explicit_block_cloze_color(block)
        if explicit_color is not None:
            return explicit_color
        return self._configured_marker_color(
            self._payload(block).get(_INHERITED_CALLOUT_CLOZE_COLOR_KEY),
        )

    def _explicit_block_cloze_color(self, block: NotionBlock) -> str | None:
        """Return a real Notion block background for renderer-supported colorable types."""
        if block.block_type not in _COLORABLE_RENDERED_BLOCK_TYPES:
            return None
        return self._configured_marker_color(
            self._payload(block).get("color"),
            require_background=True,
        )

    def _configured_marker_color(self, value: Any, *, require_background: bool = False) -> str | None:
        """Normalize one configured Notion marker color without accepting foreground block colors."""
        color = str(value or "").strip().lower()
        if require_background:
            normalized = self._normalize_background_color(color)
            if normalized is None:
                return None
            color = normalized
        else:
            color = self._normalize_background_color(color) or color

        if color not in _CLOZE_NUMBERS or color not in self._marker_colors:
            return None
        return color

    @staticmethod
    def _normalize_background_color(value: Any) -> str | None:
        """Return a base color from either REST or enhanced-Markdown notation."""
        color = str(value or "").strip().lower()
        if color.endswith("_background"):
            return color.removesuffix("_background")
        if color.endswith("_bg"):
            return color.removesuffix("_bg")
        return None

    def _block_cloze_number(self, block: NotionBlock) -> int | None:
        """Return the fixed cloze number for a configured block background color."""
        marker_color = self._block_cloze_color(block)
        return _CLOZE_NUMBERS.get(marker_color) if marker_color is not None else None

    def _rich_text(self, block: NotionBlock) -> list[dict[str, Any]]:
        rich_text = self._payload(block).get("rich_text")
        return [item for item in rich_text if isinstance(item, dict)] if isinstance(rich_text, list) else []

    def _table_cell_cloze_number(self, cell_rich_text: Iterable[dict[str, Any]]) -> int | None:
        """Return a marker only when every non-empty cell fragment has one color."""
        number: int | None = None
        has_content = False
        for item in cell_rich_text:
            if not self._cloze_fragment(item).strip():
                continue

            item_number = self._cloze_number(item)
            if item_number is None or (number is not None and item_number != number):
                return None
            number = item_number
            has_content = True
        return number if has_content else None

    @staticmethod
    def _plain_text(rich_text: Iterable[dict[str, Any]]) -> str:
        return "".join(
            ClozeCardParser._rich_text_item_plain_text(item)
            for item in rich_text
        )

    @staticmethod
    def _rich_text_item_plain_text(item: dict[str, Any]) -> str:
        """Return one item's visible text without formatting annotations."""
        plain_text = item.get("plain_text")
        if isinstance(plain_text, str):
            return plain_text
        text_payload = item.get("text")
        if isinstance(text_payload, dict):
            content = text_payload.get("content")
            if isinstance(content, str):
                return content
        return ""

    def _without_extra_prefix(self, block: NotionBlock) -> NotionBlock:
        """Remove a visible ``Extra:`` prefix across formatted rich-text runs."""
        raw     = dict(block.raw)
        payload = dict(self._payload(block))
        items   = self._rich_text(block)
        prefix_match = _EXTRA_PREFIX_RE.match(self._plain_text(items))
        if prefix_match is None:
            return block

        remaining_prefix_length = prefix_match.end()
        stripped_items: list[dict[str, Any]] = []
        for original_item in items:
            visible_text = self._rich_text_item_plain_text(original_item)
            if remaining_prefix_length <= 0 or not visible_text:
                stripped_items.append(original_item)
                continue
            if len(visible_text) <= remaining_prefix_length:
                # This complete formatting run belongs to the marker.
                remaining_prefix_length -= len(visible_text)
                continue

            # Only the beginning of this run belongs to the marker. Clone the
            # item so its remaining text keeps links and formatting annotations.
            item = dict(original_item)
            text_payload = original_item.get("text")
            if isinstance(text_payload, dict) and isinstance(text_payload.get("content"), str):
                copied_text_payload = dict(text_payload)
                copied_text_payload["content"] = text_payload["content"][
                    remaining_prefix_length:
                ]
                item["text"] = copied_text_payload
            if isinstance(original_item.get("plain_text"), str):
                item["plain_text"] = original_item["plain_text"][
                    remaining_prefix_length:
                ]
            stripped_items.append(item)
            remaining_prefix_length = 0

        payload["rich_text"]  = stripped_items
        raw[block.block_type] = payload
        return replace(block, raw=raw)

    def _cloze_number(self, item: Any) -> int | None:
        if isinstance(item, dict):
            cell_color = self._configured_marker_color(item.get(TABLE_CELL_CLOZE_COLOR_KEY))
            if cell_color is not None:
                return _CLOZE_NUMBERS[cell_color]
            inherited_color = self._configured_marker_color(
                item.get(_INHERITED_CALLOUT_CLOZE_COLOR_KEY),
            )
            if inherited_color is not None:
                return _CLOZE_NUMBERS[inherited_color]

        annotations = item.get("annotations") if isinstance(item, dict) else None
        if not isinstance(annotations, dict):
            return None
        
        # get item color annotation and convert it to the corresponding cloze number
        color = self._configured_marker_color(
            annotations.get("color"),
            require_background=True,
        )
        if color is not None:
            return _CLOZE_NUMBERS[color]
        
        # get item background color annotation and convert it to the corresponding cloze number
        background = self._configured_marker_color(annotations.get("background_color"))
        return _CLOZE_NUMBERS.get(background) if background is not None else None

    @staticmethod
    def _cloze_fragment(item: dict[str, Any]) -> str:
        if item.get("type") == "equation":
            expression = item.get("equation", {}).get("expression") if isinstance(item.get("equation"), dict) else ""
            return f"\\({html.escape(str(expression).strip())}\\)" if str(expression).strip() else ""
        return html.escape(str(item.get("text", {}).get("content") or item.get("plain_text") or ""))

    def _remove_marker_color(self, item: dict[str, Any]) -> dict[str, Any]:
        result      = dict(item)
        result.pop(TABLE_CELL_CLOZE_COLOR_KEY, None)
        result.pop(_INHERITED_CALLOUT_CLOZE_COLOR_KEY, None)
        annotations = dict(item.get("annotations") or {})

        # Set color annotation to default for cloze color 
        color = self._normalize_background_color(annotations.get("color")) or str(annotations.get("color") or "").strip().lower()
        if color in self._marker_colors:
            annotations["color"] = "default"

        # Set background_color annotation to default for cloze background color
        background = self._normalize_background_color(annotations.get("background_color")) or str(annotations.get("background_color") or "").strip().lower()
        if background in self._marker_colors:
            annotations["background_color"] = "default"

        result["annotations"] = annotations
        return result

    def _validate_cloze_markup(self, text: str) -> tuple[int, list[str]]:
        """Check balanced, properly nested Anki ``{{cN::...}}`` markup."""
        stack:       list[int] = []
        marker_count           = 0
        errors:      list[str] = []
        index                  = 0

        while index < len(text):
            match = _CLOZE_START_RE.match(text, index)
            if match:
                stack.append(index)
                marker_count += 1
                if len(stack) > _MAX_NESTING_DEPTH:
                    errors.append(f"Cloze nesting exceeds Anki's supported depth of {_MAX_NESTING_DEPTH}.")
                index = match.end()
                continue

            if text.startswith("{{", index):
                errors.append("Cloze markup must start with {{cN:: where N is a positive integer.")
                index += 2
                continue

            if text.startswith("}}", index):
                if not stack:
                    errors.append("Cloze markup contains a closing }} without a matching opening marker.")
                
                else:
                    start = stack.pop()
                    body  = text[start:text.index("}}", start) if "}}" in text[start:] else len(text)]
                    body  = body.split("::", 1)[1] if "::" in body else ""
                    if not html.unescape(_HTML_TAG_RE.sub("", body)).strip():
                        errors.append("Each cloze deletion must hide non-empty text.")

                index += 2
                continue

            index += 1
        if stack:
            errors.append("Cloze markup contains an opening marker without a matching closing }}.")

        return marker_count, errors
