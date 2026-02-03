"""Parse Notion block trees into deterministic card payloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
from typing import Any, Iterable
from urllib.parse import urlsplit

from .notion_client import NotionBlock

_SAFE_COLOR_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")
_SAFE_LANG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_UNSAFE_LINK_SCHEMES = frozenset({"javascript", "data", "vbscript"})


@dataclass(frozen=True)
class ToggleCardPayload:
    """Represents one parsed Notion toggle card."""

    notion_page_id: str
    notion_block_id: str
    front_html: str
    back_html: str
    content_hash: str
    last_edited_time: str | None


def extract_root_toggle_blocks(blocks: Iterable[NotionBlock]) -> list[NotionBlock]:
    """Return only top-level toggle blocks."""
    return [block for block in blocks if block.block_type == "toggle"]


def render_rich_text(rich_text: Iterable[dict[str, Any]]) -> str:
    """Render Notion rich text items into sanitized HTML."""
    parts: list[str] = []
    for item in rich_text:
        parts.append(_render_rich_text_item(item))
    return "".join(parts)


def render_blocks(blocks: Iterable[NotionBlock]) -> str:
    """Render blocks to deterministic HTML while coalescing list sequences."""
    ordered_blocks = list(blocks)
    html_chunks: list[str] = []
    index = 0

    while index < len(ordered_blocks):
        block = ordered_blocks[index]
        if block.block_type in {"bulleted_list_item", "numbered_list_item"}:
            tag_name = "ul" if block.block_type == "bulleted_list_item" else "ol"
            list_html, index = _render_list_sequence(ordered_blocks, index, block.block_type, tag_name)
            html_chunks.append(list_html)
            continue

        html_chunks.append(_render_block(block))
        index += 1

    return "".join(chunk for chunk in html_chunks if chunk)


def parse_page_to_cards(page_id: str, blocks: Iterable[NotionBlock]) -> list[ToggleCardPayload]:
    """Parse top-level toggles into card payloads."""
    payloads: list[ToggleCardPayload] = []
    for block in extract_root_toggle_blocks(blocks):
        front_html = render_rich_text(_block_rich_text(block))
        back_html = render_blocks(block.children)
        payloads.append(
            ToggleCardPayload(
                notion_page_id=page_id,
                notion_block_id=block.block_id,
                front_html=front_html,
                back_html=back_html,
                content_hash=_compute_content_hash(page_id, block.block_id, front_html, back_html),
                last_edited_time=_as_optional_string(block.raw.get("last_edited_time")),
            )
        )
    return payloads


def _render_block(block: NotionBlock) -> str:
    """Render a single block, including required children recursion."""
    block_type = block.block_type
    if block_type == "paragraph":
        return _render_paragraph(block)
    if block_type == "quote":
        return _render_quote(block)
    if block_type == "callout":
        return _render_callout(block)
    if block_type == "code":
        return _render_code_block(block)
    if block_type == "equation":
        return _render_block_equation(block)
    if block_type == "toggle":
        return _render_toggle_inline(block)
    if block_type == "divider":
        return "<hr/>"
    return ""


def _render_paragraph(block: NotionBlock) -> str:
    """Render a paragraph block and any nested children."""
    text_html = render_rich_text(_block_rich_text(block))
    body = f"<p>{text_html}</p>"
    children_html = render_blocks(block.children)
    return body + children_html


def _render_quote(block: NotionBlock) -> str:
    """Render a quote block."""
    text_html = render_rich_text(_block_rich_text(block))
    body = f"<p>{text_html}</p>" if text_html else ""
    children_html = render_blocks(block.children)
    return f"<blockquote>{body}{children_html}</blockquote>"


def _render_callout(block: NotionBlock) -> str:
    """Render a callout block with optional leading icon."""
    payload = _block_payload(block)
    icon_html = _render_callout_icon(payload.get("icon"))
    text_html = render_rich_text(_block_rich_text(block))
    body = f"<p>{text_html}</p>" if text_html else ""
    children_html = render_blocks(block.children)
    return f'<div class="callout">{icon_html}<div>{body}{children_html}</div></div>'


def _render_callout_icon(icon_payload: Any) -> str:
    """Render a callout emoji icon when available."""
    if not isinstance(icon_payload, dict):
        return ""

    if icon_payload.get("type") == "emoji":
        emoji = icon_payload.get("emoji")
        if isinstance(emoji, str) and emoji:
            return f'<p class="notion-callout-icon">{html.escape(emoji)}</p>'

    return ""


def _render_toggle_inline(block: NotionBlock) -> str:
    """Render a nested toggle for inline display in parent cards."""
    title_html = render_rich_text(_block_rich_text(block))
    children_html = render_blocks(block.children)
    return f'<details class="notion-toggle"><summary>{title_html}</summary>{children_html}</details>'


def _render_code_block(block: NotionBlock) -> str:
    """Render a code block with escaped plain text."""
    payload = _block_payload(block)
    code_rich_text = payload.get("rich_text") if isinstance(payload.get("rich_text"), list) else []
    code_text = _rich_text_to_plain(code_rich_text)
    language = _sanitize_language(payload.get("language"))
    class_attr = f' class="language-{language}"' if language else ""
    return f'<pre class="code"><code{class_attr}>{html.escape(code_text)}</code></pre>'


def _render_block_equation(block: NotionBlock) -> str:
    """Render a display equation using MathJax delimiters."""
    payload = _block_payload(block)
    expression = _normalize_equation_expression(payload.get("expression"))
    return f'<div class="notion-block-equation">\\[{html.escape(expression)}\\]</div>'


def _render_list_sequence(
    blocks: list[NotionBlock],
    start_index: int,
    block_type: str,
    list_tag: str,
) -> tuple[str, int]:
    """Render consecutive list item blocks as one list."""
    index = start_index
    items: list[str] = []

    while index < len(blocks) and blocks[index].block_type == block_type:
        block = blocks[index]
        text_html = render_rich_text(_block_rich_text(block))
        children_html = render_blocks(block.children)
        items.append(f"<li>{text_html}{children_html}</li>")
        index += 1

    return f"<{list_tag}>{''.join(items)}</{list_tag}>", index


def _render_rich_text_item(item: dict[str, Any]) -> str:
    """Render one rich_text item including annotations and links."""
    item_type = item.get("type")
    if item_type == "equation":
        equation = item.get("equation") or {}
        expression = _normalize_equation_expression(equation.get("expression"))
        rendered = f'<span class="notion-equation">\\({html.escape(expression)}\\)</span>'
    else:
        content = _extract_text_content(item)
        rendered = html.escape(content)

    annotations = item.get("annotations") or {}
    if bool(annotations.get("code")):
        rendered = f"<code>{rendered}</code>"
    if bool(annotations.get("bold")):
        rendered = f"<strong>{rendered}</strong>"
    if bool(annotations.get("italic")):
        rendered = f"<em>{rendered}</em>"
    if bool(annotations.get("underline")):
        rendered = f"<u>{rendered}</u>"
    if bool(annotations.get("strikethrough")):
        rendered = f"<s>{rendered}</s>"

    href = _sanitize_href(item.get("href"))
    if href:
        rendered = f'<a href="{html.escape(href, quote=True)}">{rendered}</a>'
    
    color = _sanitize_color(annotations.get("color"))
    if color and color != "default":
        rendered = f'<span class="highlight-{color}">{rendered}</span>'

    return rendered


def _extract_text_content(item: dict[str, Any]) -> str:
    """Extract text content from a non-equation rich_text item."""
    text_payload = item.get("text")
    if isinstance(text_payload, dict):
        content = text_payload.get("content")
        if isinstance(content, str):
            return content

    plain = item.get("plain_text")
    if isinstance(plain, str):
        return plain
    return ""


def _block_payload(block: NotionBlock) -> dict[str, Any]:
    """Return the block-type payload section from a Notion block."""
    payload = block.raw.get(block.block_type)
    if isinstance(payload, dict):
        return payload
    return {}


def _block_rich_text(block: NotionBlock) -> list[dict[str, Any]]:
    """Return block rich_text list or an empty list."""
    payload = _block_payload(block)
    rich_text = payload.get("rich_text")
    if isinstance(rich_text, list):
        return [item for item in rich_text if isinstance(item, dict)]
    return []


def _rich_text_to_plain(rich_text: Iterable[dict[str, Any]]) -> str:
    """Flatten rich_text items to plain text content."""
    segments: list[str] = []
    for item in rich_text:
        item_type = item.get("type")
        if item_type == "equation":
            equation = item.get("equation") or {}
            expression = equation.get("expression")
            if isinstance(expression, str):
                segments.append(expression)
                continue

        text = _extract_text_content(item)
        if text:
            segments.append(text)
    return "".join(segments)


def _sanitize_href(value: Any) -> str:
    """Return safe href values and drop unsafe URL schemes."""
    if not isinstance(value, str):
        return ""

    href = value.strip()
    if not href:
        return ""

    scheme = urlsplit(href).scheme.lower()
    if scheme in _UNSAFE_LINK_SCHEMES:
        return ""
    return href


def _sanitize_color(value: Any) -> str:
    """Return safe color names for highlight class generation."""
    if not isinstance(value, str):
        return "default"

    cleaned = value.strip().lower()
    if not cleaned:
        return "default"

    if set(cleaned) <= _SAFE_COLOR_CHARS:
        return cleaned
    return "default"


def _sanitize_language(value: Any) -> str:
    """Return a safe CSS class fragment for code language."""
    if not isinstance(value, str):
        return ""

    cleaned = value.strip().lower()
    if not cleaned:
        return ""

    if set(cleaned) <= _SAFE_LANG_CHARS:
        return cleaned
    return ""


def _normalize_equation_expression(value: Any) -> str:
    """Normalize equation text for stable MathJax rendering."""
    if not isinstance(value, str):
        return ""

    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")


def _as_optional_string(value: Any) -> str | None:
    """Normalize optional values to strings or None."""
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _compute_content_hash(
    page_id: str,
    block_id: str,
    front_html: str,
    back_html: str,
) -> str:
    """Compute a deterministic content hash for sync comparisons."""
    payload = f"{page_id}\n{block_id}\n{front_html}\n{back_html}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
