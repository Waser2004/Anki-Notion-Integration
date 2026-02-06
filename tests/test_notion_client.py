"""Tests for Notion client auth behavior and request retry logic."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from anki_notion_integration.notion_client import NotionClient, NotionResponse


class NotionClientAuthTests(unittest.TestCase):
    """Verify OAuth retry behavior for Notion API requests."""

    def test_request_retries_once_after_401_with_refreshed_token(self) -> None:
        auth_headers: list[str] = []

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> NotionResponse:
            _ = (method, url, body, timeout)
            auth_headers.append(headers.get("Authorization", ""))
            if len(auth_headers) == 1:
                return NotionResponse(status=401, headers={}, body=b'{"message":"unauthorized"}')
            return NotionResponse(status=200, headers={}, body=b'{"results":[],"has_more":false}')

        refresh_calls = {"count": 0}

        def token_refresh() -> str:
            refresh_calls["count"] += 1
            return "new-access-token"

        client = NotionClient(api_token="old-access-token", transport=transport, token_refresh=token_refresh)
        pages = client.list_pages()

        self.assertEqual(pages, [])
        self.assertEqual(refresh_calls["count"], 1)
        self.assertEqual(len(auth_headers), 2)
        self.assertEqual(auth_headers[0], "Bearer old-access-token")
        self.assertEqual(auth_headers[1], "Bearer new-access-token")


class NotionClientPageParentResolutionTests(unittest.TestCase):
    """Verify page parent normalization for block-nested child pages."""

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
