"""Tests for protected shell endpoint behavior and API conventions."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.main import create_app


class ShellApiTests(unittest.TestCase):
    """Ensure shell endpoints are protected and return stable placeholders."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)

        db_path = Path(self._temp_dir.name) / "api_shells.db"
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
        )
        app = create_app(settings)
        self._client_ctx = TestClient(app)
        self.client = self._client_ctx.__enter__()
        self.addCleanup(lambda: self._client_ctx.__exit__(None, None, None))

        self.client.post(
            "/v1/auth/register",
            json={"email": "shell@example.com", "password": "shell-pass-1"},
        )
        token_response = self.client.post(
            "/v1/auth/token",
            json={"email": "shell@example.com", "password": "shell-pass-1"},
        )
        self.access_token = token_response.json()["access_token"]

    def _auth_headers(self) -> dict[str, str]:
        """Return bearer auth headers used by protected endpoint calls."""
        return {"Authorization": f"Bearer {self.access_token}"}

    def test_shell_endpoints_require_auth(self) -> None:
        response = self.client.post(
            "/v1/static/generate-question-variants",
            json={
                "question": "Q",
                "answer": "A",
                "number_variations": 1,
                "language": "en",
                "style": "exam",
                "difficulty": "medium",
                "constraints": {"no_trick_questions": True, "keep_length_similar": True},
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")

    def test_shell_endpoints_return_not_implemented_contract(self) -> None:
        variants_response = self.client.post(
            "/v1/static/generate-question-variants",
            headers=self._auth_headers(),
            json={
                "question": "What is a group?",
                "answer": "A set with an associative operation and identity/inverses.",
                "number_variations": 2,
                "language": "en",
                "style": "exam",
                "difficulty": "medium",
                "constraints": {"no_trick_questions": True, "keep_length_similar": True},
            },
        )
        self.assertEqual(variants_response.status_code, 501)
        self.assertEqual(variants_response.json()["error"]["code"], "NOT_IMPLEMENTED")
        self.assertIn("X-Request-Id", variants_response.headers)

        tts_response = self.client.post(
            "/v1/static/text-to-speech",
            headers=self._auth_headers(),
            json={"text": "Define a group.", "voice": "alloy", "format": "mp3", "speed": 1.0},
        )
        self.assertEqual(tts_response.status_code, 501)
        self.assertEqual(tts_response.json()["error"]["code"], "NOT_IMPLEMENTED")

        eval_response = self.client.post(
            "/v1/active/evaluate-answer",
            headers=self._auth_headers(),
            json={
                "question": "What is a group?",
                "expected_answer": "A set with operation, identity, inverses, and associativity.",
                "user_answer": "A set with operation and identity.",
                "grading": {"strictness": "medium", "allow_paraphrase": True},
                "output_format": "short",
            },
        )
        self.assertEqual(eval_response.status_code, 501)
        self.assertEqual(eval_response.json()["error"]["code"], "NOT_IMPLEMENTED")

    def test_shell_endpoints_validate_request_payloads(self) -> None:
        response = self.client.post(
            "/v1/static/text-to-speech",
            headers=self._auth_headers(),
            json={"text": "", "voice": "alloy", "format": "mp3", "speed": 1.0},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")

    def test_openapi_contains_all_v1_routes(self) -> None:
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]

        self.assertIn("/v1/auth/register", paths)
        self.assertIn("/v1/auth/token", paths)
        self.assertIn("/v1/auth/refresh", paths)
        self.assertIn("/v1/auth/me", paths)
        self.assertIn("/v1/static/generate-question-variants", paths)
        self.assertIn("/v1/static/text-to-speech", paths)
        self.assertIn("/v1/active/evaluate-answer", paths)
