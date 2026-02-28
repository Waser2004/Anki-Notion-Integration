"""HTTP client for the external Noteck AI API."""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Any
from urllib import error, request

from .ai_settings import AiSettingsStore


class AiApiError(RuntimeError):
    """Raised when an AI API request fails."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class LoginResult:
    """Auth response payload returned by the AI API token endpoint."""

    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class MeResult:
    """Authenticated user payload returned by `/v1/auth/me`."""

    user_id: str
    email: str
    plan: str
    month_tokens_left: int | None
    reset_at: str | None


@dataclass(frozen=True)
class EvaluateAnswerResult:
    """Answer evaluation response payload."""

    verdict: str
    score: float
    feedback: str
    missing_points: list[str]


class AiApiClient:
    """Small API client with token refresh support and JSON error normalization."""

    def __init__(self, settings_store: AiSettingsStore, timeout_seconds: float = 20.0) -> None:
        self._settings_store = settings_store
        self._timeout_seconds = timeout_seconds
        self._refresh_lock = threading.Lock()

    def login(self, base_url: str, email: str, password: str) -> LoginResult:
        """Exchange credentials for a token pair and persist it."""
        # Login request
        payload = self._request_json(
            method="POST",
            base_url=base_url,
            path="/v1/auth/token",
            body={"email": email, "password": password},
            require_auth=False,
            retry_on_unauthorized=False,
        )

        # extract and validate tokens from response payload.
        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")
        if not access_token or not refresh_token:
            raise AiApiError("Login succeeded but no tokens were returned.")

        expires_in_raw = payload.get("expires_in")
        try:
            expires_in = int(expires_in_raw)
        except (TypeError, ValueError):
            expires_in = 0

        # Persist tokens and base URL for future requests.s
        self._settings_store.set_tokens(access_token, refresh_token)
        self._settings_store.set_value("ai_api_base_url", base_url.strip())
        self._settings_store.set_value("ai_api_email", email.strip())

        return LoginResult(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    def logout(self) -> None:
        """Clear local tokens to log out in the addon."""
        self._settings_store.clear_tokens()

    def me(self, base_url: str) -> MeResult:
        """Return the current authenticated user profile."""
        payload = self._request_json(
            method="GET",
            base_url=base_url,
            path="/v1/auth/me",
            body=None,
            require_auth=True,
            retry_on_unauthorized=True,
        )

        # extract and validate quota fields from response payload.
        quota             = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
        month_tokens_left = quota.get("month_tokens_left") if isinstance(quota, dict) else None
        reset_at          = quota.get("reset_at") if isinstance(quota, dict) else None
        if not isinstance(month_tokens_left, int):
            month_tokens_left = None
        if not isinstance(reset_at, str):
            reset_at = None

        # normalize and return result dataclass.
        return MeResult(
            user_id=str(payload.get("user_id") or ""),
            email=str(payload.get("email") or ""),
            plan=str(payload.get("plan") or ""),
            month_tokens_left=month_tokens_left,
            reset_at=reset_at,
        )

    def generate_question_variants(
        self,
        *,
        base_url: str,
        question: str,
        answer: str,
        number_variations: int,
        style: str,
        difficulty: str,
        no_trick_questions: bool,
        keep_length_similar: bool,
        language: str = "en",
    ) -> list[str]:
        """Generate question variants and return ordered question text list."""
        payload = self._request_json(
            method="POST",
            base_url=base_url,
            path="/v1/static/generate-question-variants",
            body={
                "question": question,
                "answer": answer,
                "number_variations": number_variations,
                "language": language,
                "style": style,
                "difficulty": difficulty,
                "constraints": {
                    "no_trick_questions": bool(no_trick_questions),
                    "keep_length_similar": bool(keep_length_similar),
                },
            },
            require_auth=True,
            retry_on_unauthorized=True,
        )

        # extract and validate question variants from response payload.
        items = payload.get("items")
        if not isinstance(items, list):
            raise AiApiError("Question variant response did not include an items list.")
        
        variants: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            question_value = item.get("question")
            if isinstance(question_value, str) and question_value.strip():
                variants.append(question_value.strip())
        if not variants:
            raise AiApiError("Question variant response contained no valid questions.")
        
        return variants

    def generate_cloze_variants(
        self,
        *,
        base_url: str,
        cloze_text: str,
        number_variations: int,
        style: str,
        difficulty: str,
        no_trick_questions: bool,
        keep_length_similar: bool,
        language: str = "en",
    ) -> list[str]:
        """Generate cloze variants and return ordered cloze text list."""
        payload = self._request_json(
            method="POST",
            base_url=base_url,
            path="/v1/static/generate-cloze-variants",
            body={
                "cloze_text": cloze_text,
                "number_variations": number_variations,
                "language": language,
                "style": style,
                "difficulty": difficulty,
                "constraints": {
                    "no_trick_questions": bool(no_trick_questions),
                    "keep_length_similar": bool(keep_length_similar),
                },
            },
            require_auth=True,
            retry_on_unauthorized=True,
        )

        # Extract and validate cloze variants from response payload.
        items = payload.get("items")
        if not isinstance(items, list):
            raise AiApiError("Cloze variant response did not include an items list.")

        variants: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cloze_value = item.get("cloze_text")
            if isinstance(cloze_value, str) and cloze_value.strip():
                variants.append(cloze_value.strip())
        if not variants:
            raise AiApiError("Cloze variant response contained no valid cloze text values.")

        return variants

    def text_to_speech(
        self,
        *,
        base_url: str,
        text: str,
        voice: str,
        speed: float,
        output_format: str = "mp3",
        parse_cloze: bool = False,
    ) -> bytes:
        """Generate speech audio bytes for one text input."""
        status_code, headers, body_bytes = self._request_raw(
            method="POST",
            base_url=base_url,
            path="/v1/static/text-to-speech",
            body={
                "text": text,
                "voice": voice,
                "format": output_format,
                "speed": speed,
                "parse_cloze": bool(parse_cloze),
            },
            require_auth=True,
            retry_on_unauthorized=True,
        )
        _ = status_code
        _ = headers

        if not body_bytes:
            raise AiApiError("Text-to-speech response returned empty audio.")
        return body_bytes

    def evaluate_answer(
        self,
        *,
        base_url: str,
        question: str,
        expected_answer: str,
        user_answer: str,
        strictness: str,
        allow_paraphrase: bool,
        output_format: str,
    ) -> EvaluateAnswerResult:
        """Evaluate a typed learner answer and return verdict details."""
        payload = self._request_json(
            method="POST",
            base_url=base_url,
            path="/v1/active/evaluate-answer",
            body={
                "question": question,
                "expected_answer": expected_answer,
                "user_answer": user_answer,
                "grading": {
                    "strictness": strictness,
                    "allow_paraphrase": bool(allow_paraphrase),
                },
                "output_format": output_format,
            },
            require_auth=True,
            retry_on_unauthorized=True,
        )

        # extract and validate API response fields.
        raw_missing_points = payload.get("missing_points")
        missing_points     = [item for item in raw_missing_points if isinstance(item, str)] if isinstance(raw_missing_points, list) else []
        raw_score          = payload.get("score")
        score              = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
        
        # normalize and return result dataclass.
        return EvaluateAnswerResult(
            verdict=str(payload.get("verdict") or "incorrect"),
            score=max(0.0, min(1.0, score)),
            feedback=str(payload.get("feedback") or ""),
            missing_points=missing_points,
        )

    def _refresh_access_token(self, base_url: str) -> None:
        """Refresh and persist access token from the currently stored refresh token."""
        token_pair = self._settings_store.get_tokens()
        if token_pair is None or not token_pair.refresh_token:
            raise AiApiError("No refresh token available. Please login again.")

        payload = self._request_json(
            method="POST",
            base_url=base_url,
            path="/v1/auth/refresh",
            body={"refresh_token": token_pair.refresh_token},
            require_auth=False,
            retry_on_unauthorized=False,
        )

        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")

        if not access_token or not refresh_token:
            raise AiApiError("Token refresh succeeded but no new tokens were returned.")
        
        self._settings_store.set_tokens(access_token, refresh_token)

    def _request_json(
        self,
        *,
        method: str,
        base_url: str,
        path: str,
        body: dict[str, Any] | None,
        require_auth: bool,
        retry_on_unauthorized: bool,
    ) -> dict[str, Any]:
        """Send one JSON request and return parsed payload."""
        # Send the request and get raw response bytes.
        status_code, _headers, body_bytes = self._request_raw(
            method=method,
            base_url=base_url,
            path=path,
            body=body,
            require_auth=require_auth,
            retry_on_unauthorized=retry_on_unauthorized,
        )

        # parse response to json.
        try:
            parsed = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}

        # Response not valid JSON, raise with included status code for better debugging.
        except json.JSONDecodeError as exc:
            raise AiApiError(f"AI API returned non-JSON response (HTTP {status_code}).") from exc
        
        # parse failed but no exception, throw generic error.
        if not isinstance(parsed, dict):
            raise AiApiError("AI API returned an unexpected response payload.")
        
        return parsed

    def _request_raw(
        self,
        *,
        method: str,
        base_url: str,
        path: str,
        body: dict[str, Any] | None,
        require_auth: bool,
        retry_on_unauthorized: bool,
    ) -> tuple[int, dict[str, str], bytes]:
        """Send one HTTP request and return status, headers, and raw bytes."""
        # Create header and body for the request.
        request_url = f"{base_url.rstrip('/')}{path}"
        request_headers = {"Content-Type": "application/json"}
        access_token_used = ""
        if require_auth:
            token_pair = self._settings_store.get_tokens()
            if token_pair is None or not token_pair.access_token:
                raise AiApiError("Missing access token. Please login first.")
            access_token_used = token_pair.access_token
            request_headers["Authorization"] = f"Bearer {access_token_used}"

        # send request
        encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
        http_request = request.Request(
            request_url,
            data=encoded_body,
            headers=request_headers,
            method=method,
        )

        # validate returned status code and parse response body as bytes
        try:
            with request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                return int(response.status), dict(response.headers.items()), bytes(response.read())
        
        # Connection erroror try again (only once)
        except error.HTTPError as exc:
            status_code = int(exc.code)
            response_body = bytes(exc.read())
            if require_auth and retry_on_unauthorized and status_code == 401:
                self._refresh_access_token_threadsafe(
                    base_url=base_url,
                    stale_access_token=access_token_used,
                )
                return self._request_raw(
                    method=method,
                    base_url=base_url,
                    path=path,
                    body=body,
                    require_auth=require_auth,
                    retry_on_unauthorized=False,
                )
            self._raise_api_error(status_code, response_body)
        
        # Raise API error for connection issues, timeouts, DNS errors, etc.
        except error.URLError as exc:
            raise AiApiError(f"Failed to reach AI API at {request_url}: {exc}") from exc

    def _refresh_access_token_threadsafe(self, *, base_url: str, stale_access_token: str) -> None:
        """Refresh tokens once for concurrent 401 responses across worker threads."""
        with self._refresh_lock:
            token_pair = self._settings_store.get_tokens()
            if (
                token_pair is not None
                and token_pair.access_token
                and stale_access_token
                and token_pair.access_token != stale_access_token
            ):
                # Another worker already refreshed and persisted a newer token.
                return
            self._refresh_access_token(base_url)

    @staticmethod
    def _raise_api_error(status_code: int, response_body: bytes) -> None:
        """Raise a normalized API error from server response bytes."""
        message = f"AI API request failed with HTTP {status_code}."
        code: str | None = None
        try:
            parsed = json.loads(response_body.decode("utf-8")) if response_body else {}
        except json.JSONDecodeError:
            parsed = {}

        if isinstance(parsed, dict):
            error_payload = parsed.get("error")
            if isinstance(error_payload, dict):
                api_message = error_payload.get("message")
                api_code = error_payload.get("code")
                if isinstance(api_message, str) and api_message.strip():
                    message = api_message.strip()
                if isinstance(api_code, str) and api_code.strip():
                    code = api_code.strip()
        raise AiApiError(message, status_code=status_code, code=code)
