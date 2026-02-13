"""Integration-style tests for auth endpoint behavior."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.main import create_app


class AuthApiTests(unittest.TestCase):
    """Validate the full auth lifecycle for the dev milestone."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)

        db_path = Path(self._temp_dir.name) / "api_auth.db"
        settings = Settings(
            app_name="Noteck AI API Test",
            environment="test",
            database_url=f"sqlite:///{db_path}",
            jwt_secret="test-secret",
            access_token_ttl_seconds=3600,
            refresh_token_ttl_seconds=7200,
            enable_startup_admin_seed=False,
            startup_admin_email="",
            startup_admin_password="",
            openai_api_key="test-key",
            openai_model="gpt-5-mini-2025-08-07",
            openai_timeout_seconds=20.0,
            openai_base_url="https://api.openai.com/v1",
        )
        app = create_app(settings)
        self._client_ctx = TestClient(app)
        self.client = self._client_ctx.__enter__()
        self.addCleanup(lambda: self._client_ctx.__exit__(None, None, None))

    def test_register_login_me_and_refresh_flow(self) -> None:
        register_response = self.client.post(
            "/v1/auth/register",
            json={"email": "user@example.com", "password": "strong-pass"},
        )
        self.assertEqual(register_response.status_code, 200)
        self.assertTrue(register_response.json()["user_id"].startswith("usr_"))
        self.assertIn("X-Request-Id", register_response.headers)

        token_response = self.client.post(
            "/v1/auth/token",
            json={"email": "user@example.com", "password": "strong-pass"},
        )
        self.assertEqual(token_response.status_code, 200)
        token_payload = token_response.json()
        self.assertEqual(token_payload["token_type"], "bearer")
        self.assertGreater(len(token_payload["access_token"]), 20)
        self.assertGreater(len(token_payload["refresh_token"]), 20)

        me_response = self.client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
        )
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["email"], "user@example.com")

        refresh_response = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": token_payload["refresh_token"]},
        )
        self.assertEqual(refresh_response.status_code, 200)
        refreshed = refresh_response.json()
        self.assertNotEqual(refreshed["refresh_token"], token_payload["refresh_token"])

        reused_refresh = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": token_payload["refresh_token"]},
        )
        self.assertEqual(reused_refresh.status_code, 401)
        self.assertEqual(reused_refresh.json()["error"]["code"], "UNAUTHORIZED")

    def test_auth_failure_paths(self) -> None:
        self.client.post(
            "/v1/auth/register",
            json={"email": "dup@example.com", "password": "password-123"},
        )

        duplicate_response = self.client.post(
            "/v1/auth/register",
            json={"email": "dup@example.com", "password": "password-123"},
        )
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.json()["error"]["code"], "EMAIL_ALREADY_EXISTS")

        invalid_login = self.client.post(
            "/v1/auth/token",
            json={"email": "dup@example.com", "password": "wrong-pass"},
        )
        self.assertEqual(invalid_login.status_code, 401)
        self.assertEqual(invalid_login.json()["error"]["code"], "INVALID_CREDENTIALS")

        invalid_refresh = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": "invalid-token"},
        )
        self.assertEqual(invalid_refresh.status_code, 401)
        self.assertEqual(invalid_refresh.json()["error"]["code"], "UNAUTHORIZED")

    def test_auth_me_requires_bearer_token(self) -> None:
        response = self.client.get("/v1/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")
