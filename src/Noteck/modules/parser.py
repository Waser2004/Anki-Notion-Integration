"""Parse Notion block trees into deterministic card payloads."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import html
import math
import re
from typing import Any, Collection, Iterable, Mapping
from urllib.parse import urlsplit

from .card_types import BASIC, BASIC_REVERSED, CLOZE, INPUT, normalize_default_selectable_card_type
from .cards import MODEL_NAME_BASIC, MODEL_NAME_BASIC_REVERSED, MODEL_NAME_CLOZE, MODEL_NAME_INPUT
from .notion_client import NotionBlock

_SAFE_COLOR_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")
_SAFE_LANG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_UNSAFE_LINK_SCHEMES = frozenset({"javascript", "data", "vbscript"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")
_OPERATOR_CHARS = frozenset("+-*/%=!<>|&^~?:")
_PUNCTUATION_CHARS = frozenset("()[]{}.,;")
_CLOZE_EXTRA_PREFIX_RE = re.compile(r"^\s*extra\s*:\s*", re.IGNORECASE)

# Canonical language labels and aliases for common Notion code-block values.
_LANGUAGE_ALIASES = {
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "typescript": "typescript",
    "java": "java",
    "c": "c",
    "h": "c",
    "c++": "cpp",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "h++": "cpp",
    "c#": "csharp",
    "cs": "csharp",
    "csharp": "csharp",
    "go": "go",
    "golang": "go",
    "rs": "rust",
    "rust": "rust",
    "sql": "sql",
    "mysql": "sql",
    "postgresql": "sql",
    "plsql": "sql",
    "bash": "bash",
    "shell": "bash",
    "sh": "bash",
    "zsh": "bash",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "html": "html",
    "xml": "html",
    "css": "css",
}

_LANGUAGE_KEYWORDS: dict[str, frozenset[str]] = {
    "python": frozenset(
        {
            "and", "as", "assert", "async", "await", "break", "case", "class", "continue",
            "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
            "if", "import", "in", "is", "lambda", "match", "nonlocal", "not", "or", "pass",
            "raise", "return", "try", "while", "with", "yield",
        }
    ),
    "javascript": frozenset(
        {
            "async", "await", "break", "case", "catch", "class", "const", "continue",
            "debugger", "default", "delete", "do", "else", "export", "extends", "finally",
            "for", "from", "function", "if", "import", "in", "instanceof", "let", "new",
            "return", "switch", "throw", "try", "typeof", "var", "void", "while", "with",
            "yield",
        }
    ),
    "typescript": frozenset(
        {
            "abstract", "any", "as", "async", "await", "break", "case", "catch", "class",
            "const", "continue", "declare", "default", "do", "else", "enum", "export",
            "extends", "finally", "for", "from", "function", "if", "implements", "import",
            "in", "infer", "instanceof", "interface", "keyof", "let", "namespace", "new",
            "readonly", "return", "satisfies", "switch", "throw", "try", "type", "typeof",
            "var", "while",
        }
    ),
    "java": frozenset(
        {
            "abstract", "assert", "boolean", "break", "case", "catch", "class", "const",
            "continue", "default", "do", "else", "enum", "extends", "final", "finally",
            "for", "if", "implements", "import", "instanceof", "interface", "native", "new",
            "package", "private", "protected", "public", "return", "static", "strictfp",
            "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try",
            "void", "volatile", "while",
        }
    ),
    "c": frozenset(
        {
            "auto", "break", "case", "const", "continue", "default", "do", "else", "enum",
            "extern", "for", "goto", "if", "inline", "register", "restrict", "return",
            "sizeof", "static", "struct", "switch", "typedef", "union", "volatile", "while",
        }
    ),
    "cpp": frozenset(
        {
            "alignas", "auto", "break", "case", "catch", "class", "const", "constexpr",
            "continue", "decltype", "default", "delete", "do", "else", "enum", "explicit",
            "export", "extern", "for", "friend", "goto", "if", "inline", "mutable",
            "namespace", "new", "noexcept", "operator", "private", "protected", "public",
            "return", "static", "struct", "switch", "template", "this", "throw", "try",
            "typedef", "typename", "union", "using", "virtual", "while",
        }
    ),
    "csharp": frozenset(
        {
            "abstract", "as", "base", "break", "case", "catch", "class", "const", "continue",
            "default", "delegate", "do", "else", "enum", "event", "explicit", "extern",
            "finally", "for", "foreach", "if", "implicit", "in", "interface", "internal",
            "is", "lock", "namespace", "new", "operator", "out", "override", "private",
            "protected", "public", "readonly", "ref", "return", "sealed", "sizeof", "stackalloc",
            "static", "struct", "switch", "this", "throw", "try", "typeof", "unchecked",
            "unsafe", "using", "virtual", "void", "volatile", "while",
        }
    ),
    "go": frozenset(
        {
            "break", "case", "chan", "const", "continue", "default", "defer", "else", "fallthrough",
            "for", "func", "go", "goto", "if", "import", "interface", "map", "package", "range",
            "return", "select", "struct", "switch", "type", "var",
        }
    ),
    "rust": frozenset(
        {
            "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else",
            "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop",
            "match", "mod", "move", "mut", "pub", "ref", "return", "self", "static",
            "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while",
        }
    ),
    "sql": frozenset(
        {
            "select", "from", "where", "join", "inner", "left", "right", "full", "on", "group",
            "by", "order", "having", "limit", "offset", "insert", "into", "values", "update",
            "set", "delete", "create", "alter", "drop", "table", "view", "index", "distinct",
            "and", "or", "not", "null", "is", "as", "case", "when", "then", "else", "end",
        }
    ),
    "bash": frozenset(
        {
            "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
            "case", "esac", "function", "in", "select", "time", "coproc", "return", "break",
            "continue", "export", "local", "readonly",
        }
    ),
    "json": frozenset({"true", "false", "null"}),
    "yaml": frozenset({"true", "false", "null", "yes", "no", "on", "off"}),
    "html": frozenset({"doctype"}),
    "css": frozenset(
        {
            "@media", "@supports", "@keyframes", "@font-face", "@import",
            "display", "position", "color", "background", "font-size", "grid", "flex",
        }
    ),
}

_LANGUAGE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"int", "float", "str", "bool", "list", "dict", "tuple", "set"}),
    "javascript": frozenset({"string", "number", "boolean", "object", "undefined"}),
    "typescript": frozenset({"string", "number", "boolean", "void", "unknown", "never", "any"}),
    "java": frozenset({"int", "long", "float", "double", "char", "boolean", "String"}),
    "c": frozenset({"int", "long", "short", "float", "double", "char", "void", "size_t"}),
    "cpp": frozenset({"int", "long", "short", "float", "double", "char", "void", "bool", "std"}),
    "csharp": frozenset({"int", "long", "short", "float", "double", "decimal", "string", "bool", "var"}),
    "go": frozenset({"int", "int64", "float32", "float64", "string", "bool", "byte", "rune", "error"}),
    "rust": frozenset({"i32", "i64", "u32", "u64", "f32", "f64", "bool", "str", "String"}),
    "sql": frozenset({"varchar", "int", "bigint", "date", "timestamp", "boolean"}),
    "bash": frozenset({"PATH", "HOME", "PWD", "SHELL"}),
    "json": frozenset(),
    "yaml": frozenset(),
    "html": frozenset({"html", "head", "body", "script", "style", "div", "span"}),
    "css": frozenset({"px", "em", "rem", "vh", "vw"}),
}


@dataclass(frozen=True)
class ToggleCardPayload:
    """Represents one parsed Notion card payload."""

    notion_page_id: str
    notion_block_id: str
    front_html: str = ""
    back_html: str = ""
    card_type: str = BASIC
    model_name: str = MODEL_NAME_BASIC
    fields: dict[str, str] = field(default_factory=dict)
    content_hash: str = ""
    last_edited_time: str | None = None


@dataclass(frozen=True)
class ImageOcclusionCandidate:
    """Represents one image candidate for Image Occlusion workflow."""

    notion_block_id: str
    image_url: str
    caption_html: str
    caption_plain: str


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


def parse_page_to_cards(
    page_id: str,
    blocks: Iterable[NotionBlock],
    *,
    default_card_type: str = BASIC,
    card_type_overrides: Mapping[str, str] | None = None,
    enable_cloze: bool = False,
    include_block_ids: Collection[str] | None = None,
) -> list[ToggleCardPayload]:
    """Parse page blocks into typed card payloads."""
    resolved_default_card_type = normalize_default_selectable_card_type(default_card_type)
    normalized_include_block_ids = {str(block_id) for block_id in include_block_ids} if include_block_ids else None
    top_level_blocks = list(blocks)
    payloads: list[ToggleCardPayload] = []
    for block in extract_root_toggle_blocks(top_level_blocks):
        # Skip excluded/non-target blocks before rendering payload fields.
        if normalized_include_block_ids is not None and block.block_id not in normalized_include_block_ids:
            continue
        
        # apply card specific card type override.
        normalized_overrides: dict[str, str] = {}
        if card_type_overrides:
            normalized_overrides = {
                str(block_id): normalize_default_selectable_card_type(card_type)
                for block_id, card_type in card_type_overrides.items()
            }
        resolved_card_type = normalized_overrides.get(block.block_id, resolved_default_card_type)

        # parse front and bacvk html
        front_html = _render_toggle_front(block)
        back_html = render_blocks(block.children)

        # create card fields and content hash for change detection
        fields = _build_toggle_fields(
            block_id=block.block_id,
            front_html=front_html,
            back_html=back_html,
            back_blocks=block.children,
            card_type=resolved_card_type,
        )
        model_name = _model_name_for_card_type(resolved_card_type)
        content_hash = _compute_payload_content_hash(
            page_id=page_id,
            block_id=block.block_id,
            card_type=resolved_card_type,
            model_name=model_name,
            fields=fields,
        )

        # add card to output list
        payloads.append(
            ToggleCardPayload(
                notion_page_id=page_id,
                notion_block_id=block.block_id,
                front_html=front_html,
                back_html=back_html,
                card_type=resolved_card_type,
                model_name=model_name,
                fields=fields,
                content_hash=content_hash,
                last_edited_time=_as_optional_string(block.raw.get("last_edited_time")),
            )
        )

    # parse cloze
    if enable_cloze:
        payloads.extend(
            _parse_top_level_cloze_paragraphs(
                page_id,
                top_level_blocks,
                include_block_ids=normalized_include_block_ids,
            )
        )

    return payloads


def _render_block(block: NotionBlock) -> str:
    """Render a single block, including required children recursion."""
    block_type = block.block_type
    if block_type == "heading_1":
        return _render_heading(block, level=1)
    if block_type == "heading_2":
        return _render_heading(block, level=2)
    if block_type == "heading_3":
        return _render_heading(block, level=3)
    if block_type == "column_list":
        return _render_column_list(block)
    if block_type == "column":
        return _render_column(block)
    if block_type == "paragraph":
        return _render_paragraph(block)
    if block_type == "table":
        return _render_table(block)
    if block_type == "quote":
        return _render_quote(block)
    if block_type == "callout":
        return _render_callout(block)
    if block_type == "image":
        return _render_image(block)
    if block_type == "code":
        return _render_code_block(block)
    if block_type == "equation":
        return _render_block_equation(block)
    if block_type == "toggle":
        return _render_toggle_inline(block)
    if block_type == "divider":
        return "<hr/>"
    return ""


def _render_column_list(block: NotionBlock) -> str:
    """Render a Notion multi-column container with width ratios."""
    column_blocks = [child for child in block.children if child.block_type == "column"]
    if not column_blocks:
        return ""

    # Resolve ratios once so rendering is deterministic for any column count.
    width_ratios = _resolve_column_width_ratios(column_blocks)
    rendered_columns = [
        _render_column_with_width(column_block, width_ratio=width_ratio)
        for column_block, width_ratio in zip(column_blocks, width_ratios)
    ]
    return f'<div class="notion-columns">{"".join(rendered_columns)}</div>'


def _render_column(block: NotionBlock) -> str:
    """Render a standalone column block when encountered directly."""
    return _render_column_with_width(block, width_ratio=None)


def _render_column_with_width(block: NotionBlock, *, width_ratio: float | None) -> str:
    """Render one column block and optionally apply an explicit width ratio."""
    children_html = render_blocks(block.children)
    if width_ratio is None:
        return f'<div class="notion-column">{children_html}</div>'

    width_percent = width_ratio * 100.0
    style_attr = f' style="--notion-column-width: {width_percent:.6f}%;"'
    ratio_attr = f' data-column-ratio="{width_ratio:.6f}"'
    return f'<div class="notion-column"{ratio_attr}{style_attr}>{children_html}</div>'


def _render_paragraph(block: NotionBlock) -> str:
    """Render a paragraph block and any nested children."""
    text_html = render_rich_text(_block_rich_text(block))
    body = f"<p>{text_html}</p>"
    children_html = render_blocks(block.children)
    return body + children_html


def _render_heading(block: NotionBlock, *, level: int) -> str:
    """Render a heading block (levels 1-3) and any nested children."""
    text_html = render_rich_text(_block_rich_text(block))
    body = f"<h{level}>{text_html}</h{level}>"
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
    """Render a code block with lightweight syntax-highlight spans when possible."""
    payload = _block_payload(block)
    code_rich_text = payload.get("rich_text") if isinstance(payload.get("rich_text"), list) else []
    code_text = _rich_text_to_plain(code_rich_text)
    language = _sanitize_language(payload.get("language"))
    if language == "mermaid":
        return _render_mermaid_code_block(code_text, payload)

    canonical_language = _canonicalize_language(language)
    class_attr = f' class="language-{language}"' if language else ""
    code_html = _render_highlighted_code(code_text, canonical_language)
    return f'<pre class="code"><code{class_attr}>{code_html}</code></pre>'


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


def _render_table(block: NotionBlock) -> str:
    """Render a Notion table block and its row children."""
    payload = _block_payload(block)
    has_column_header = bool(payload.get("has_column_header"))
    has_row_header = bool(payload.get("has_row_header"))
    table_width = _normalize_table_width(payload.get("table_width"))

    row_blocks = [child for child in block.children if child.block_type == "table_row"]
    rows = [_table_row_cells(row_block, table_width) for row_block in row_blocks]
    if not rows:
        return f'<table><tbody></tbody></table>'

    # process header row if present
    if has_column_header:
        header_html = f"<thead>{_render_table_row(rows[0], row_index=0, has_column_header=True, has_row_header=False)}</thead>"
        body_rows = rows[1:]
    else:
        header_html = ""
        body_rows = rows

    body_html = "".join(
        _render_table_row(
            row_cells,
            row_index=index + (1 if has_column_header else 0),
            has_column_header=has_column_header,
            has_row_header=has_row_header,
        )
        for index, row_cells in enumerate(body_rows)
    )
    return f'<table>{header_html}<tbody>{body_html}</tbody></table>'


def _resolve_column_width_ratios(column_blocks: list[NotionBlock]) -> list[float]:
    """Resolve one normalized width ratio per column block."""
    column_count = len(column_blocks)
    if column_count == 0:
        return []

    parsed = [_column_width_ratio(column_block) for column_block in column_blocks]
    known = [ratio for ratio in parsed if ratio is not None]
    missing_count = sum(1 for ratio in parsed if ratio is None)

    if missing_count == 0:
        total = sum(known)
        if total > 0:
            return [ratio / total for ratio in known]
        return [1.0 / column_count] * column_count

    total_known = sum(known)
    if total_known <= 0:
        return [1.0 / column_count] * column_count

    if total_known < 1.0:
        # Notion width ratios are commonly fractional shares summing to 1.
        inferred = (1.0 - total_known) / missing_count
        return [ratio if ratio is not None else inferred for ratio in parsed]

    # If known values already exceed 1 with missing columns, use known average as fallback.
    inferred = total_known / len(known)
    weights = [ratio if ratio is not None else inferred for ratio in parsed]
    weight_total = sum(weights)
    if weight_total <= 0:
        return [1.0 / column_count] * column_count
    return [weight / weight_total for weight in weights]


def _column_width_ratio(block: NotionBlock) -> float | None:
    """Extract one safe width_ratio from a `column` block payload."""
    payload = _block_payload(block)
    value = payload.get("width_ratio")
    if not isinstance(value, (int, float)):
        return None

    ratio = float(value)
    if not math.isfinite(ratio) or ratio <= 0:
        return None
    return ratio


def _normalize_table_width(value: Any) -> int | None:
    """Normalize Notion table width metadata to a positive integer."""
    if isinstance(value, int) and value > 0:
        return value
    return None


def _table_row_cells(row_block: NotionBlock, table_width: int | None) -> list[list[dict[str, Any]]]:
    """Return one table row as a list of rich-text cell payloads."""
    row_payload = _block_payload(row_block)
    raw_cells = row_payload.get("cells")
    if not isinstance(raw_cells, list):
        return []

    cells: list[list[dict[str, Any]]] = []
    for raw_cell in raw_cells:
        if isinstance(raw_cell, list):
            cells.append([item for item in raw_cell if isinstance(item, dict)])
        else:
            cells.append([])

    normalized_width = table_width if table_width is not None else len(cells)
    if normalized_width < len(cells):
        return cells[:normalized_width]
    if normalized_width > len(cells):
        return cells + ([[]] * (normalized_width - len(cells)))
    return cells


def _render_table_row(
    row_cells: list[list[dict[str, Any]]],
    *,
    row_index: int,
    has_column_header: bool,
    has_row_header: bool,
) -> str:
    """Render one HTML table row honoring Notion header metadata."""
    parts: list[str] = []
    for column_index, cell_rich_text in enumerate(row_cells):
        cell_html = render_rich_text(cell_rich_text)
        if has_column_header and row_index == 0:
            parts.append(f'<th scope="col">{cell_html}</th>')
            continue
        if has_row_header and column_index == 0:
            parts.append(f'<th scope="row">{cell_html}</th>')
            continue
        parts.append(f"<td>{cell_html}</td>")
    return f"<tr>{''.join(parts)}</tr>"


def _render_image(block: NotionBlock) -> str:
    """Render a Notion image block as a figure with optional caption."""
    payload = _block_payload(block)
    image_url = _extract_image_url(payload)
    caption_items = _extract_caption_items(payload.get("caption"))
    caption_html = render_rich_text(caption_items)
    caption_plain = _rich_text_to_plain(caption_items)
    caption_tag = f"<figcaption>{caption_html}</figcaption>" if caption_html else ""

    if not image_url:
        if caption_tag:
            return f'<figure class="notion-image">{caption_tag}</figure>'
        return ""

    alt_text = caption_plain if caption_plain else "Notion image"
    src_attr = html.escape(image_url, quote=True)
    alt_attr = html.escape(alt_text, quote=True)
    return f'<figure class="notion-image"><img src="{src_attr}" alt="{alt_attr}" loading="lazy"/>{caption_tag}</figure>'


def _extract_image_url(payload: dict[str, Any]) -> str:
    """Extract a safe image URL from a Notion image payload."""
    image_type = payload.get("type")
    if image_type not in {"external", "file"}:
        return ""

    source_payload = payload.get(image_type)
    if not isinstance(source_payload, dict):
        return ""
    return _sanitize_media_href(source_payload.get("url"))


def _extract_caption_items(value: Any) -> list[dict[str, Any]]:
    """Return image/code caption rich-text items."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _render_mermaid_code_block(code_text: str, payload: dict[str, Any]) -> str:
    """Render Mermaid code as a sync-time SVG placeholder with source fallback."""
    encoded_source = base64.urlsafe_b64encode(code_text.encode("utf-8")).decode("ascii")
    code_html = html.escape(code_text)
    caption_html = render_rich_text(_extract_caption_items(payload.get("caption")))
    caption_tag = f"<figcaption>{caption_html}</figcaption>" if caption_html else ""
    return (
        '<figure class="notion-mermaid">'
        f'<div class="notion-mermaid-source" data-mermaid="{encoded_source}">'
        f'<pre class="code"><code class="language-mermaid">{code_html}</code></pre>'
        "</div>"
        f"{caption_tag}"
        "</figure>"
    )


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

    background_color = _sanitize_color(annotations.get("background_color"))
    if background_color and background_color != "default":
        rendered = f'<span class="highlight-{background_color}_background">{rendered}</span>'

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


