"""OpenAI-backed text-to-speech service for static audio endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.core.config import Settings
from app.core.errors import ApiError
from app.services.utils import _load_system_prompt


_CLOZE_PROMPT_VERSION = "tts_cloze_v1"
_CLOZE_SYSTEM_PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / f"{_CLOZE_PROMPT_VERSION}_system_prompt.txt"
_CLOZE_MARKER_RE = re.compile(r"\{\{c\d+::.*?(?:::.*?)?\}\}")
_CLOZE_PLACEHOLDER_TOKEN = "[blank]"


@dataclass(frozen=True)
class TextToSpeechInput:
    """Input payload consumed by the OpenAI text-to-speech service."""

    text: str
    voice: str
    format: str
    speed: float
    parse_cloze: bool = False


@dataclass(frozen=True)
class TextToSpeechResult:
    """Normalized output consumed by the static text-to-speech endpoint."""

    audio_bytes: bytes
    content_type: str
    model: str
    voice: str
    format: str


def synthesize_text_to_speech(payload: TextToSpeechInput, settings: Settings) -> TextToSpeechResult:
    """Generate speech audio from text via OpenAI and return normalized response data."""
    api_key = settings.openai_api_key.strip()
    if not api_key:
        raise ApiError(
            status_code=503,
            code="AI_PROVIDER_NOT_CONFIGURED",
            message="OpenAI API key is not configured",
        )

    client = OpenAI(
        api_key=api_key,
        base_url=settings.openai_base_url.rstrip("/"),
        timeout=settings.openai_timeout_seconds,
    )

    synthesized_text = payload.text
    instructions: str | None = None
    if payload.parse_cloze:
        synthesized_text = _replace_cloze_markers_with_placeholder(payload.text)
        instructions = _load_system_prompt(
            _CLOZE_SYSTEM_PROMPT_FILE,
            load_error_message="Failed to load cloze text-to-speech system prompt",
            empty_error_message="Cloze text-to-speech system prompt file is empty",
        ).replace("{placeholder}", _CLOZE_PLACEHOLDER_TOKEN)

    request_payload: dict[str, object] = {
        "model": settings.openai_tts_model,
        "voice": payload.voice,
        "input": synthesized_text,
        "response_format": payload.format,
        "speed": payload.speed,
    }
    fallback_payload: dict[str, object] | None = None
    if instructions:
        request_payload["instructions"] = instructions
        # Keep one fallback payload for environments where `instructions` is unsupported.
        fallback_payload = {
            "model": settings.openai_tts_model,
            "voice": payload.voice,
            "input": synthesized_text,
            "response_format": payload.format,
            "speed": payload.speed,
        }

    try:
        response = _create_speech_response(client, request_payload, fallback_payload=fallback_payload)
    except APITimeoutError as exc:
        raise ApiError(
            status_code=502,
            code="UPSTREAM_AI_ERROR",
            message="OpenAI request timed out",
            details={"error_type": exc.__class__.__name__},
        ) from exc
    except APIConnectionError as exc:
        raise ApiError(
            status_code=502,
            code="UPSTREAM_AI_ERROR",
            message="Failed to contact OpenAI",
            details={"error_type": exc.__class__.__name__},
        ) from exc
    except APIStatusError as exc:
        raise ApiError(
            status_code=502,
            code="UPSTREAM_AI_ERROR",
            message="OpenAI returned an error response",
            details={"upstream_status_code": exc.status_code},
        ) from exc

    audio_bytes = _extract_audio_bytes(response)
    content_type = "audio/mpeg" if payload.format == "mp3" else "audio/wav"
    return TextToSpeechResult(
        audio_bytes=audio_bytes,
        content_type=content_type,
        model=settings.openai_tts_model,
        voice=payload.voice,
        format=payload.format,
    )


def _replace_cloze_markers_with_placeholder(text: str) -> str:
    """Replace Anki-style cloze markers with one deterministic spoken placeholder."""
    replaced = _CLOZE_MARKER_RE.sub(_CLOZE_PLACEHOLDER_TOKEN, text)
    normalized = " ".join(replaced.split())
    return normalized if normalized else _CLOZE_PLACEHOLDER_TOKEN


def _create_speech_response(
    client: OpenAI,
    request_payload: dict[str, object],
    *,
    fallback_payload: dict[str, object] | None,
) -> object:
    """Call TTS once and retry without instructions when the SDK rejects that argument."""
    try:
        return client.audio.speech.create(**request_payload)
    except TypeError:
        if fallback_payload is None:
            raise
        return client.audio.speech.create(**fallback_payload)


def _extract_audio_bytes(response: object) -> bytes:
    """Read raw bytes from the OpenAI SDK response object in a defensive way."""
    audio_bytes: bytes
    if hasattr(response, "read") and callable(getattr(response, "read")):
        audio_bytes = bytes(response.read())
    elif hasattr(response, "content"):
        content = getattr(response, "content")
        audio_bytes = bytes(content) if isinstance(content, (bytes, bytearray)) else b""
    elif isinstance(response, (bytes, bytearray)):
        audio_bytes = bytes(response)
    else:
        audio_bytes = b""

    if not audio_bytes:
        raise ApiError(
            status_code=502,
            code="UPSTREAM_AI_ERROR",
            message="OpenAI returned empty audio payload",
        )

    return audio_bytes
