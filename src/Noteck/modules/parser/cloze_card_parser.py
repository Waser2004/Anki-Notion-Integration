"""Cloze-card parser and validator for Anki-compatible payloads."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from . import parser as shared
from ..card_types import CLOZE
from ..cards import MODEL_NAME_CLOZE
from ..notion_client import NotionBlock
from .renderer import _render_rich_text_item, render_blocks_with_renderer, render_rich_text


_CLOZE_CONTAINER_PREFIX_RE = re.compile(r"^\s*\[cloze\]", re.IGNORECASE)
_EXTRA_PREFIX_RE           = re.compile(r"^\s*extra\s*:\s*", re.IGNORECASE)

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

_CLOZE_START_RE    = re.compile(r"\{\{c([1-9]\d*)::")
_HTML_TAG_RE       = re.compile(r"<[^>]*>")
_MAX_NESTING_DEPTH = 3


@dataclass(frozen=True)
class ClozeValidationResult:
    """Result of checking one payload against Anki cloze field requirements."""
    is_valid: bool
    errors:   tuple[str, ...] = ()


class ClozeCardParser:
    """Build normal and advanced cloze payloads from Notion blocks."""

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
            
            # Check if the paragraph contains any cloze markers (background colors).
            rich_text = self._rich_text(block)
            if not any(self._cloze_number(item) is not None for item in rich_text):
                continue
            
            # Convert the rich text to Anki cloze markup.
            text = self._rich_text_to_cloze_text(rich_text)
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
        return render_rich_text(self._rich_text(self._without_extra_prefix(block)))

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
            fragment = self._cloze_fragment(item)
            if not fragment:
                continue

            item_number = self._cloze_number(item)
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
        is_gray_toggle = color.strip().lower() == "gray_background" if isinstance(color, str) else False
        return enable_gray_toggle_cloze and is_gray_toggle

    def parse_advanced(self, page_id: str, block: NotionBlock) -> "shared.ToggleCardPayload":
        """Parse one advanced cloze toggle into an Anki cloze payload."""
        text_blocks, extra_blocks = self._split_advanced_children(block.children)
        fields = {
            "Text":            render_blocks_with_renderer(text_blocks,  rich_text_renderer = self._rich_text_to_advanced_cloze_html),
            "Extra":           render_blocks_with_renderer(extra_blocks, rich_text_renderer = self._rich_text_to_advanced_cloze_html),
            "Notion Block ID": block.block_id,
        }
        return self._payload_for_fields(page_id, block, fields)

    def _split_advanced_children(self, children: Iterable[NotionBlock]) -> tuple[list[NotionBlock], list[NotionBlock]]:
        text_blocks:  list[NotionBlock] = []
        extra_blocks: list[NotionBlock] = []

        for child in children:
            # Check if the child is a "extra" paragraph add it to extra blocks
            if self._is_extra_paragraph(child):
                extra_blocks.append(self._without_extra_prefix(child))
                continue

            text_blocks.append(child)

        return text_blocks, extra_blocks

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
        return shared.ToggleCardPayload(
            notion_page_id   = page_id,
            notion_block_id  = block.block_id,
            card_type        = CLOZE,
            model_name       = MODEL_NAME_CLOZE,
            fields           = fields,
            content_hash     = shared._compute_payload_content_hash(
                page_id    = page_id,
                block_id   = block.block_id,
                card_type  = CLOZE,
                model_name = MODEL_NAME_CLOZE,
                fields     = fields,
            ),
            last_edited_time = shared._as_optional_string(block.raw.get("last_edited_time")),
        )

    @staticmethod
    def _payload(block: NotionBlock) -> dict[str, Any]:
        payload = block.raw.get(block.block_type)
        return payload if isinstance(payload, dict) else {}

    def _rich_text(self, block: NotionBlock) -> list[dict[str, Any]]:
        rich_text = self._payload(block).get("rich_text")
        return [item for item in rich_text if isinstance(item, dict)] if isinstance(rich_text, list) else []

    @staticmethod
    def _plain_text(rich_text: Iterable[dict[str, Any]]) -> str:
        return "".join(str(item.get("plain_text") or item.get("text", {}).get("content") or "") for item in rich_text)

    def _without_extra_prefix(self, block: NotionBlock) -> NotionBlock:
        """Return a copy of an ``Extra:`` paragraph with its prefix removed."""
        # Extract block data
        raw     = dict(block.raw)
        payload = dict(self._payload(block))
        items   = self._rich_text(block)

        for index, original_item in enumerate(items):
            text_payload = original_item.get("text")
            if not isinstance(text_payload, dict) or not isinstance(text_payload.get("content"), str):
                continue
            
            # Remove the "Extra:" prefix from the content field of the first rich text item if it exists.
            item         = dict(original_item)
            text_payload = dict(text_payload)
            text_payload["content"] = _EXTRA_PREFIX_RE.sub("", text_payload["content"], count=1)
            item["text"] = text_payload

            # Remove the "Extra:" prefix from the plain_text field if it exists.
            if isinstance(item.get("plain_text"), str):
                item["plain_text"] = _EXTRA_PREFIX_RE.sub("", item["plain_text"], count=1)

            items[index] = item
            break
        
        # Update the block's rich_text payload with the modified items.
        payload["rich_text"]  = items
        raw[block.block_type] = payload
        
        return replace(block, raw=raw)

    @staticmethod
    def _cloze_number(item: Any) -> int | None:
        annotations = item.get("annotations") if isinstance(item, dict) else None
        if not isinstance(annotations, dict):
            return None
        
        # get item color annotation and convert it to the corresponding cloze number
        color = str(annotations.get("color") or "").lower()
        if color.endswith("_background"):
            number = _CLOZE_NUMBERS.get(color.removesuffix("_background"))
            if number is not None:
                return number
        
        # get item background color annotation and convert it to the corresponding cloze number
        background = str(annotations.get("background_color") or "").lower().removesuffix("_background")
        return _CLOZE_NUMBERS.get(background)

    @staticmethod
    def _cloze_fragment(item: dict[str, Any]) -> str:
        if item.get("type") == "equation":
            expression = item.get("equation", {}).get("expression") if isinstance(item.get("equation"), dict) else ""
            return f"\\({html.escape(str(expression).strip())}\\)" if str(expression).strip() else ""
        return html.escape(str(item.get("text", {}).get("content") or item.get("plain_text") or ""))

    @staticmethod
    def _remove_marker_color(item: dict[str, Any]) -> dict[str, Any]:
        result      = dict(item)
        annotations = dict(item.get("annotations") or {})

        # Set color annotation to default for cloze color 
        color = str(annotations.get("color") or "").lower()
        if color.endswith("_background") and color.removesuffix("_background") in _CLOZE_NUMBERS:
            annotations["color"] = "default"

        # Set background_color annotation to default for cloze background color
        if str(annotations.get("background_color") or "").lower().removesuffix("_background") in _CLOZE_NUMBERS:
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