def _render_toggle_front(block: NotionBlock) -> str:
    """Render a toggle title via `render_blocks` for consistent front/back parsing."""
    front_block = NotionBlock(
        block_id=block.block_id,
        block_type="paragraph",
        has_children=False,
        parent_id=block.parent_id,
        parent_type=block.parent_type,
        raw={
            "id": block.block_id,
            "type": "paragraph",
            "paragraph": {"rich_text": _block_rich_text(block)},
        },
        children=(),
    )
    return render_blocks([front_block])


def _build_toggle_fields(
    *,
    block_id: str,
    front_html: str,
    back_html: str,
    back_blocks: Iterable[NotionBlock],
    card_type: str,
) -> dict[str, str]:
    """Build model fields for one parsed toggle block."""
    if card_type == INPUT:
        return {
            "Front": front_html,
            "Back": back_html,
            "Expected Answer": _raw_text_from_blocks(back_blocks),
            "Notion Block ID": block_id,
        }
    return {
        "Front": front_html,
        "Back": back_html,
        "Notion Block ID": block_id,
    }


def _model_name_for_card_type(card_type: str) -> str:
    """Return model name for one canonical card type."""
    if card_type == BASIC_REVERSED:
        return MODEL_NAME_BASIC_REVERSED
    if card_type == INPUT:
        return MODEL_NAME_INPUT
    if card_type == CLOZE:
        return MODEL_NAME_CLOZE
    return MODEL_NAME_BASIC


