"""Tests for Notion client auth behavior and request retry logic."""

from __future__ import annotations

from pathlib import Path
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
