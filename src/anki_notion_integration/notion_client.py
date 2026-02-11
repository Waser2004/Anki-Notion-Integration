"""Notion API client helpers for the Anki-Notion integration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterable, Mapping
from urllib import request, parse

from .db import Database
from .settings import SettingsStore


class NotionApiError(RuntimeError):
    """Raised when the Notion API returns an error response."""

    def __init__(self, message: str, status: int | None = None, payload: Any | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


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


Transport = Callable[[str, str, dict[str, str], bytes | None, float], NotionResponse]


class NotionClient:
    """Small Notion REST API wrapper focused on pages and toggle blocks."""

    def __init__(
        self,
        api_token: str,
        notion_version: str = "2022-06-28",
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

    @classmethod
    def from_settings(
        cls,
        db: Database,
        profile_name: str | None = None,
        notion_version: str = "2022-06-28",
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
            if not include_database_pages and page.parent_type == "database_id":
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
        return self._fetch_block_children_recursive(page_id)

    def get_page_last_edited_time(self, page_id: str) -> str | None:
        """Return the page `last_edited_time` used for fast-sync decisions."""
        payload = self._request_json("GET", f"/pages/{page_id}", None)
        last_edited_time = payload.get("last_edited_time")
        if isinstance(last_edited_time, str) and last_edited_time:
            return last_edited_time
        return None

    def get_page_blocks_shallow(self, page_id: str) -> list[NotionBlock]:
        """Return the page's direct child blocks without expanding nested children."""
        blocks: list[NotionBlock] = []
        for payload in self._fetch_block_children(page_id):
            blocks.append(self._normalize_block(payload))
        return blocks

    def get_block(self, block_id: str) -> NotionBlock:
        """Return a single Notion block without expanding its children."""
        payload = self._request_json("GET", f"/blocks/{block_id}", None)
        return self._normalize_block(payload)

    def get_block_children_recursive(self, block_id: str) -> list[NotionBlock]:
        """Return the full block tree under the given block id."""
        return self._fetch_block_children_recursive(block_id)

    def update_toggle(self, block_id: str, title: str, body: str) -> None:
        """Update a toggle block title and replace its child blocks."""
        self._update_toggle_title(block_id, title)
        self._replace_block_children(block_id, body)

    def build_child_page_order_map(
        self,
        pages_by_id: Mapping[str, NotionPage],
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

    def _extract_page_title(self, payload: dict[str, Any]) -> str:
        """Return the best-effort page title from Notion properties."""
        properties = payload.get("properties") or {}
        for prop in properties.values():
            if prop.get("type") == "title":
                return self._rich_text_to_plain(prop.get("title", [])) or "Untitled"
        
        return "Untitled"

    def _normalize_icon(self, icon_payload: dict[str, Any] | None) -> dict[str, Any] | None:
        """Return the icon payload if present."""
        if not icon_payload:
            return None
        if not isinstance(icon_payload, dict):
            return None
        
        return icon_payload

    def _fetch_block_children_recursive(self, block_id: str) -> list[NotionBlock]:
        """Fetch blocks recursively starting from a parent block."""
        blocks: list[NotionBlock] = []

        # fetch child blocks of given parent block
        for payload in self._fetch_block_children(block_id):
            block = self._normalize_block(payload)

            # add children to NotionBlock if present
            if block.has_children:
                children = self._fetch_block_children_recursive(block.block_id)
                block = NotionBlock(
                    block_id     = block.block_id,
                    block_type   = block.block_type,
                    has_children = block.has_children,
                    parent_id    = block.parent_id,
                    parent_type  = block.parent_type,
                    raw          = block.raw,
                    children     = tuple(children),
                )

            blocks.append(block)
        
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
            raise NotionApiError(message, status=response.status, payload=data)
        
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
        
        # transport failed
        except Exception as exc:
            raise NotionTransportError(str(exc)) from exc

    def _chunked(self, items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
        """Yield lists of at most size entries."""
        for index in range(0, len(items), size):
            yield items[index : index + size]