def _parse_top_level_cloze_paragraphs(
    page_id: str,
    blocks: list[NotionBlock],
    *,
    include_block_ids: set[str] | None = None,
) -> list[ToggleCardPayload]:
    """Parse top-level paragraphs containing cloze markers into cloze payloads."""
    payloads: list[ToggleCardPayload] = []
    consumed_indices: set[int] = set()

    for index, block in enumerate(blocks):
        if index in consumed_indices:
            continue
        if block.block_type != "paragraph":
            continue
        if include_block_ids is not None and block.block_id not in include_block_ids:
            continue
        rich_text = _block_rich_text(block)
        if not _paragraph_has_cloze_marker(rich_text):
            continue

        cloze_text = _rich_text_to_cloze_text(rich_text)
        if not cloze_text.strip():
            continue

        extra_html = ""
        next_index = index + 1
        if next_index < len(blocks):
            next_block = blocks[next_index]
            # Keep cloze-extra deterministic: consume exactly one adjacent `Extra:` paragraph.
            if _is_cloze_extra_paragraph(next_block):
                extra_html = _render_extra_paragraph_without_prefix(next_block)
                consumed_indices.add(next_index)
        model_name = _model_name_for_card_type(CLOZE)
        fields = {
            "Text": cloze_text,
            "Extra": extra_html,
            "Notion Block ID": block.block_id,
        }
        payloads.append(
            ToggleCardPayload(
                notion_page_id=page_id,
                notion_block_id=block.block_id,
                card_type=CLOZE,
                model_name=model_name,
                fields=fields,
                content_hash=_compute_payload_content_hash(
                    page_id=page_id,
                    block_id=block.block_id,
                    card_type=CLOZE,
                    model_name=model_name,
                    fields=fields,
                ),
                last_edited_time=_as_optional_string(block.raw.get("last_edited_time")),
            )
        )
    return payloads


