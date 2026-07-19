"""Tests for Notion client page parent resolution behavior."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.notion_client import NotionClient, NotionResponse


class NotionClientPageParentResolutionTests(unittest.TestCase):
    """Verify page parent normalization for block-nested child pages."""

    def test_default_api_version_header_is_current(self) -> None:
        headers_seen: list[dict[str, str]] = []

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, body, timeout)
            headers_seen.append(headers)
            return _json_response({"results": [], "has_more": False})

        NotionClient(api_token="token", transport=transport).list_pages()

        self.assertEqual(headers_seen[0]["Notion-Version"], "2026-03-11")

    def test_list_pages_resolves_block_parent_to_containing_page(self) -> None:
        calls: list[str] = []

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            calls.append(url)
            if url.endswith("/search"):
                return _json_response(
                    {
                        "results": [
                            _page_payload("root-page", "Root"),
                            _page_payload("nested-page", "Nested", parent_type="block_id", parent_id="callout-block"),
                        ],
                        "has_more": False,
                    }
                )
            if url.endswith("/blocks/callout-block"):
                return _json_response(
                    {
                        "id": "callout-block",
                        "parent": {
                            "type": "page_id",
                            "page_id": "root-page",
                        },
                    }
                )
            self.fail(f"Unexpected URL: {url}")

        client = NotionClient(api_token="token", transport=transport)
        pages = {page.page_id: page for page in client.list_pages()}

        self.assertEqual(pages["nested-page"].parent_type, "page_id")
        self.assertEqual(pages["nested-page"].parent_id, "root-page")
        self.assertIn("https://api.notion.com/v1/blocks/callout-block", calls)

    def test_list_pages_uses_cached_resolution_for_shared_block_parent(self) -> None:
        block_request_count = {"count": 0}

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            if url.endswith("/search"):
                return _json_response(
                    {
                        "results": [
                            _page_payload("root-page", "Root"),
                            _page_payload("nested-page-1", "Nested 1", parent_type="block_id", parent_id="shared-block"),
                            _page_payload("nested-page-2", "Nested 2", parent_type="block_id", parent_id="shared-block"),
                        ],
                        "has_more": False,
                    }
                )
            if url.endswith("/blocks/shared-block"):
                block_request_count["count"] += 1
                return _json_response(
                    {
                        "id": "shared-block",
                        "parent": {
                            "type": "page_id",
                            "page_id": "root-page",
                        },
                    }
                )
            self.fail(f"Unexpected URL: {url}")

        client = NotionClient(api_token="token", transport=transport)
        pages = {page.page_id: page for page in client.list_pages()}

        self.assertEqual(pages["nested-page-1"].parent_id, "root-page")
        self.assertEqual(pages["nested-page-2"].parent_id, "root-page")
        self.assertEqual(block_request_count["count"], 1)

    def test_list_pages_excludes_database_rows_with_legacy_or_data_source_parent(self) -> None:
        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            if url.endswith("/search"):
                return _json_response(
                    {
                        "results": [
                            _page_payload("regular-page", "Regular page"),
                            _page_payload("legacy-row", "Legacy row", parent_type="database_id", parent_id="database"),
                            _page_payload("data-source-row", "Data source row", parent_type="data_source_id", parent_id="data-source"),
                        ],
                        "has_more": False,
                    }
                )
            self.fail(f"Unexpected URL: {url}")

        client = NotionClient(api_token="token", transport=transport)

        self.assertEqual([page.page_id for page in client.list_pages()], ["regular-page"])
        self.assertEqual(
            [page.page_id for page in client.list_pages(include_database_pages=True)],
            ["regular-page", "legacy-row", "data-source-row"],
        )

    def test_list_pages_excludes_page_nested_inside_database_row(self) -> None:
        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            if url.endswith("/search"):
                return _json_response(
                    {
                        "results": [
                            _page_payload("database-row", "Database row", parent_type="data_source_id", parent_id="data-source"),
                            _page_payload("nested-page", "Nested page", parent_type="block_id", parent_id="row-block"),
                            _page_payload("regular-page", "Regular page"),
                        ],
                        "has_more": False,
                    }
                )
            if url.endswith("/blocks/row-block"):
                return _json_response(
                    {
                        "id": "row-block",
                        "parent": {
                            "type": "page_id",
                            "page_id": "database-row",
                        },
                    }
                )
            self.fail(f"Unexpected URL: {url}")

        client = NotionClient(api_token="token", transport=transport)

        self.assertEqual([page.page_id for page in client.list_pages()], ["regular-page"])


class NotionClientChildPageOrderTests(unittest.TestCase):
    """Verify child-page sibling order extraction from parent block order."""

    def test_build_child_page_order_map_matches_parent_block_order(self) -> None:
        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            if url.endswith("/search"):
                return _json_response(
                    {
                        "results": [
                            _page_payload("parent", "Parent"),
                            _page_payload("3", "3", parent_type="page_id", parent_id="parent"),
                            _page_payload("1", "1", parent_type="page_id", parent_id="parent"),
                            _page_payload("5", "5", parent_type="page_id", parent_id="parent"),
                            _page_payload("2", "2", parent_type="page_id", parent_id="parent"),
                            _page_payload("4", "4", parent_type="page_id", parent_id="parent"),
                        ],
                        "has_more": False,
                    }
                )
            if url.endswith("/blocks/parent/children"):
                return _json_response(
                    {
                        "results": [
                            _block_payload("1", "child_page"),
                            _block_payload("2", "child_page"),
                            _block_payload("paragraph-1", "paragraph"),
                            _block_payload("3", "child_page"),
                            _block_payload("4", "child_page"),
                            _block_payload("5", "child_page"),
                        ],
                        "has_more": False,
                    }
                )
            self.fail(f"Unexpected URL: {url}")

        client = NotionClient(api_token="token", transport=transport)
        pages = client.list_pages()
        order_map = client.build_child_page_order_map({page.page_id: page for page in pages})

        self.assertEqual(order_map.get("parent"), ("1", "2", "3", "4", "5"))

    def test_build_child_page_order_map_appends_missing_known_children(self) -> None:
        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            if url.endswith("/search"):
                return _json_response(
                    {
                        "results": [
                            _page_payload("parent", "Parent"),
                            _page_payload("child-a", "A", parent_type="page_id", parent_id="parent"),
                            _page_payload("child-b", "B", parent_type="page_id", parent_id="parent"),
                            _page_payload("child-c", "C", parent_type="page_id", parent_id="parent"),
                        ],
                        "has_more": False,
                    }
                )
            if url.endswith("/blocks/parent/children"):
                return _json_response(
                    {
                        "results": [
                            _block_payload("child-b", "child_page"),
                            _block_payload("child-a", "child_page"),
                            # `child-c` not listed as child_page block -> should be appended.
                        ],
                        "has_more": False,
                    }
                )
            self.fail(f"Unexpected URL: {url}")

        client = NotionClient(api_token="token", transport=transport)
        pages = client.list_pages()
        order_map = client.build_child_page_order_map({page.page_id: page for page in pages})

        self.assertEqual(order_map.get("parent"), ("child-b", "child-a", "child-c"))


class NotionClientMarkdownTests(unittest.TestCase):
    """Verify enhanced Markdown retrieval uses the page Markdown endpoint."""

    def test_get_page_markdown_returns_markdown_payload(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (body, timeout)
            calls.append((url, headers))
            return _json_response({"object": "page_markdown", "id": "page-1", "markdown": "<table/>"})

        client = NotionClient(api_token="token", transport=transport)

        self.assertEqual(client.get_page_markdown("page-1"), "<table/>")
        self.assertEqual(calls[0][0], "https://api.notion.com/v1/pages/page-1/markdown")
        self.assertEqual(calls[0][1]["Notion-Version"], "2026-03-11")


def _json_response(payload: dict[str, object]) -> NotionResponse:
    """Build a success JSON response payload for transport test doubles."""
    return NotionResponse(status=200, headers={}, body=json.dumps(payload).encode("utf-8"))


def _page_payload(
    page_id: str,
    title: str,
    *,
    parent_type: str = "workspace",
    parent_id: str | None = None,
) -> dict[str, object]:
    """Build one page payload with title and parent data."""
    parent: dict[str, object] = {"type": parent_type}
    if parent_id:
        parent[parent_type] = parent_id

    return {
        "object": "page",
        "id": page_id,
        "parent": parent,
        "properties": {
            "title": {
                "id": "title",
                "type": "title",
                "title": [{"type": "text", "plain_text": title, "text": {"content": title}}],
            }
        },
    }


def _block_payload(block_id: str, block_type: str) -> dict[str, object]:
    """Build one minimal block payload for `/blocks/.../children` responses."""
    return {
        "object": "block",
        "id": block_id,
        "type": block_type,
        "has_children": False,
    }
