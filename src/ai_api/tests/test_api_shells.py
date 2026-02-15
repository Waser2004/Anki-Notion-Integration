"""Tests for protected shell endpoint behavior and API conventions."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from app.services.text_to_speech import TextToSpeechResult


class ShellApiTests(unittest.TestCase):
    """Ensure protected endpoint behavior and stable API contracts."""

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
            openai_api_key="test-key",
            openai_model="gpt-5-mini-2025-08-07",
            openai_tts_model="gpt-4o-mini-tts",
            openai_timeout_seconds=20.0,
            openai_base_url="https://api.openai.com/v1",
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

    def _variants_payload(self) -> dict[str, object]:
        """Return a valid request payload for variant generation tests."""
        return {
            "question": "What is a group?",
            "answer": "A set with an associative operation and identity/inverses.",
            "number_variations": 2,
            "language": "en",
            "style": "exam",
            "difficulty": "medium",
            "constraints": {"no_trick_questions": True, "keep_length_similar": True},
        }

    def _cloze_variants_payload(self) -> dict[str, object]:
        """Return a valid request payload for cloze variant generation tests."""
        return {
            "cloze_text": "Paris is the capital of {{c1::France}}.",
            "number_variations": 2,
            "language": "en",
            "style": "exam",
            "difficulty": "medium",
            "constraints": {"no_trick_questions": True, "keep_length_similar": True},
        }

    def test_shell_endpoints_require_auth(self) -> None:
        response = self.client.post(
            "/v1/static/generate-question-variants",
            json=self._variants_payload(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")

        cloze_response = self.client.post(
            "/v1/static/generate-cloze-variants",
            json=self._cloze_variants_payload(),
        )
        self.assertEqual(cloze_response.status_code, 401)
        self.assertEqual(cloze_response.json()["error"]["code"], "UNAUTHORIZED")

        tts_response = self.client.post(
            "/v1/static/text-to-speech",
            json={"text": "Define a group.", "voice": "alloy", "format": "mp3", "speed": 1.0},
        )
        self.assertEqual(tts_response.status_code, 401)
        self.assertEqual(tts_response.json()["error"]["code"], "UNAUTHORIZED")

        eval_response = self.client.post(
            "/v1/active/evaluate-answer",
            json={
                "question": "What is a group?",
                "expected_answer": "A set with operation, identity, inverses, and associativity.",
                "user_answer": "A set with operation and identity.",
                "grading": {"strictness": "medium", "allow_paraphrase": True},
                "output_format": "short",
            },
        )
        self.assertEqual(eval_response.status_code, 401)
        self.assertEqual(eval_response.json()["error"]["code"], "UNAUTHORIZED")

    def test_generate_question_variants_success_contract(self) -> None:
        mocked_provider_response = {
            "items": [
                {"question": "Define a group in algebra."},
                {"question": "State the axioms of a group."},
            ],
            "usage": {"input_tokens": 123, "output_tokens": 456},
            "model": "gpt-5-mini-2025-08-07",
        }
        with patch(
            "app.services.question_variants._call_openai_responses_parse",
            return_value=mocked_provider_response,
        ):
            variants_response = self.client.post(
                "/v1/static/generate-question-variants",
                headers=self._auth_headers(),
                json=self._variants_payload(),
            )

        self.assertEqual(variants_response.status_code, 200)
        payload = variants_response.json()
        self.assertEqual(len(payload["items"]), 2)
        self.assertTrue(payload["items"][0]["question"])
        self.assertEqual(payload["items"][0]["type"], "open")
        self.assertEqual(payload["meta"]["prompt_version"], "varq_v3")
        self.assertEqual(payload["meta"]["model"], "gpt-5-mini-2025-08-07")
        self.assertEqual(payload["meta"]["usage"]["input_tokens"], 123)
        self.assertEqual(payload["meta"]["usage"]["output_tokens"], 456)
        self.assertIn("X-Request-Id", variants_response.headers)

    def test_generate_question_variants_provider_error_contract(self) -> None:
        with patch(
            "app.services.question_variants._call_openai_responses_parse",
            side_effect=ApiError(
                status_code=502,
                code="UPSTREAM_AI_ERROR",
                message="OpenAI returned an error response",
            ),
        ):
            response = self.client.post(
                "/v1/static/generate-question-variants",
                headers=self._auth_headers(),
                json=self._variants_payload(),
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_AI_ERROR")

    def test_generate_question_variants_malformed_payload_contract(self) -> None:
        with patch(
            "app.services.question_variants._call_openai_responses_parse",
            return_value={"items": [{}], "usage": {"input_tokens": 1, "output_tokens": 1}},
        ):
            response = self.client.post(
                "/v1/static/generate-question-variants",
                headers=self._auth_headers(),
                json=self._variants_payload(),
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_AI_ERROR")

    def test_generate_question_variants_missing_api_key_contract(self) -> None:
        self.client.app.state.settings = Settings(
            app_name="Noteck AI API Test",
            environment="test",
            database_url=self.client.app.state.settings.database_url,
            jwt_secret="test-secret",
            access_token_ttl_seconds=3600,
            refresh_token_ttl_seconds=7200,
            enable_startup_admin_seed=False,
            startup_admin_email="",
            startup_admin_password="",
            openai_api_key="",
            openai_model="gpt-5-mini-2025-08-07",
            openai_tts_model="gpt-4o-mini-tts",
            openai_timeout_seconds=20.0,
            openai_base_url="https://api.openai.com/v1",
        )
        response = self.client.post(
            "/v1/static/generate-question-variants",
            headers=self._auth_headers(),
            json=self._variants_payload(),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "AI_PROVIDER_NOT_CONFIGURED")

    def test_generate_cloze_variants_success_contract(self) -> None:
        mocked_provider_response = {
            "items": [
                {"cloze_text": "Paris remains the capital of {{c1::France}}."},
                {"cloze_text": "The capital city of {{c1::France}} is Paris."},
            ],
            "usage": {"input_tokens": 11, "output_tokens": 22},
            "model": "gpt-5-mini-2025-08-07",
        }
        with patch(
            "app.services.cloze_variants._call_openai_responses_parse",
            return_value=mocked_provider_response,
        ):
            response = self.client.post(
                "/v1/static/generate-cloze-variants",
                headers=self._auth_headers(),
                json=self._cloze_variants_payload(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["type"], "cloze")
        self.assertTrue(payload["items"][0]["cloze_text"])
        self.assertEqual(payload["meta"]["prompt_version"], "varc_v1")
        self.assertEqual(payload["meta"]["model"], "gpt-5-mini-2025-08-07")
        self.assertEqual(payload["meta"]["usage"]["input_tokens"], 11)
        self.assertEqual(payload["meta"]["usage"]["output_tokens"], 22)
        self.assertIn("X-Request-Id", response.headers)

    def test_generate_cloze_variants_provider_error_contract(self) -> None:
        with patch(
            "app.services.cloze_variants._call_openai_responses_parse",
            side_effect=ApiError(
                status_code=502,
                code="UPSTREAM_AI_ERROR",
                message="OpenAI returned an error response",
            ),
        ):
            response = self.client.post(
                "/v1/static/generate-cloze-variants",
                headers=self._auth_headers(),
                json=self._cloze_variants_payload(),
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_AI_ERROR")

    def test_generate_cloze_variants_malformed_payload_contract(self) -> None:
        with patch(
            "app.services.cloze_variants._call_openai_responses_parse",
            return_value={"items": [{}], "usage": {"input_tokens": 1, "output_tokens": 1}},
        ):
            response = self.client.post(
                "/v1/static/generate-cloze-variants",
                headers=self._auth_headers(),
                json=self._cloze_variants_payload(),
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_AI_ERROR")

    def test_generate_cloze_variants_missing_api_key_contract(self) -> None:
        self.client.app.state.settings = Settings(
            app_name="Noteck AI API Test",
            environment="test",
            database_url=self.client.app.state.settings.database_url,
            jwt_secret="test-secret",
            access_token_ttl_seconds=3600,
            refresh_token_ttl_seconds=7200,
            enable_startup_admin_seed=False,
            startup_admin_email="",
            startup_admin_password="",
            openai_api_key="",
            openai_model="gpt-5-mini-2025-08-07",
            openai_tts_model="gpt-4o-mini-tts",
            openai_timeout_seconds=20.0,
            openai_base_url="https://api.openai.com/v1",
        )
        response = self.client.post(
            "/v1/static/generate-cloze-variants",
            headers=self._auth_headers(),
            json=self._cloze_variants_payload(),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "AI_PROVIDER_NOT_CONFIGURED")

    def test_text_to_speech_success_contract(self) -> None:
        with patch(
            "app.api.v1.static.synthesize_text_to_speech",
            return_value=TextToSpeechResult(
                audio_bytes=b"FAKE_MP3_BYTES",
                content_type="audio/mpeg",
                model="gpt-4o-mini-tts",
                voice="alloy",
                format="mp3",
            ),
        ) as synth_mock:
            tts_response = self.client.post(
                "/v1/static/text-to-speech",
                headers=self._auth_headers(),
                json={
                    "text": "Paris is in {{c1::France}}.",
                    "voice": "alloy",
                    "format": "mp3",
                    "speed": 1.0,
                    "parse_cloze": True,
                },
            )

        self.assertEqual(tts_response.status_code, 200)
        self.assertEqual(tts_response.content, b"FAKE_MP3_BYTES")
        self.assertEqual(tts_response.headers["content-type"], "audio/mpeg")
        self.assertIn("X-Request-Id", tts_response.headers)
        called_input = synth_mock.call_args.args[0]
        self.assertTrue(called_input.parse_cloze)

    def test_text_to_speech_parse_cloze_defaults_false(self) -> None:
        with patch(
            "app.api.v1.static.synthesize_text_to_speech",
            return_value=TextToSpeechResult(
                audio_bytes=b"FAKE_MP3_BYTES",
                content_type="audio/mpeg",
                model="gpt-4o-mini-tts",
                voice="alloy",
                format="mp3",
            ),
        ) as synth_mock:
            response = self.client.post(
                "/v1/static/text-to-speech",
                headers=self._auth_headers(),
                json={"text": "Define a group.", "voice": "alloy", "format": "mp3", "speed": 1.0},
            )
        self.assertEqual(response.status_code, 200)
        called_input = synth_mock.call_args.args[0]
        self.assertFalse(called_input.parse_cloze)

    def test_text_to_speech_provider_error_contract(self) -> None:
        with patch(
            "app.api.v1.static.synthesize_text_to_speech",
            side_effect=ApiError(
                status_code=502,
                code="UPSTREAM_AI_ERROR",
                message="OpenAI returned an error response",
            ),
        ):
            response = self.client.post(
                "/v1/static/text-to-speech",
                headers=self._auth_headers(),
                json={"text": "Define a group.", "voice": "alloy", "format": "mp3", "speed": 1.0},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_AI_ERROR")

    def test_text_to_speech_missing_api_key_contract(self) -> None:
        self.client.app.state.settings = Settings(
            app_name="Noteck AI API Test",
            environment="test",
            database_url=self.client.app.state.settings.database_url,
            jwt_secret="test-secret",
            access_token_ttl_seconds=3600,
            refresh_token_ttl_seconds=7200,
            enable_startup_admin_seed=False,
            startup_admin_email="",
            startup_admin_password="",
            openai_api_key="",
            openai_model="gpt-5-mini-2025-08-07",
            openai_tts_model="gpt-4o-mini-tts",
            openai_timeout_seconds=20.0,
            openai_base_url="https://api.openai.com/v1",
        )
        response = self.client.post(
            "/v1/static/text-to-speech",
            headers=self._auth_headers(),
            json={"text": "Define a group.", "voice": "alloy", "format": "mp3", "speed": 1.0},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "AI_PROVIDER_NOT_CONFIGURED")

    def test_evaluate_answer_success_contract(self) -> None:
        mocked_provider_response = {
            "verdict": "partial",
            "score": 0.7,
            "feedback": "Good, but you missed associativity.",
            "missing_points": ["associativity"],
            "usage": {"input_tokens": 98, "output_tokens": 120},
            "model": "gpt-5-mini-2025-08-07",
        }
        with patch(
            "app.services.evaluate_answer._call_openai_responses_parse",
            return_value=mocked_provider_response,
        ):
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

        self.assertEqual(eval_response.status_code, 200)
        payload = eval_response.json()
        self.assertEqual(payload["verdict"], "partial")
        self.assertEqual(payload["score"], 0.7)
        self.assertEqual(payload["feedback"], "Good, but you missed associativity.")
        self.assertEqual(payload["missing_points"], ["associativity"])
        self.assertEqual(payload["meta"]["prompt_version"], "eval_v2")
        self.assertEqual(payload["meta"]["model"], "gpt-5-mini-2025-08-07")
        self.assertEqual(payload["meta"]["usage"]["input_tokens"], 98)
        self.assertEqual(payload["meta"]["usage"]["output_tokens"], 120)
        self.assertIn("X-Request-Id", eval_response.headers)

    def test_evaluate_answer_provider_error_contract(self) -> None:
        with patch(
            "app.services.evaluate_answer._call_openai_responses_parse",
            side_effect=ApiError(
                status_code=502,
                code="UPSTREAM_AI_ERROR",
                message="OpenAI returned an error response",
            ),
        ):
            response = self.client.post(
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

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_AI_ERROR")

    def test_evaluate_answer_missing_api_key_contract(self) -> None:
        self.client.app.state.settings = Settings(
            app_name="Noteck AI API Test",
            environment="test",
            database_url=self.client.app.state.settings.database_url,
            jwt_secret="test-secret",
            access_token_ttl_seconds=3600,
            refresh_token_ttl_seconds=7200,
            enable_startup_admin_seed=False,
            startup_admin_email="",
            startup_admin_password="",
            openai_api_key="",
            openai_model="gpt-5-mini-2025-08-07",
            openai_tts_model="gpt-4o-mini-tts",
            openai_timeout_seconds=20.0,
            openai_base_url="https://api.openai.com/v1",
        )
        response = self.client.post(
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
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "AI_PROVIDER_NOT_CONFIGURED")

    def test_shell_endpoints_validate_request_payloads(self) -> None:
        empty_text_response = self.client.post(
            "/v1/static/text-to-speech",
            headers=self._auth_headers(),
            json={"text": "", "voice": "alloy", "format": "mp3", "speed": 1.0},
        )
        self.assertEqual(empty_text_response.status_code, 422)
        self.assertEqual(empty_text_response.json()["error"]["code"], "INVALID_REQUEST")

        speed_response = self.client.post(
            "/v1/static/text-to-speech",
            headers=self._auth_headers(),
            json={"text": "Define a group.", "voice": "alloy", "format": "mp3", "speed": 2.5},
        )
        self.assertEqual(speed_response.status_code, 422)
        self.assertEqual(speed_response.json()["error"]["code"], "INVALID_REQUEST")

        format_response = self.client.post(
            "/v1/static/text-to-speech",
            headers=self._auth_headers(),
            json={"text": "Define a group.", "voice": "alloy", "format": "ogg", "speed": 1.0},
        )
        self.assertEqual(format_response.status_code, 422)
        self.assertEqual(format_response.json()["error"]["code"], "INVALID_REQUEST")

        invalid_eval_response = self.client.post(
            "/v1/active/evaluate-answer",
            headers=self._auth_headers(),
            json={
                "question": "",
                "expected_answer": "A set with operation, identity, inverses, and associativity.",
                "user_answer": "A set with operation and identity.",
                "grading": {"strictness": "strict", "allow_paraphrase": True},
                "output_format": "brief",
            },
        )
        self.assertEqual(invalid_eval_response.status_code, 422)
        self.assertEqual(invalid_eval_response.json()["error"]["code"], "INVALID_REQUEST")

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
