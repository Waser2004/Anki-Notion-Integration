"""Canonicalize Notion Markdown snapshots for deterministic change detection."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit


_QUOTED_URL_RE = re.compile(
    r'(?P<prefix>\b(?:src|url)=")(?P<url>https?://[^"]+)(?P<suffix>")',
    re.IGNORECASE,
)
_MARKDOWN_URL_RE = re.compile(
    r"(?P<prefix>\]\()(?P<url>https?://(?:\\.|[^)\s])+)(?P<suffix>\))",
)
_DETAILS_OPEN_RE = re.compile(r"^(?P<indent>\t*)<details(?:\s[^>]*)?>\s*$")
_DETAILS_CLOSE_RE = re.compile(r"^\t*</details>\s*$")
_SIGNED_QUERY_KEYS = frozenset(
    {
        "expires",
        "key-pair-id",
        "policy",
        "signature",
    }
)


def canonicalize_notion_markdown(markdown: str) -> str:
    """Return stable Markdown with transport-only URL signatures removed."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _QUOTED_URL_RE.sub(_replace_transient_url, normalized)
    normalized = _MARKDOWN_URL_RE.sub(_replace_transient_url, normalized)

    # Notion block boundaries do not use trailing spaces, so removing them
    # prevents harmless serialization whitespace from invalidating snapshots.
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def hash_notion_markdown(markdown: str) -> str:
    """Return a SHA-256 hash of canonical Notion Markdown."""
    canonical = canonicalize_notion_markdown(markdown)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_root_toggle_markdown(markdown: str) -> tuple[str, ...] | None:
    """Return top-level regular-toggle sources, or ``None`` when malformed."""
    canonical = canonicalize_notion_markdown(markdown)
    if not canonical:
        return ()

    lines                    = canonical.split("\n")
    sources:      list[str]  = []
    start_index:  int | None = None
    details_depth            = 0
    fence_marker: str | None = None

    for index, line in enumerate(lines):
        stripped = line.lstrip("\t")
        marker   = _fence_marker(stripped)
        if marker is not None:
            if fence_marker is None:
                fence_marker = marker
            elif marker.startswith(fence_marker):
                fence_marker = None
            continue
        if fence_marker is not None:
            continue

        # Detect the start of a <details> block, skipping any nested blocks that belong to a parent.
        opened = _DETAILS_OPEN_RE.fullmatch(line)
        if opened is not None:
            indent = opened.group("indent")
            if start_index is None:
                if indent:
                    continue

                start_index = index
                details_depth = 1
                continue

            details_depth += 1
            continue

        # Detect the end of a <details> block, skipping any nested blocks that belong to a parent.
        if start_index is None or _DETAILS_CLOSE_RE.fullmatch(line) is None:
            continue
        details_depth -= 1
        if details_depth < 0:
            return None

        # When the depth returns to zero, we have a complete top-level <details> block.
        # Add details content to sources and reset the start index for the next block.
        if details_depth == 0:
            sources.append("\n".join(lines[start_index : index + 1]))
            start_index = None

    if start_index is not None or fence_marker is not None:
        return None
    return tuple(sources)


def _replace_transient_url(match: re.Match[str]) -> str:
    """Preserve surrounding Markdown syntax while canonicalizing one URL."""
    return (
        f"{match.group('prefix')}"
        f"{_canonicalize_url(match.group('url'))}"
        f"{match.group('suffix')}"
    )


def _canonicalize_url(value: str) -> str:
    """Strip only query parameters that identify an expiring signed URL."""
    parsed = urlsplit(value)
    query_keys = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    has_signature = any(key.startswith("x-amz-") for key in query_keys) or bool(
        query_keys & _SIGNED_QUERY_KEYS
    )
    if not has_signature:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _fence_marker(line: str) -> str | None:
    """Return the opening backtick/tilde run for a fenced code-block line."""
    match = re.match(r"^(?P<marker>`{3,}|~{3,})", line)
    return None if match is None else match.group("marker")
