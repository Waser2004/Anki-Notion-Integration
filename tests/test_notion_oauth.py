"""Tests for Notion public OAuth helpers."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib import request
from urllib.error import URLError

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import keyring
from keyring.backend import KeyringBackend

from anki_notion_integration.db import Database
from anki_notion_integration.notion_oauth import (
    NotionOAuthSessionStore,
    OAuthResponse,
    build_authorization_url,
    disconnect_notion,
    ensure_valid_access_token,
    exchange_authorization_code,
    refresh_access_token,
    wait_for_authorization_code,
)
from anki_notion_integration import notion_oauth_config


class _InMemoryKeyring(KeyringBackend):
    """Simple in-memory keyring backend for deterministic OAuth tests."""

    priority = 1

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


class NotionOAuthTests(unittest.TestCase):
    """Validate OAuth URL creation, callbacks, token flows, and logout handling."""

    def setUp(self) -> None:
        self._keyring = _InMemoryKeyring()
        keyring.set_keyring(self._keyring)
        # Use deterministic test credentials so OAuth URL/request helpers can run.
        notion_oauth_config.NOTION_OAUTH_CLIENT_ID = "test-client-id"
        notion_oauth_config.NOTION_OAUTH_CLIENT_SECRET = "test-client-secret"
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db_path = Path(self._temp_dir.name) / "oauth.db"
        self._db = Database(self._db_path)
        self._db.initialize()

    def test_build_authorization_url_contains_required_params(self) -> None:
        url = build_authorization_url(state="state-123", redirect_uri="http://127.0.0.1:8765/notion/oauth/callback")
        self.assertIn("/oauth/authorize?", url)
        self.assertIn("response_type=code", url)
        self.assertIn("owner=user", url)
        self.assertIn("state=state-123", url)

    def test_exchange_authorization_code_uses_basic_auth(self) -> None:
        captured: dict[str, object] = {}

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> OAuthResponse:
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = body
            captured["timeout"] = timeout
            return OAuthResponse(
                status=200,
                headers={},
                body=b'{"access_token":"a-1","refresh_token":"r-1","expires_in":3600}',
            )

        payload = exchange_authorization_code(
            code="code-1",
            redirect_uri="http://127.0.0.1:8765/notion/oauth/callback",
            transport=transport,
            timeout_seconds=9.0,
        )
        self.assertEqual(payload["access_token"], "a-1")
        self.assertEqual(captured["method"], "POST")
        self.assertIn("/oauth/token", str(captured["url"]))
        self.assertIn("Basic ", str(captured["headers"]))
        self.assertIn(b'"grant_type": "authorization_code"', bytes(captured["body"]))

    def test_refresh_access_token_updates_session_tokens(self) -> None:
        session = NotionOAuthSessionStore(self._db, profile_name="p1")
        session.save_token_payload({"access_token": "old-a", "refresh_token": "old-r", "expires_in": 60})

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> OAuthResponse:
            _ = (method, url, headers, body, timeout)
            return OAuthResponse(
                status=200,
                headers={},
                body=b'{"access_token":"new-a","refresh_token":"new-r","expires_in":3600}',
            )

        access_token = refresh_access_token(session=session, transport=transport)
        self.assertEqual(access_token, "new-a")
        self.assertEqual(session.get_access_token(), "new-a")
        self.assertEqual(session.get_refresh_token(), "new-r")

    def test_disconnect_revokes_tokens_and_clears_local_state(self) -> None:
        calls: list[bytes] = []
        session = NotionOAuthSessionStore(self._db, profile_name="p1")
        session.save_token_payload({"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 60})

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> OAuthResponse:
            _ = (method, url, headers, timeout)
            calls.append(body or b"")
            return OAuthResponse(status=200, headers={}, body=b"{}")

        disconnect_notion(session=session, transport=transport)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(session.get_access_token())
        self.assertIsNone(session.get_refresh_token())

    def test_wait_for_authorization_code_receives_callback(self) -> None:
        def send_callback() -> None:
            url = "http://127.0.0.1:8865/notion/oauth/callback?code=code-xyz&state=state-xyz"
            for _ in range(20):
                try:
                    with request.urlopen(url, timeout=0.2):
                        return
                except URLError:
                    time.sleep(0.05)
                    continue

        worker = threading.Thread(target=send_callback, daemon=True)
        worker.start()

        code = wait_for_authorization_code(
            host="127.0.0.1",
            port=8865,
            callback_path="/notion/oauth/callback",
            expected_state="state-xyz",
            timeout_seconds=3.0,
        )
        self.assertEqual(code, "code-xyz")

    def test_ensure_valid_access_token_returns_stored_token(self) -> None:
        session = NotionOAuthSessionStore(self._db, profile_name="p1")
        session.save_token_payload({"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600})
        token = ensure_valid_access_token(session=session)
        self.assertEqual(token, "access-1")
