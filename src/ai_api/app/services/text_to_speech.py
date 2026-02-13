"""OpenAI-backed text-to-speech service for static audio endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.core.config import Settings
from app.core.errors import ApiError


@dataclass(frozen=True)
class TextToSpeechInput:
    """Input payload consumed by the OpenAI text-to-speech service."""

    text: str
    voice: str
    format: str
    speed: float


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

    try:
        response = client.audio.speech.create(
            model=settings.openai_tts_model,
            voice=payload.voice,
            input=payload.text,
            response_format=payload.format,
            speed=payload.speed,
        )
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
