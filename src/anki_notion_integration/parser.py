"""Parse Notion block trees into deterministic card payloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from .notion_client import NotionBlock

_SAFE_COLOR_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")
_SAFE_LANG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_UNSAFE_LINK_SCHEMES = frozenset({"javascript", "data", "vbscript"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")
_OPERATOR_CHARS = frozenset("+-*/%=!<>|&^~?:")
_PUNCTUATION_CHARS = frozenset("()[]{}.,;")

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
    """Render a code block with lightweight syntax-highlight spans when possible."""
    payload = _block_payload(block)
    code_rich_text = payload.get("rich_text") if isinstance(payload.get("rich_text"), list) else []
    code_text = _rich_text_to_plain(code_rich_text)
    language = _sanitize_language(payload.get("language"))
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
    payload = f"{page_id}\n{block_id}\n{front_html}\n{back_html}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
