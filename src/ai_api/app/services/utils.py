"""Shared helpers for AI service modules."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import ApiError


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting data returned by the upstream AI provider."""

    input_tokens: int
    output_tokens: int


def _load_system_prompt(prompt_file: Path, *, load_error_message: str, empty_error_message: str) -> str:
    """Load and cache a system prompt from disk with consistent API error handling."""
    return _read_prompt_from_disk(str(prompt_file), load_error_message, empty_error_message)


@lru_cache(maxsize=None)
def _read_prompt_from_disk(prompt_file: str, load_error_message: str, empty_error_message: str) -> str:
    """Read prompt text from disk once per unique file/message combination."""
    try:
        prompt = Path(prompt_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ApiError(
            status_code=500,
            code="PROMPT_LOAD_ERROR",
            message=load_error_message,
            details={"error_type": exc.__class__.__name__},
        ) from exc

    if not prompt:
        raise ApiError(
            status_code=500,
            code="PROMPT_LOAD_ERROR",
            message=empty_error_message,
        )

    return prompt


def _extract_usage(response_json: dict[str, Any]) -> TokenUsage:
    """Read token usage fields from normalized structured output."""
    usage_payload = response_json.get("usage")
    if not isinstance(usage_payload, dict):
        return TokenUsage(input_tokens=0, output_tokens=0)

    input_tokens = usage_payload.get("input_tokens")
    output_tokens = usage_payload.get("output_tokens")

    if not isinstance(input_tokens, int) or input_tokens < 0:
        input_tokens = 0
    if not isinstance(output_tokens, int) or output_tokens < 0:
        output_tokens = 0

    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
