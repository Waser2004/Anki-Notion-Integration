"""Tests for Notion client page parent resolution behavior."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules import notion_client as notion_client_module
from Noteck.modules.notion_client import NotionApiError, NotionClient, NotionResponse


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


class NotionClientPageTreeQueueTests(unittest.TestCase):
    """Verify complete trees use bounded concurrent workers and robust retries."""

    def test_get_pages_sync_data_uses_bounded_page_workers(self) -> None:
        active_pages: set[str] = set()
        pages_with_overlap: set[str] = set()
        progress_updates: list[tuple[int, int]] = []
        lock = threading.Lock()

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            page_id = "page-1" if "page-1" in url else "page-2"
            with lock:
                active_pages.add(page_id)
                if len(active_pages) > 1:
                    pages_with_overlap.update(active_pages)
            try:
                time.sleep(0.02)
                if url.endswith(f"/pages/{page_id}"):
                    return _json_response({"id": page_id, "last_edited_time": f"{page_id}-edited"})
                if url.endswith(f"/pages/{page_id}/markdown"):
                    return _json_response(
                        {
                            "id": page_id,
                            "markdown": f"# {page_id}",
                            "truncated": False,
                            "unknown_block_ids": [],
                        }
                    )
                if f"/blocks/{page_id}/children?page_size=100" in url:
                    return _json_response({"results": [], "has_more": False})
                self.fail(f"Unexpected URL: {url}")
            finally:
                with lock:
                    active_pages.discard(page_id)

        with patch.object(notion_client_module, "NOTION_REQUESTS_PER_SECOND", 1_000_000.0):
            client = NotionClient(api_token="token", transport=transport)
            results = client.get_pages_sync_data(
                ["page-1", "page-2"],
                progress_callback=lambda completed, total: progress_updates.append(
                    (completed, total)
                ),
            )

        self.assertEqual(set(results), {"page-1", "page-2"})
        self.assertEqual(pages_with_overlap, {"page-1", "page-2"})
        self.assertEqual(progress_updates, [(1, 2), (2, 2)])
        for page_id, result in results.items():
            self.assertIsNone(result.error)
            self.assertIsNotNone(result.data)
            assert result.data is not None
            self.assertEqual(result.data.page_id, page_id)
            self.assertEqual(result.data.last_edited_time, f"{page_id}-edited")
            self.assertEqual(result.data.markdown_snapshot.markdown, f"# {page_id}")
            self.assertEqual(result.data.shallow_blocks, ())

    def test_get_page_markdown_returns_complete_snapshot(self) -> None:
        calls: list[str] = []

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, headers, body, timeout)
            calls.append(url)
            return _json_response(
                {
                    "object": "page_markdown",
                    "id": "page-1",
                    "markdown": "<details>\n<summary>Question</summary>\n\tAnswer\n</details>",
                    "truncated": False,
                    "unknown_block_ids": ["unknown-1"],
                }
            )

        with patch.object(notion_client_module, "NOTION_REQUESTS_PER_SECOND", 1_000_000.0):
            snapshot = NotionClient(api_token="token", transport=transport).get_page_markdown("page-1")

        self.assertEqual(snapshot.page_id, "page-1")
        self.assertIn("<summary>Question</summary>", snapshot.markdown)
        self.assertFalse(snapshot.truncated)
        self.assertEqual(snapshot.unknown_block_ids, ("unknown-1",))
        self.assertEqual(calls, ["https://api.notion.com/v1/pages/page-1/markdown"])

    def test_get_page_content_fetches_paginated_descendants_with_bounded_workers(self) -> None:
        calls: list[str] = []
        active_requests = 0
        maximum_active_requests = 0
        lock = threading.Lock()

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            nonlocal active_requests, maximum_active_requests
            _ = (method, headers, body, timeout)
            calls.append(url)
            with lock:
                active_requests += 1
                maximum_active_requests = max(maximum_active_requests, active_requests)
            try:
                if "/blocks/page-1/children?" in url and "start_cursor=" not in url:
                    return _json_response(
                        {
                            "results": [
                                _block_payload(f"parent-{index}", "toggle", has_children=True)
                                for index in range(6)
                            ],
                            "has_more": True,
                            "next_cursor": "next page",
                        }
                    )
                if "/blocks/page-1/children?" in url:
                    return _json_response(
                        {
                            "results": [_block_payload("root-leaf", "paragraph")],
                            "has_more": False,
                        }
                    )
                if "/blocks/parent-" in url:
                    # Blocking transports execute in worker threads, allowing the
                    # async pool's concurrency bound to be observed.
                    time.sleep(0.03)
                    parent_id = url.split("/blocks/", 1)[1].split("/", 1)[0]
                    return _json_response(
                        {
                            "results": [_block_payload(f"{parent_id}-leaf", "paragraph")],
                            "has_more": False,
                        }
                    )
                self.fail(f"Unexpected URL: {url}")
            finally:
                with lock:
                    active_requests -= 1

        with patch.object(notion_client_module, "NOTION_REQUESTS_PER_SECOND", 1_000_000.0):
            client = NotionClient(api_token="token", transport=transport)
            blocks = client.get_page_content("page-1")

        self.assertEqual(
            [block.block_id for block in blocks],
            [*(f"parent-{index}" for index in range(6)), "root-leaf"],
        )
        self.assertEqual(blocks[0].children[0].block_id, "parent-0-leaf")
        self.assertGreaterEqual(maximum_active_requests, 2)
        self.assertLessEqual(maximum_active_requests, notion_client_module.NOTION_TREE_WORKER_COUNT)
        self.assertTrue(all("page_size=100" in url for url in calls))
        self.assertTrue(any("start_cursor=next%20page" in url for url in calls))

    def test_get_page_content_retries_429_after_retry_after(self) -> None:
        attempts = 0

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            nonlocal attempts
            _ = (method, url, headers, body, timeout)
            attempts += 1
            if attempts == 1:
                return NotionResponse(
                    status=429,
                    headers={"Retry-After": "0"},
                    body=json.dumps({"message": "slow down"}).encode("utf-8"),
                )
            return _json_response({"results": [], "has_more": False})

        with patch.object(notion_client_module, "NOTION_REQUESTS_PER_SECOND", 1_000_000.0):
            client = NotionClient(api_token="token", transport=transport)
            self.assertEqual(client.get_page_content("page-1"), [])

        self.assertEqual(attempts, 2)

    def test_get_page_content_raises_after_rate_limit_retries_are_exhausted(self) -> None:
        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, url, headers, body, timeout)
            return NotionResponse(
                status=429,
                headers={"Retry-After": "0"},
                body=json.dumps({"message": "slow down"}).encode("utf-8"),
            )

        with patch.object(notion_client_module, "NOTION_REQUESTS_PER_SECOND", 1_000_000.0), patch.object(
            notion_client_module,
            "NOTION_RATE_LIMIT_RETRIES",
            1,
        ):
            client = NotionClient(api_token="token", transport=transport)
            with self.assertRaises(NotionApiError) as raised:
                client.get_page_content("page-1")

        self.assertEqual(raised.exception.status, 429)

    def test_default_transport_preserves_http_error_response_metadata(self) -> None:
        client = NotionClient(api_token="token")
        http_error = notion_client_module.error.HTTPError(
            url="https://api.notion.com/v1/blocks/page-1/children",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "2"},
            fp=BytesIO(json.dumps({"message": "slow down"}).encode("utf-8")),
        )

        with patch.object(notion_client_module.request, "urlopen", side_effect=http_error):
            response = client._default_transport(
                "GET",
                "https://api.notion.com/v1/blocks/page-1/children",
                {},
                None,
                15.0,
            )

        self.assertEqual(response.status, 429)
        self.assertEqual(response.headers["Retry-After"], "2")
        self.assertIn(b"slow down", response.body)


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


def _block_payload(
    block_id: str,
    block_type: str,
    *,
    has_children: bool = False,
) -> dict[str, object]:
    """Build one minimal block payload for `/blocks/.../children` responses."""
    return {
        "object": "block",
        "id": block_id,
        "type": block_type,
        "has_children": has_children,
    }
