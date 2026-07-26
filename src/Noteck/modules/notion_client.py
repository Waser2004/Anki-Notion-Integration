"""Notion API client helpers for Noteck."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import html
import json
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib import error, parse, request

from .db import Database
from .settings import SettingsStore


NOTION_API_VERSION = "2026-03-11"
NOTION_BLOCK_PAGE_SIZE = 100
NOTION_TREE_WORKER_COUNT = 4
NOTION_TREE_QUEUE_SIZE = NOTION_TREE_WORKER_COUNT * 2
NOTION_PAGE_WORKER_COUNT = 4
NOTION_PAGE_QUEUE_SIZE = NOTION_PAGE_WORKER_COUNT * 2
NOTION_REQUESTS_PER_SECOND = 3.0
NOTION_RATE_LIMIT_RETRIES = 5


class NotionApiError(RuntimeError):
    """Raised when the Notion API returns an error response."""

    def __init__(
        self,
        message: str,
        status:  int | None               = None,
        payload: Any | None               = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.headers = dict(headers or {})


class NotionTransportError(RuntimeError):
    """Raised when the HTTP transport fails before a response is received."""


@dataclass(frozen=True)
class NotionResponse:
    """HTTP response wrapper used by the Notion client transport."""

    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class NotionPage:
    """Normalized Notion page metadata returned by the search endpoint."""

    page_id: str
    title: str
    icon: dict[str, Any] | None
    parent_id: str | None
    parent_type: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PageNode:
    """Tree node for hierarchical page navigation."""

    page: NotionPage
    children: tuple["PageNode", ...]


@dataclass(frozen=True)
class NotionBlock:
    """Normalized Notion block including nested children."""

    block_id: str
    block_type: str
    has_children: bool
    parent_id: str | None
    parent_type: str | None
    raw: dict[str, Any]
    children: tuple["NotionBlock", ...] = ()


_MARKDOWN_TABLE_RE = re.compile(
    r"<table\b[^>]*>(?P<body>.*?)</table>", re.IGNORECASE | re.DOTALL
)
_MARKDOWN_COLGROUP_RE = re.compile(
    r"<colgroup\b[^>]*>(?P<body>.*?)</colgroup>", re.IGNORECASE | re.DOTALL
)
_MARKDOWN_COL_RE = re.compile(r"<col\b(?P<attrs>[^>]*)/?>", re.IGNORECASE)
_MARKDOWN_ROW_RE = re.compile(
    r"<tr\b(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL
)
_MARKDOWN_CELL_RE = re.compile(
    r"<(?:td|th)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?:td|th)>",
    re.IGNORECASE | re.DOTALL,
)
_MARKDOWN_ATTRIBUTE_RE = re.compile(
    r"([A-Za-z][\w-]*)\s*=\s*([\"'])(.*?)\2", re.DOTALL
)
_MARKDOWN_LINK_START_RE = re.compile(
    r"(?<!!)\[(?P<label>(?:\\.|[^\]])*)\]\("
)
_MARKDOWN_CODE_RE = re.compile(
    r"(?P<fence>`+)(?P<body>.*?)(?P=fence)", re.DOTALL
)
_MARKDOWN_MATH_RE = re.compile(
    r"(?<!\\)\$(?P<body>.*?)(?<!\\)\$", re.DOTALL
)
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(?P<body>.+?)\*\*", re.DOTALL)
_MARKDOWN_ITALIC_RE = re.compile(
    r"(?<!\*)\*(?!\*)(?P<body>.+?)(?<!\*)\*(?!\*)", re.DOTALL
)
_MARKDOWN_STRIKETHROUGH_RE = re.compile(r"~~(?P<body>.+?)~~", re.DOTALL)
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\*~`$\[\]<>^|{}])")
_MARKDOWN_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_MARKDOWN_DATE_MENTION_RE = re.compile(
    r"<mention-date\b(?P<attrs>[^>]*)/?>",
    re.IGNORECASE,
)
_MARKDOWN_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_UNKNOWN_RE = re.compile(
    r"<unknown\b(?P<attrs>[^>]*)/?>",
    re.IGNORECASE | re.DOTALL,
)


def merge_markdown_table_colors(
    blocks: Iterable[NotionBlock],
    markdown: str,
) -> list[NotionBlock]:
    """Overlay enhanced-Markdown table colors onto the block-API tree."""
    markdown_tables = _extract_markdown_table_colors(markdown)
    if not markdown_tables:
        return list(blocks)

    used_tables: set[int] = set()

    def enrich(block: NotionBlock) -> NotionBlock:
        """Recursively enrich tables without mutating cached block objects."""
        prepared_children = tuple(enrich(child) for child in block.children)
        current = block
        if block.block_type == "table":
            table_index = _match_markdown_table(block, markdown_tables, used_tables)
            if table_index is not None:
                current = _apply_table_colors(block, markdown_tables[table_index])
        if current is block and prepared_children != block.children:
            current = _copy_block(block, children=prepared_children)
        return current

    return [enrich(block) for block in blocks]


def _extract_markdown_table_colors(markdown: str) -> list[dict[str, Any]]:
    """Parse table color attributes from enhanced Markdown."""
    tables: list[dict[str, Any]] = []
    for table_match in _MARKDOWN_TABLE_RE.finditer(markdown or ""):
        body = table_match.group("body")
        column_colors: list[str | None] = []
        colgroup_match = _MARKDOWN_COLGROUP_RE.search(body)
        if colgroup_match:
            column_colors = [
                _markdown_color(_markdown_attributes(match.group("attrs")).get("color"))
                for match in _MARKDOWN_COL_RE.finditer(colgroup_match.group("body"))
            ]

        rows: list[dict[str, Any]] = []
        for row_match in _MARKDOWN_ROW_RE.finditer(body):
            row_color = _markdown_color(
                _markdown_attributes(row_match.group("attrs")).get("color")
            )
            cells = [
                {
                    "text": _normalize_table_text(
                        cell_match.group("body"),
                        enhanced_markdown=True,
                    ),
                    "color": _markdown_color(
                        _markdown_attributes(cell_match.group("attrs")).get("color")
                    ),
                }
                for cell_match in _MARKDOWN_CELL_RE.finditer(row_match.group("body"))
            ]
            rows.append({"color": row_color, "cells": cells})
        tables.append({"column_colors": column_colors, "rows": rows})
    return tables


def _markdown_attributes(raw_attributes: str) -> dict[str, str]:
    """Return lowercase enhanced-Markdown attributes from one tag."""
    return {
        name.lower(): value.strip()
        for name, _, value in _MARKDOWN_ATTRIBUTE_RE.findall(raw_attributes)
    }


def _notion_id_key(value: Any) -> str:
    """Normalize dashed and compact Notion IDs for URL-fragment matching."""
    return str(value or "").strip().lower().replace("-", "")


def _replace_unknown_markdown_tags(
    markdown: str,
    replacements: Mapping[str, str],
) -> str:
    """Replace fetched ``<unknown>`` placeholders at their original positions."""
    normalized_replacements = {
        _notion_id_key(block_id): replacement
        for block_id, replacement in replacements.items()
        if _notion_id_key(block_id) and replacement
    }

    def replace(match: re.Match[str]) -> str:
        """Match an unknown block through the ID in its Notion URL."""
        attributes = _markdown_attributes(match.group("attrs"))
        block_url = attributes.get("url", "")
        parsed_url = parse.urlparse(block_url)
        candidates = (
            parsed_url.fragment,
            parsed_url.path.rstrip("/").rsplit("/", 1)[-1],
        )
        for candidate in candidates:
            replacement = normalized_replacements.get(_notion_id_key(candidate))
            if replacement is not None:
                return replacement
        return match.group(0)

    return _MARKDOWN_UNKNOWN_RE.sub(replace, markdown or "")


def _markdown_color(value: Any) -> str | None:
    """Normalize an enhanced-Markdown color value."""
    color = str(value or "").strip().lower()
    return color or None


def _normalize_table_text(value: str, *, enhanced_markdown: bool = False) -> str:
    """Normalize table cell text for matching the two API representations."""
    prepared = value or ""
    protected: list[str] = []

    if enhanced_markdown:
        # Inline code and equations are literal content. Protect them before removing Markdown delimiters or XML-like formatting tags.
        def protect_value(literal: str) -> str:
            token = f"\ue000{len(protected)}\ue001"
            protected.append(literal)
            return token

        def protect_literal(match: re.Match[str]) -> str:
            return protect_value(match.group("body"))

        # Protect literal content before removing Markdown formatting or HTML-like tags.
        prepared = _MARKDOWN_CODE_RE.sub(protect_literal, prepared)
        prepared = _MARKDOWN_MATH_RE.sub(protect_literal, prepared)
        prepared = _MARKDOWN_ESCAPE_RE.sub(
            lambda match: protect_value(match.group(1)),
            prepared,
        )
        prepared = _strip_markdown_link_destinations(prepared)
        for formatting_pattern in (
            _MARKDOWN_BOLD_RE,
            _MARKDOWN_STRIKETHROUGH_RE,
            _MARKDOWN_ITALIC_RE,
        ):
            # Repeating handles nested combinations such as bold italic text.
            while True:
                normalized = formatting_pattern.sub(
                    lambda match: match.group("body"),
                    prepared,
                )
                if normalized == prepared:
                    break
                prepared = normalized
        prepared = _MARKDOWN_BREAK_RE.sub(" ", prepared)
        prepared = _MARKDOWN_DATE_MENTION_RE.sub(
            lambda match: _markdown_date_mention_text(match.group("attrs")),
            prepared,
        )

    plain = html.unescape(_MARKDOWN_TAG_RE.sub("", prepared))
    for index, literal in enumerate(protected):
        plain = plain.replace(f"\ue000{index}\ue001", literal)
    return " ".join(plain.split()).strip()


def _strip_markdown_link_destinations(value: str) -> str:
    """Keep link labels while consuming balanced Markdown destinations."""
    parts: list[str] = []
    cursor = 0
    while True:
        match = _MARKDOWN_LINK_START_RE.search(value, cursor)
        if match is None:
            parts.append(value[cursor:])
            break

        depth = 1
        destination_index = match.end()
        while destination_index < len(value) and depth:
            character = value[destination_index]
            if character == "\\" and destination_index + 1 < len(value):
                destination_index += 2
                continue

            # Consume balanced parentheses in the Markdown link destination.
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                
            destination_index += 1

        # link never closed, preserve the rest of the string as-is
        if depth:
            # Preserve malformed or incomplete input rather than dropping text.
            parts.append(value[cursor:])
            break

        parts.append(value[cursor:match.start()])
        parts.append(match.group("label"))
        cursor = destination_index

    return "".join(parts)


def _markdown_date_mention_text(raw_attributes: str) -> str:
    """Return the block-API-style visible text for one Markdown date mention."""
    attributes = _markdown_attributes(raw_attributes)
    start = attributes.get("start", "")
    end = attributes.get("end", "")
    if start and end:
        return f"{start} → {end}"
    return start


def _table_text_matrix(block: NotionBlock) -> list[list[str]]:
    """Extract a normalized text matrix from a block-API table."""
    matrix: list[list[str]] = []
    for row in block.children:
        if row.block_type != "table_row":
            continue
        payload = row.raw.get("table_row")
        cells = payload.get("cells") if isinstance(payload, dict) else None
        row_text: list[str] = []
        for cell in cells if isinstance(cells, list) else []:
            fragments = cell if isinstance(cell, list) else []
            values = [
                str(item.get("plain_text") or item.get("text", {}).get("content") or "")
                for item in fragments
                if isinstance(item, dict)
            ]
            row_text.append(_normalize_table_text("".join(values)))
        matrix.append(row_text)
    return matrix


def _match_markdown_table(
    block: NotionBlock,
    tables: list[dict[str, Any]],
    used_tables: set[int],
) -> int | None:
    """Find the unused enhanced-Markdown table with the same cell text."""
    block_matrix = _table_text_matrix(block)
    for index, table in enumerate(tables):
        if index in used_tables:
            continue
        markdown_matrix = [
            [cell["text"] for cell in row["cells"]] for row in table["rows"]
        ]
        if markdown_matrix == block_matrix:
            used_tables.add(index)
            return index
    return None


def _apply_table_colors(block: NotionBlock, table: dict[str, Any]) -> NotionBlock:
    """Store effective Markdown cell colors on matching table-row payloads."""
    rows = table["rows"]
    column_colors = table["column_colors"]
    prepared_children: list[NotionBlock] = []
    row_index = 0
    for child in block.children:
        if child.block_type != "table_row" or row_index >= len(rows):
            prepared_children.append(child)
            continue

        row = rows[row_index]
        payload = child.raw.get("table_row")
        if not isinstance(payload, dict):
            prepared_children.append(child)
            row_index += 1
            continue

        colors = [
            cell["color"]
            or row["color"]
            or (column_colors[index] if index < len(column_colors) else None)
            for index, cell in enumerate(row["cells"])
        ]
        raw = dict(child.raw)
        row_payload = dict(payload)
        row_payload["_noteck_cell_colors"] = colors
        raw["table_row"] = row_payload
        prepared_children.append(_copy_block(child, raw=raw))
        row_index += 1

    return _copy_block(block, children=tuple(prepared_children))


def _copy_block(
    block: NotionBlock,
    *,
    raw: dict[str, Any] | None = None,
    children: tuple[NotionBlock, ...] | None = None,
) -> NotionBlock:
    """Copy one immutable normalized block with selected replacements."""
    return NotionBlock(
        block_id=block.block_id,
        block_type=block.block_type,
        has_children=block.has_children,
        parent_id=block.parent_id,
        parent_type=block.parent_type,
        raw=block.raw if raw is None else raw,
        children=block.children if children is None else children,
    )


@dataclass(frozen=True)
class NotionMarkdownSnapshot:
    """Complete enhanced-Markdown response for one Notion page or subtree."""

    page_id: str
    markdown: str
    truncated: bool
    unknown_block_ids: tuple[str, ...]


@dataclass(frozen=True)
class NotionPageSyncData:
    """Notion source data required before one page can be reconciled."""

    page_id: str
    last_edited_time: str | None
    markdown_snapshot: NotionMarkdownSnapshot
    shallow_blocks: tuple[NotionBlock, ...]


@dataclass(frozen=True)
class NotionPageFetchResult:
    """One page-preparation result, including an isolated fetch failure."""

    page_id: str
    data: NotionPageSyncData | None = None
    error: Exception | None = None


@dataclass(frozen=True)
class _BlockFetchJob:
    """One page-tree queue item identifying a parent whose children are needed."""

    page_id:         str
    parent_block_id: str


@dataclass(frozen=True)
class _BlockFetchResult:
    """One completed fetch plus jobs discovered while processing it."""

    job:        _BlockFetchJob
    child_jobs: tuple[_BlockFetchJob, ...]
    error:      Exception | None = None


class _AsyncRateLimiter:
    """Serialize request starts to a shared average requests-per-second limit."""

    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / requests_per_second
        self._next_request_at = 0.0

        self._next_request_at_lock = threading.Lock()

    async def acquire(self) -> None:
        """Wait until the next request may start."""
        while True:
            with self._next_request_at_lock:
                now = time.monotonic()
                delay = self._next_request_at - now
                if delay <= 0:
                    self._next_request_at = now + self._interval
                    return

            await asyncio.sleep(delay)

    async def defer(self, delay_seconds: float) -> None:
        """Prevent every worker from retrying before a shared server deadline."""
        with self._next_request_at_lock:
            retry_at              = time.monotonic() + max(0.0, delay_seconds)
            self._next_request_at = max(self._next_request_at, retry_at)


# Keep this alias Python 3.9-compatible because it is evaluated at import time.
Transport = Callable[[str, str, dict[str, str], Optional[bytes], float], NotionResponse]
PageFetchProgressCallback = Callable[[int, int], None]


class NotionClient:
    """Small Notion REST API wrapper focused on pages and toggle blocks."""

    def __init__(
        self,
        api_token: str,
        notion_version: str = NOTION_API_VERSION,
        base_url: str = "https://api.notion.com/v1",
        timeout_seconds: float = 15.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_token:
            raise NotionApiError("Notion API token is required.")
        
        self._api_token = api_token
        self._notion_version = notion_version
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport or self._default_transport
        self._block_parent_page_cache: dict[str, str | None] = {}
        self._page_parent_type_cache: dict[str, str | None] = {}
        self._tree_rate_limiter = _AsyncRateLimiter(NOTION_REQUESTS_PER_SECOND)

    @classmethod
    def from_settings(
        cls,
        db: Database,
        profile_name: str | None = None,
        notion_version: str = NOTION_API_VERSION,
        base_url: str = "https://api.notion.com/v1",
        timeout_seconds: float = 15.0,
        transport: Transport | None = None,
    ) -> "NotionClient":
        """Create a client using the stored Notion API token."""
        store = SettingsStore(db, profile_name=profile_name)
        api_token = store.get_value("notion_api_key")
        if not api_token:
            raise NotionApiError("Notion API token is not set in settings.")

        return cls(
            api_token=str(api_token),
            notion_version=notion_version,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def list_pages(self, include_database_pages: bool = False) -> list[NotionPage]:
        """Return all accessible Notion pages using the search endpoint."""
        return list(self.iter_pages(include_database_pages=include_database_pages))

    def iter_pages(self, include_database_pages: bool = False) -> Iterable[NotionPage]:
        """Yield accessible Notion pages as they are received from the API."""
        payload: dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
        }

        for page_payload in self._paginate_search(payload):
            page = self._normalize_page(page_payload)
            raw_parent = page_payload.get("parent") or {}
            raw_parent_type = raw_parent.get("type")
            if raw_parent_type != "block_id":
                self._page_parent_type_cache[page.page_id] = (
                    str(raw_parent_type) if raw_parent_type else None
                )

            if not include_database_pages and page.parent_type in {"database_id", "data_source_id"}:
                continue
            if (
                not include_database_pages
                and raw_parent_type == "block_id"
                and page.parent_type == "page_id"
                and page.parent_id
                and self._is_database_page(page.parent_id)
            ):
                continue
            yield page

    def list_pages_tree(self, include_database_pages: bool = False) -> list[PageNode]:
        """Return pages organized into a parent/child hierarchy."""
        children_map: dict[str | None, list[NotionPage]] = {}
        roots: list[NotionPage] = []

        # fetch all pages
        pages = self.list_pages(include_database_pages=include_database_pages)
        pages_by_id = {page.page_id: page for page in pages}

        for page in pages:
            parent_id = page.parent_id if page.parent_type == "page_id" else None

            # collect root pages
            if parent_id is None or parent_id not in pages_by_id:
                roots.append(page)
                continue

            # add child to parents child list
            children_map.setdefault(parent_id, []).append(page)

        def build_node(page: NotionPage, seen: set[str]) -> PageNode:
            """Recursively build a PageNode tree, avoiding cycles."""
            if page.page_id in seen:
                return PageNode(page=page, children=())
            
            next_seen = set(seen)
            next_seen.add(page.page_id)
            children = tuple(
                build_node(child, next_seen)
                for child in children_map.get(page.page_id, [])
            )

            return PageNode(page=page, children=children)

        # build tree starting from root pages
        return [build_node(page, set()) for page in roots]

    def get_page_content(self, page_id: str) -> list[NotionBlock]:
        """Return the full block tree for a page."""
        return asyncio.run(self._fetch_page_tree(page_id))

    def get_pages_sync_data(
        self,
        page_ids: Iterable[str],
        *,
        progress_callback: PageFetchProgressCallback | None = None,
    ) -> dict[str, NotionPageFetchResult]:
        """Fetch sync inputs for pages through a bounded asynchronous queue."""
        ordered_page_ids = tuple(dict.fromkeys(str(page_id) for page_id in page_ids))
        if not ordered_page_ids:
            return {}

        return asyncio.run(
            self._fetch_pages_sync_data(
                ordered_page_ids,
                progress_callback=progress_callback,
            )
        )

    def get_page_markdown(self, page_id: str) -> NotionMarkdownSnapshot:
        """Return a page's complete enhanced Markdown representation."""
        payload = asyncio.run(
            self._fetch_complete_markdown_payload(
                page_id,
                visited=frozenset(),
            )
        )
        unknown_block_ids = payload.get("unknown_block_ids")
        if not isinstance(unknown_block_ids, list):
            unknown_block_ids = []

        return NotionMarkdownSnapshot(
            page_id=str(payload.get("id") or page_id),
            markdown=str(payload.get("markdown") or ""),
            truncated=bool(payload.get("truncated")),
            unknown_block_ids=tuple(str(block_id) for block_id in unknown_block_ids),
        )

    def get_page_last_edited_time(self, page_id: str) -> str | None:
        """Return page-level edit metadata for sync diagnostics and persistence."""
        payload = asyncio.run(
            self._request_json_with_rate_limit_retry(
                "GET",
                f"/pages/{page_id}",
                None,
                limiter=self._tree_rate_limiter,
            )
        )
        last_edited_time = payload.get("last_edited_time")
        if isinstance(last_edited_time, str) and last_edited_time:
            return last_edited_time
        return None

    def get_page_blocks_shallow(self, page_id: str) -> list[NotionBlock]:
        """Return the page's direct child blocks without expanding nested children."""
        return asyncio.run(
            self._fetch_block_children_async(
                page_id,
                limiter=self._tree_rate_limiter,
            )
        )

    def get_block(self, block_id: str) -> NotionBlock:
        """Return a single Notion block without expanding its children."""
        payload = self._request_json("GET", f"/blocks/{block_id}", None)
        return self._normalize_block(payload)

    def get_block_children_recursive(self, block_id: str) -> list[NotionBlock]:
        """Return the full block tree under the given block id."""
        return asyncio.run(self._fetch_page_tree(block_id))

    def update_toggle(self, block_id: str, title: str, body: str) -> None:
        """Update a toggle block title and replace its child blocks."""
        self._update_toggle_title(block_id, title)
        self._replace_block_children(block_id, body)

    def build_child_page_order_map(
        self,
        pages_by_id: Mapping[str, NotionPage],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, tuple[str, ...]]:
        """Return sibling order hints from parent-page block order for known children."""
        children_by_parent: dict[str, list[str]] = {}
        for page_id, page in pages_by_id.items():
            parent_id = page.parent_id if page.parent_type == "page_id" else None
            if not parent_id:
                continue
            if parent_id not in pages_by_id:
                continue
            children_by_parent.setdefault(parent_id, []).append(page_id)

        ordered_children_by_parent: dict[str, tuple[str, ...]] = {}
        for parent_page_id, current_child_ids in children_by_parent.items():
            # Ordering is best-effort metadata; stop between parent requests as
            # soon as the owning refresh asks to cancel.
            if should_cancel is not None and should_cancel():
                break
            # Single-child parents do not need an extra API call.
            if len(current_child_ids) <= 1:
                continue

            ordered_child_page_ids = self._list_direct_child_page_ids(parent_page_id)
            if not ordered_child_page_ids:
                continue

            known_child_ids = set(current_child_ids)
            ordered_known_child_ids = [
                child_page_id
                for child_page_id in ordered_child_page_ids
                if child_page_id in known_child_ids
            ]
            if not ordered_known_child_ids:
                continue

            # Keep all known children; if Notion omits any from `child_page` blocks,
            # preserve the current relative order by appending those items.
            ordered_known_child_id_set = set(ordered_known_child_ids)
            merged_order = list(ordered_known_child_ids)
            for child_page_id in current_child_ids:
                if child_page_id in ordered_known_child_id_set:
                    continue
                merged_order.append(child_page_id)

            if merged_order != current_child_ids:
                ordered_children_by_parent[parent_page_id] = tuple(merged_order)

        return ordered_children_by_parent

    def _paginate_search(self, payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
        """Yield search results across all pages."""
        next_cursor: str | None = None
        
        while True:
            # request payload with pagination cursor if present
            request_payload = dict(payload)
            if next_cursor:
                request_payload["start_cursor"] = next_cursor
            
            # iteratively fetch pages
            response = self._request_json("POST", "/search", request_payload)
            for result in response.get("results", []):
                yield result
            
            if not response.get("has_more"):
                return
            
            next_cursor = response.get("next_cursor")
            if not next_cursor:
                return

    def _list_direct_child_page_ids(self, page_id: str) -> tuple[str, ...]:
        """Return direct child-page ids in their top-to-bottom block order."""
        child_page_ids: list[str] = []
        seen_child_page_ids: set[str] = set()

        for block_payload in self._fetch_block_children(page_id):
            if str(block_payload.get("type")) != "child_page":
                continue

            child_page_id = block_payload.get("id")
            if not child_page_id:
                continue

            child_page_id = str(child_page_id)
            if child_page_id in seen_child_page_ids:
                continue
            seen_child_page_ids.add(child_page_id)
            child_page_ids.append(child_page_id)

        return tuple(child_page_ids)

    def _normalize_page(self, payload: dict[str, Any]) -> NotionPage:
        """Extract normalized page metadata from a raw API payload."""
        # extract parent information
        parent = payload.get("parent") or {}
        parent_type = parent.get("type")
        parent_id = parent.get(parent_type) if parent_type else None

        # Notion pages nested inside blocks (for example callouts/toggles) have a
        # `block_id` parent. Resolve that block ancestry to the owning page so the
        # UI can render correct page hierarchy instead of promoting them to root.
        if parent_type == "block_id" and isinstance(parent_id, str) and parent_id:
            resolved_parent_page_id = self._resolve_parent_page_id_from_block(parent_id)
            if resolved_parent_page_id:
                parent_type = "page_id"
                parent_id = resolved_parent_page_id

        # build NotionPage
        return NotionPage(
            page_id     = str(payload.get("id")),
            title       = self._extract_page_title(payload),
            icon        = self._normalize_icon(payload.get("icon")),
            parent_id   = str(parent_id) if parent_id else None,
            parent_type = str(parent_type) if parent_type else None,
            raw         = payload,
        )

    def _resolve_parent_page_id_from_block(self, block_id: str) -> str | None:
        """Resolve a block parent chain to its containing page id when possible."""
        if block_id in self._block_parent_page_cache:
            return self._block_parent_page_cache[block_id]

        visited: list[str] = []
        current_block_id: str | None = block_id

        while current_block_id:
            cached_parent_page_id = self._block_parent_page_cache.get(current_block_id)
            if current_block_id in self._block_parent_page_cache:
                for visited_block_id in visited:
                    self._block_parent_page_cache[visited_block_id] = cached_parent_page_id
                return cached_parent_page_id

            visited.append(current_block_id)
            try:
                block_payload = self._request_json("GET", f"/blocks/{current_block_id}", None)
            except (NotionApiError, NotionTransportError):
                for visited_block_id in visited:
                    self._block_parent_page_cache[visited_block_id] = None
                return None

            parent_payload = block_payload.get("parent") or {}
            parent_type = parent_payload.get("type")
            if parent_type == "page_id":
                parent_page_id = parent_payload.get("page_id")
                resolved_parent_page_id = str(parent_page_id) if parent_page_id else None
                for visited_block_id in visited:
                    self._block_parent_page_cache[visited_block_id] = resolved_parent_page_id
                return resolved_parent_page_id

            if parent_type != "block_id":
                for visited_block_id in visited:
                    self._block_parent_page_cache[visited_block_id] = None
                return None

            next_block_id = parent_payload.get("block_id")
            current_block_id = str(next_block_id) if next_block_id else None

        for visited_block_id in visited:
            self._block_parent_page_cache[visited_block_id] = None
        return None

    def _is_database_page(self, page_id: str) -> bool:
        """Return whether a page is a row in a database or data source."""
        if page_id not in self._page_parent_type_cache:
            payload     = self._request_json("GET", f"/pages/{page_id}", None)
            parent      = payload.get("parent") or {}
            parent_type = parent.get("type")
            
            self._page_parent_type_cache[page_id] = str(parent_type) if parent_type else None

        return self._page_parent_type_cache[page_id] in {"database_id", "data_source_id"}

    def _extract_page_title(self, payload: dict[str, Any]) -> str:
        """Return the best-effort page title from Notion properties."""
        properties = payload.get("properties") or {}
        for prop in properties.values():
            if prop.get("type") == "title":
                return self._rich_text_to_plain(prop.get("title", [])) or "Untitled"
        
        return "Untitled"

    async def _fetch_pages_sync_data(
        self,
        page_ids: tuple[str, ...],
        *,
        progress_callback: PageFetchProgressCallback | None,
    ) -> dict[str, NotionPageFetchResult]:
        """Prepare multiple pages concurrently while sharing the API limiter."""
        jobs:    asyncio.Queue[str] = asyncio.Queue(maxsize=NOTION_PAGE_QUEUE_SIZE)
        results: dict[str, NotionPageFetchResult] = {}
        completed_count = 0

        async def worker() -> None:
            """Fetch the metadata, Markdown, and shallow roots for queued pages."""
            nonlocal completed_count
            while True:
                page_id = await jobs.get()
                try:
                    try:
                        data = await self._fetch_page_sync_data(page_id)
                        results[page_id] = NotionPageFetchResult(page_id=page_id, data=data)
                    except Exception as exc:
                        results[page_id] = NotionPageFetchResult(page_id=page_id, error=exc)
                finally:
                    # All workers run on one event loop, so this update cannot
                    # interleave with another worker between read and write.
                    completed_count += 1
                    if progress_callback is not None:
                        try:
                            progress_callback(completed_count, len(page_ids))
                        except Exception:
                            pass

                    jobs.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(NOTION_PAGE_WORKER_COUNT)
        ]

        try:
            # Workers must already be consuming before the bounded queue is filled.
            for page_id in page_ids:
                await jobs.put(page_id)
            await jobs.join()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        return results

    async def _fetch_page_sync_data(self, page_id: str) -> NotionPageSyncData:
        """Fetch all non-recursive Notion inputs used by one sync page."""
        page_payload = await self._request_json_with_rate_limit_retry(
            "GET",
            f"/pages/{page_id}",
            None,
            limiter=self._tree_rate_limiter,
        )
        markdown_payload = await self._fetch_complete_markdown_payload(
            page_id,
            visited=frozenset(),
        )
        shallow_blocks = await self._fetch_block_children_async(
            page_id,
            limiter=self._tree_rate_limiter,
        )

        unknown_block_ids = markdown_payload.get("unknown_block_ids")
        if not isinstance(unknown_block_ids, list):
            unknown_block_ids = []
        last_edited_time = page_payload.get("last_edited_time")
        if not isinstance(last_edited_time, str) or not last_edited_time:
            last_edited_time = None

        return NotionPageSyncData(
            page_id           = page_id,
            last_edited_time  = last_edited_time,
            markdown_snapshot = NotionMarkdownSnapshot(
                page_id           = str(markdown_payload.get("id") or page_id),
                markdown          = str(markdown_payload.get("markdown") or ""),
                truncated         = bool(markdown_payload.get("truncated")),
                unknown_block_ids = tuple(str(block_id) for block_id in unknown_block_ids),
            ),
            shallow_blocks    = tuple(shallow_blocks),
        )

    async def _fetch_complete_markdown_payload(
        self,
        page_id: str,
        *,
        visited: frozenset[str],
    ) -> dict[str, Any]:
        """Resolve truncated Markdown subtrees while retaining inaccessible tags."""
        page_key = _notion_id_key(page_id)
        payload = await self._request_json_with_rate_limit_retry(
            "GET",
            f"/pages/{page_id}/markdown",
            None,
            limiter=self._tree_rate_limiter,
        )
        if not bool(payload.get("truncated")):
            return payload

        unknown_block_ids = payload.get("unknown_block_ids")
        if not isinstance(unknown_block_ids, list):
            unknown_block_ids = []

        replacements: dict[str, str] = {}
        unresolved_ids: list[str] = []
        next_visited = visited | ({page_key} if page_key else set())
        for raw_block_id in unknown_block_ids:
            block_id = str(raw_block_id)
            block_key = _notion_id_key(block_id)
            if not block_key or block_key in next_visited:
                unresolved_ids.append(block_id)
                continue
            try:
                subtree = await self._fetch_complete_markdown_payload(
                    block_id,
                    visited=next_visited,
                )
            except NotionApiError as exc:
                error_code = (
                    exc.payload.get("code")
                    if isinstance(exc.payload, Mapping)
                    else None
                )
                if exc.status == 404 and error_code == "object_not_found":
                    # Notion deliberately conceals inaccessible blocks as
                    # object_not_found, so retain their explicit placeholders.
                    unresolved_ids.append(block_id)
                    continue
                # Authentication, rate-limit, and server failures must abort
                # the snapshot instead of making incomplete Markdown look final.
                raise

            subtree_markdown = str(subtree.get("markdown") or "")
            if not subtree_markdown.strip():
                unresolved_ids.append(block_id)
                continue
            replacements[block_id] = subtree_markdown
            nested_unknown_ids = subtree.get("unknown_block_ids")
            if isinstance(nested_unknown_ids, list):
                unresolved_ids.extend(str(value) for value in nested_unknown_ids)

        resolved_payload = dict(payload)
        resolved_payload["markdown"] = _replace_unknown_markdown_tags(
            str(payload.get("markdown") or ""),
            replacements,
        )
        # Every advertised unknown block has now been attempted. Remaining tags
        # represent unsupported or inaccessible content, not a fallback signal.
        resolved_payload["truncated"] = False
        resolved_payload["unknown_block_ids"] = unresolved_ids
        return resolved_payload

    def _normalize_icon(self, icon_payload: dict[str, Any] | None) -> dict[str, Any] | None:
        """Return the icon payload if present."""
        if not icon_payload:
            return None
        if not isinstance(icon_payload, dict):
            return None
        
        return icon_payload

    async def _fetch_page_tree(self, page_id: str) -> list[NotionBlock]:
        """Fetch a complete block tree with a bounded asynchronous worker queue."""
        jobs:               asyncio.Queue[_BlockFetchJob]                  = asyncio.Queue(maxsize=NOTION_TREE_QUEUE_SIZE)
        completed:          asyncio.Queue[_BlockFetchResult]               = asyncio.Queue()
        children_by_parent: dict[tuple[str, str], tuple[NotionBlock, ...]] = {}
        errors:             list[Exception]                                = []

        await jobs.put(_BlockFetchJob(page_id=page_id, parent_block_id=page_id))

        async def worker() -> None:
            """Fetch jobs and report their newly discovered child jobs."""
            while True:
                job = await jobs.get()

                try:
                    blocks = tuple(
                        await self._fetch_block_children_async(
                            job.parent_block_id,
                            limiter=self._tree_rate_limiter,
                        )
                    )
                    # Workers store shallow results before descendants are queued.
                    children_by_parent[(job.page_id, job.parent_block_id)] = blocks
                    child_jobs = tuple(
                        _BlockFetchJob(
                            page_id=job.page_id,
                            parent_block_id=block.block_id,
                        )
                        for block in blocks
                        if block.has_children
                    )
                    await completed.put(_BlockFetchResult(job=job, child_jobs=child_jobs))
                except Exception as exc:
                    await completed.put(_BlockFetchResult(job=job, child_jobs=(), error=exc))

        async def enqueue_discovered_jobs() -> None:
            """Feed descendants into the bounded queue and settle parent jobs."""
            while True:
                result = await completed.get()

                try:
                    if result.error is not None:
                        errors.append(result.error)
                    else:
                        for child_job in result.child_jobs:
                            await jobs.put(child_job)
                finally:
                    # A job remains unfinished until all descendants it discovered
                    # have entered the queue, so queue.join() cannot return early.
                    jobs.task_done()
                    completed.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(NOTION_TREE_WORKER_COUNT)
        ]
        enqueuer = asyncio.create_task(enqueue_discovered_jobs())

        # Wait for all jobs to be processed
        try:
            await jobs.join()
            await completed.join()
        # Cancel workers and enqueuer if the caller cancels the operation
        finally:
            for task in workers:
                task.cancel()
            enqueuer.cancel()
            await asyncio.gather(*workers, enqueuer, return_exceptions=True)

        if errors:
            raise errors[0]
        return self._assemble_block_tree(
            page_id=page_id,
            parent_block_id=page_id,
            children_by_parent=children_by_parent,
        )

    async def _fetch_block_children_async(
        self,
        block_id: str,
        *,
        limiter: _AsyncRateLimiter,
    ) -> list[NotionBlock]:
        """Return every direct child using page_size=100 and shared throttling."""
        blocks:      list[NotionBlock] = []
        next_cursor: str | None        = None

        while True:
            path = (
                f"/blocks/{block_id}/children"
                f"?page_size={NOTION_BLOCK_PAGE_SIZE}"
            )
            if next_cursor:
                path = (
                    f"{path}&start_cursor={parse.quote(next_cursor)}"
                )

            response = await self._request_json_with_rate_limit_retry(
                "GET",
                path,
                None,
                limiter=limiter,
            )
            blocks.extend(
                self._normalize_block(payload)
                for payload in response.get("results", [])
            )
            if not response.get("has_more"):
                return blocks

            next_cursor = response.get("next_cursor")
            if not next_cursor:
                return blocks

    async def _request_json_with_rate_limit_retry(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        limiter: _AsyncRateLimiter,
    ) -> dict[str, Any]:
        """Make a throttled request, respecting Retry-After on HTTP 429."""
        for retry_index in range(NOTION_RATE_LIMIT_RETRIES + 1):
            await limiter.acquire()
            try:
                return await asyncio.to_thread(
                    self._request_json_once,
                    method,
                    path,
                    payload,
                )
            except NotionApiError as exc:
                if exc.status != 429 or retry_index >= NOTION_RATE_LIMIT_RETRIES:
                    raise
                await limiter.defer(self._retry_after_seconds(exc, retry_index))

        raise RuntimeError("Notion rate-limit retry loop ended unexpectedly.")

    def _retry_after_seconds(self, error: NotionApiError, retry_index: int) -> float:
        """Return Retry-After seconds, with bounded backoff for invalid headers."""
        retry_after = next(
            (
                value
                for key, value in error.headers.items()
                if key.lower() == "retry-after"
            ),
            None,
        )
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass
        return float(min(2 ** retry_index, 8))

    def _assemble_block_tree(
        self,
        *,
        page_id: str,
        parent_block_id: str,
        children_by_parent: Mapping[tuple[str, str], tuple[NotionBlock, ...]],
        ancestors: frozenset[str] = frozenset(),
    ) -> list[NotionBlock]:
        """Rebuild immutable nested blocks from the workers' shallow results."""
        blocks: list[NotionBlock] = []
        for block in children_by_parent.get((page_id, parent_block_id), ()):
            children: tuple[NotionBlock, ...] = ()
            if block.has_children and block.block_id not in ancestors:
                children = tuple(
                    self._assemble_block_tree(
                        page_id=page_id,
                        parent_block_id=block.block_id,
                        children_by_parent=children_by_parent,
                        ancestors=ancestors | {block.block_id},
                    )
                )
            blocks.append(
                NotionBlock(
                    block_id=block.block_id,
                    block_type=block.block_type,
                    has_children=block.has_children,
                    parent_id=block.parent_id,
                    parent_type=block.parent_type,
                    raw=block.raw,
                    children=children,
                )
            )
        return blocks

    def _fetch_block_children(self, block_id: str) -> Iterable[dict[str, Any]]:
        """Yield block children across pagination."""
        next_cursor: str | None = None

        while True:
            path = f"/blocks/{block_id}/children"
            if next_cursor:
                path = f"{path}?start_cursor={parse.quote(next_cursor)}"
            
            # issue request
            response = self._request_json("GET", path, None)

            # yield block children
            for result in response.get("results", []):
                yield result
            if not response.get("has_more"):
                return
            
            # get next cursor
            next_cursor = response.get("next_cursor")
            if not next_cursor:
                return

    def _normalize_block(self, payload: dict[str, Any]) -> NotionBlock:
        """Normalize a raw Notion block."""
        # extract parent information
        parent      = payload.get("parent") or {}
        parent_type = parent.get("type")
        parent_id   = parent.get(parent_type) if parent_type else None

        # build NotionBlock
        return NotionBlock(
            block_id     = str(payload.get("id")),
            block_type   = str(payload.get("type")),
            has_children = bool(payload.get("has_children")),
            parent_id    = str(parent_id) if parent_id else None,
            parent_type  = str(parent_type) if parent_type else None,
            raw          = payload,
        )

    def _update_toggle_title(self, block_id: str, title: str) -> None:
        """Update the toggle title rich text."""
        payload = {
            "toggle": {
                "rich_text": self._text_to_rich_text(title),
            }
        }
        self._request_json("PATCH", f"/blocks/{block_id}", payload)

    def _replace_block_children(self, block_id: str, body: str) -> None:
        """Replace the child blocks of a parent with new paragraph blocks."""
        existing_ids = [block["id"] for block in self._fetch_block_children(block_id)]

        # delete existing (old) children
        for child_id in existing_ids:
            self._request_json("DELETE", f"/blocks/{child_id}", None)

        # add (updated) children
        new_children = self._build_paragraph_blocks(body)
        for chunk in self._chunked(new_children, 100): # send in chunks of 100
            payload = {"children": chunk}
            self._request_json("PATCH", f"/blocks/{block_id}/children", payload)

    def _build_paragraph_blocks(self, text: str) -> list[dict[str, Any]]:
        """Convert plain text into Notion paragraph blocks."""
        if not text or not text.strip():
            return []
        
        paragraphs = [segment for segment in text.split("\n\n") if segment.strip()]
        return [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": self._text_to_rich_text(paragraph.strip()),
                },
            }
            for paragraph in paragraphs
        ]

    def _text_to_rich_text(self, text: str) -> list[dict[str, Any]]:
        """Build a Notion rich_text array from plain text."""
        if text is None:
            return []
        if text == "":
            return []
        
        return [{"type": "text", "text": {"content": text}}]

    def _rich_text_to_plain(self, rich_text: Iterable[dict[str, Any]]) -> str:
        """Flatten rich_text into a plain-text string."""
        parts: list[str] = []
        for item in rich_text:

            text = item.get("plain_text")
            if text:
                parts.append(text)
                continue

            text = item.get("text", {}).get("content")
            if text:
                parts.append(text)
        
        return "".join(parts)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Send a JSON request and return the decoded response."""
        return self._request_json_once(method=method, path=path, payload=payload)

    def _request_json_once(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Send one JSON request attempt and parse the response."""
        # build request
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Notion-Version": self._notion_version,
            "Content-Type": "application/json",
        }
        body = None if payload is None else json.dumps(payload).encode("utf-8")

        # issue request
        response = self._transport(method, url, headers, body, self._timeout_seconds)

        # parse response
        if response.status == 204:
            return {}
        
        content = response.body.decode("utf-8") if response.body else ""
        data = json.loads(content) if content else {}

        # handle error responses
        if response.status >= 400:
            message = data.get("message") or f"Notion API error ({response.status})"
            raise NotionApiError(
                message,
                status  = response.status,
                payload = data,
                headers = response.headers,
            )
        
        return data

    def _default_transport(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> NotionResponse:
        """Issue a request using urllib and return a NotionResponse."""
        req = request.Request(url, data=body, headers=headers, method=method)

        try:
            # perform the HTTP request
            with request.urlopen(req, timeout=timeout) as response:
                raw_body = response.read()
                return NotionResponse(
                    status=response.getcode(),
                    headers=dict(response.headers.items()),
                    body=raw_body,
                )

        # urllib represents HTTP error responses as exceptions. Preserve the
        # response so API errors such as 429 can use status and Retry-After.
        except error.HTTPError as exc:
            return NotionResponse(
                status=exc.code,
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=exc.read(),
            )

        # transport failed
        except Exception as exc:
            raise NotionTransportError(str(exc)) from exc

    def _chunked(self, items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
        """Yield lists of at most size entries."""
        for index in range(0, len(items), size):
            yield items[index : index + size]