def _is_cloze_extra_paragraph(block: NotionBlock) -> bool:
    """Return whether a paragraph starts with the cloze-extra `Extra:` prefix."""
    if block.block_type != "paragraph":
        return False
    plain_text = _rich_text_to_plain(_block_rich_text(block))
    return _CLOZE_EXTRA_PREFIX_RE.search(plain_text) is not None


def _render_extra_paragraph_without_prefix(block: NotionBlock) -> str:
    """Render one extra paragraph as HTML after stripping a leading `Extra:` prefix."""
    rich_text = _block_rich_text(block)
    stripped_rich_text = _strip_extra_prefix_from_rich_text(rich_text)
    return render_rich_text(stripped_rich_text)


def _strip_extra_prefix_from_rich_text(rich_text: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clone rich text and strip the `Extra:` prefix from the first content segment."""
    stripped_items: list[dict[str, Any]] = []
    stripped_prefix = False

    for item in rich_text:
        item_copy = dict(item)
        if not stripped_prefix and item_copy.get("type") == "equation":
            equation_payload = item_copy.get("equation")
            if isinstance(equation_payload, dict):
                expression = equation_payload.get("expression")
                if isinstance(expression, str):
                    stripped_expression = _CLOZE_EXTRA_PREFIX_RE.sub("", expression, count=1)
                    if stripped_expression != expression:
                        equation_copy = dict(equation_payload)
                        equation_copy["expression"] = stripped_expression
                        item_copy["equation"] = equation_copy
                        stripped_prefix = True
        if not stripped_prefix:
            text_payload = item_copy.get("text")
            if isinstance(text_payload, dict):
                content = text_payload.get("content")
                if isinstance(content, str):
                    stripped_content = _CLOZE_EXTRA_PREFIX_RE.sub("", content, count=1)
                    if stripped_content != content:
                        text_copy = dict(text_payload)
                        text_copy["content"] = stripped_content
                        item_copy["text"] = text_copy
                        plain_text = item_copy.get("plain_text")
                        if isinstance(plain_text, str):
                            item_copy["plain_text"] = _CLOZE_EXTRA_PREFIX_RE.sub(
                                "",
                                plain_text,
                                count=1,
                            )
                        stripped_prefix = True
        stripped_items.append(item_copy)

    return stripped_items


def _paragraph_has_cloze_marker(rich_text: Iterable[dict[str, Any]]) -> bool:
    """Return whether rich text includes a cloze marker annotation."""
    for item in rich_text:
        if _is_cloze_marker_item(item):
            return True
    return False


def _rich_text_to_cloze_text(rich_text: Iterable[dict[str, Any]]) -> str:
    """Build Anki cloze text from Notion rich text using yellow markers."""
    parts: list[str] = []
    run_parts: list[str] = []
    run_is_marker: bool | None = None

    def flush_run() -> None:
        """Append one accumulated rich-text run to the final cloze output."""
        nonlocal run_is_marker
        if not run_parts:
            return

        rendered_run = "".join(run_parts)
        parts.append(f"{{{{c1::{rendered_run}}}}}" if run_is_marker else rendered_run)
        run_parts.clear()
        run_is_marker = None

    for item in rich_text:
        if not isinstance(item, dict):
            continue
        rendered_fragment = _render_cloze_rich_text_fragment(item)
        if not rendered_fragment:
            continue
        is_marker = _is_cloze_marker_item(item)

        # Merge adjacent highlighted items so one visual phrase becomes one cloze.
        if run_is_marker is not None and is_marker != run_is_marker:
            flush_run()

        if run_is_marker is None:
            run_is_marker = is_marker
        run_parts.append(rendered_fragment)

    flush_run()
    return "".join(parts)


def _is_cloze_marker_item(item: Any) -> bool:
    """Return whether one rich-text item is marked for cloze conversion."""
    if not isinstance(item, dict):
        return False

    annotations = item.get("annotations")
    if not isinstance(annotations, dict):
        return False

    color = str(annotations.get("color") or "").strip().lower()
    background_color = str(annotations.get("background_color") or "").strip().lower()
    return color == "yellow_background" or background_color == "yellow"


def _render_cloze_rich_text_fragment(item: dict[str, Any]) -> str:
    """Render one rich-text item for the cloze field without broader text styling."""
    if item.get("type") == "equation":
        equation = item.get("equation")
        if not isinstance(equation, dict):
            return ""

        expression = _normalize_equation_expression(equation.get("expression"))
        if not expression:
            return ""
        return f'\\({html.escape(expression)}\\)'

    raw_text = _extract_text_content(item)
    if not raw_text:
        return ""
    return html.escape(raw_text)


def normalize_typed_answer(value: str) -> str:
    """Normalize a text/HTML answer for stable typed-answer matching."""
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    decoded = html.unescape(without_tags).lower()
    stripped_punct = re.sub(r"[\W_]+", " ", decoded)
    return " ".join(stripped_punct.split())


def _raw_text_from_blocks(blocks: Iterable[NotionBlock]) -> str:
    """Extract deterministic raw text from blocks while preserving equation syntax."""
    parts: list[str] = []
    for block in blocks:
        own_text = _raw_text_from_single_block(block)
        if own_text:
            parts.append(own_text)
        if block.children:
            child_text = _raw_text_from_blocks(block.children)
            if child_text:
                parts.append(child_text)
    return "\n".join(parts)


def _raw_text_from_single_block(block: NotionBlock) -> str:
    """Extract the plain-text content directly represented by one block."""
    if block.block_type == "equation":
        payload = _block_payload(block)
        expression = payload.get("expression")
        if isinstance(expression, str):
            return expression
        return ""

    if block.block_type == "table_row":
        payload = _block_payload(block)
        cells = payload.get("cells")
        if not isinstance(cells, list):
            return ""
        cell_parts: list[str] = []
        for cell in cells:
            if not isinstance(cell, list):
                continue
            rich_text_items = [item for item in cell if isinstance(item, dict)]
            plain = _rich_text_to_plain(rich_text_items)
            if plain:
                cell_parts.append(plain)
        return " | ".join(cell_parts)

    if block.block_type == "image":
        payload = _block_payload(block)
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


def _render_highlighted_code(code_text: str, canonical_language: str) -> str:
    """Apply lightweight syntax highlighting spans for supported languages."""
    if not code_text:
        return ""
    if not canonical_language:
        return html.escape(code_text)

    keywords = _LANGUAGE_KEYWORDS.get(canonical_language, frozenset())
    type_names = _LANGUAGE_TYPES.get(canonical_language, frozenset())
    line_comments, block_comments = _comment_delimiters(canonical_language)
    chunks: list[str] = []
    index = 0

    # Parse left-to-right so comments/strings win before keyword matching.
    while index < len(code_text):
        comment_html, next_index = _consume_comment(code_text, index, line_comments, block_comments)
        if comment_html is not None:
            chunks.append(comment_html)
            index = next_index
            continue

        string_html, next_index = _consume_string(code_text, index)
        if string_html is not None:
            chunks.append(string_html)
            index = next_index
            continue

        number_match = _NUMBER_RE.match(code_text, index)
        if number_match is not None:
            token = number_match.group(0)
            chunks.append(_highlight_token("number", token))
            index = number_match.end()
            continue

        identifier_match = _IDENTIFIER_RE.match(code_text, index)
        if identifier_match is not None:
            token = identifier_match.group(0)
            chunks.append(
                _highlight_identifier(
                    token=token,
                    source=code_text,
                    token_end=identifier_match.end(),
                    keywords=keywords,
                    type_names=type_names,
                )
            )
            index = identifier_match.end()
            continue

        char = code_text[index]
        if char in _OPERATOR_CHARS:
            chunks.append(_highlight_token("operator", char))
        elif char in _PUNCTUATION_CHARS:
            chunks.append(_highlight_token("punctuation", char))
        else:
            chunks.append(html.escape(char))
        index += 1

    return "".join(chunks)


def _comment_delimiters(canonical_language: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Return line and block comment delimiters for one canonical language."""
    if canonical_language in {"python", "bash", "yaml"}:
        return ("#",), ()
    if canonical_language in {"javascript", "typescript", "java", "c", "cpp", "csharp", "go", "rust", "css"}:
        return ("//",), (("/*", "*/"),)
    if canonical_language == "sql":
        return ("--", "#"), (("/*", "*/"),)
    if canonical_language == "html":
        return (), (("<!--", "-->"),)
    return (), ()


def _consume_comment(
    source: str,
    index: int,
    line_comments: tuple[str, ...],
    block_comments: tuple[tuple[str, str], ...],
) -> tuple[str | None, int]:
    """Consume a line or block comment token when one starts at index."""
    for start, end in block_comments:
        if source.startswith(start, index):
            close_at = source.find(end, index + len(start))
            token_end = len(source) if close_at == -1 else close_at + len(end)
            return _highlight_token("comment", source[index:token_end]), token_end

    for marker in line_comments:
        if source.startswith(marker, index):
            newline_at = source.find("\n", index)
            token_end = len(source) if newline_at == -1 else newline_at
            return _highlight_token("comment", source[index:token_end]), token_end

    return None, index


def _consume_string(source: str, index: int) -> tuple[str | None, int]:
    """Consume a quoted string when one starts at index."""
    if index >= len(source):
        return None, index

    quote = source[index]
    if quote not in {"'", '"', "`"}:
        return None, index

    triple_quote = source.startswith(quote * 3, index)
    cursor = index + (3 if triple_quote else 1)
    while cursor < len(source):
        if triple_quote and source.startswith(quote * 3, cursor):
            cursor += 3
            break
        if not triple_quote and source[cursor] == quote:
            cursor += 1
            break
        if source[cursor] == "\\" and cursor + 1 < len(source):
            cursor += 2
            continue
        cursor += 1

    return _highlight_token("string", source[index:cursor]), cursor


def _highlight_identifier(
    *,
    token: str,
    source: str,
    token_end: int,
    keywords: frozenset[str],
    type_names: frozenset[str],
) -> str:
    """Classify identifiers as keyword/type/function or leave plain."""
    token_lower = token.lower()
    if token_lower in keywords:
        return _highlight_token("keyword", token)
    if token in type_names or token_lower in type_names:
        return _highlight_token("type", token)
    if _has_call_suffix(source, token_end):
        return _highlight_token("function", token)
    return html.escape(token)


def _has_call_suffix(source: str, token_end: int) -> bool:
    """Return True when an identifier is followed by an opening call parenthesis."""
    cursor = token_end
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    return cursor < len(source) and source[cursor] == "("


def _highlight_token(token_type: str, value: str) -> str:
    """Wrap one token in a syntax span class."""
    return f'<span class="notion-syn-{token_type}">{html.escape(value)}</span>'


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


def _sanitize_media_href(value: Any) -> str:
    """Return a safe absolute URL suitable for media fetching."""
    if not isinstance(value, str):
        return ""

    href = value.strip()
    if not href:
        return ""

    scheme = urlsplit(href).scheme.lower()
    if scheme in {"http", "https"}:
        return href
    return ""


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

    cleaned = value.strip().lower().replace(" ", "-")
    if not cleaned:
        return ""

    canonical = _canonicalize_language(cleaned)
    if canonical:
        return canonical

    if set(cleaned) <= _SAFE_LANG_CHARS:
        return cleaned
    return ""


def _canonicalize_language(language: str) -> str:
    """Map a language/alias to its canonical class name."""
    if not language:
        return ""
    return _LANGUAGE_ALIASES.get(language, "")


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
    return _compute_payload_content_hash(
        page_id=page_id,
        block_id=block_id,
        card_type=BASIC,
        model_name=MODEL_NAME_BASIC,
        fields={
            "Front": front_html,
            "Back": back_html,
            "Notion Block ID": block_id,
        },
    )


def _compute_payload_content_hash(
    *,
    page_id: str,
    block_id: str,
    card_type: str,
    model_name: str,
    fields: dict[str, str],
) -> str:
    """Compute a deterministic content hash for any typed payload."""
    ordered_fields = "\n".join(
        f"{key}={fields[key]}"
        for key in sorted(fields)
    )
    payload = (
        f"{page_id}\n"
        f"{block_id}\n"
        f"{card_type}\n"
        f"{model_name}\n"
        f"{ordered_fields}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
