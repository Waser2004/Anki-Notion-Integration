"""OpenAI-backed evaluation service for active answer grading endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import ApiError
from app.services.utils import TokenUsage, _extract_usage, _load_system_prompt


PROMPT_VERSION = "eval_v2"
SYSTEM_PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / f"{PROMPT_VERSION}_system_prompt.txt"


@dataclass(frozen=True)
class AnswerEvaluationInput:
    """Input payload consumed by the OpenAI answer evaluation service."""

    question: str
    expected_answer: str
    user_answer: str
    strictness: Literal["low", "medium", "high"]
    allow_paraphrase: bool
    output_format: Literal["short", "full"]


@dataclass(frozen=True)
class AnswerEvaluationResult:
    """Structured output consumed by the active evaluate-answer endpoint."""

    verdict: Literal["correct", "partial", "incorrect"]
    score: float
    feedback: str
    missing_points: list[str]
    model: str
    usage: TokenUsage
    prompt_version: str = PROMPT_VERSION


class _StructuredAnswerEvaluationOutput(BaseModel):
    """Structured output schema used by OpenAI responses.parse."""

    verdict: Literal["correct", "partial", "incorrect"]
    score: float
    feedback: str
    missing_points: list[str]


def evaluate_answer(payload: AnswerEvaluationInput, settings: Settings) -> AnswerEvaluationResult:
    """Evaluate a learner answer against the reference answer via OpenAI Responses API."""
    api_key = settings.openai_api_key.strip()
    if not api_key:
        raise ApiError(
            status_code=503,
            code="AI_PROVIDER_NOT_CONFIGURED",
            message="OpenAI API key is not configured",
        )

    request_body = _build_openai_request(payload)
    response_json = _call_openai_responses_parse(request_body, settings, api_key)

    score = _normalize_score(response_json.get("score"))
    verdict = _normalize_verdict(response_json.get("verdict"), score)
    feedback = _normalize_feedback(response_json.get("feedback"), payload.output_format)
    missing_points = _normalize_missing_points(response_json.get("missing_points"))
    usage = _extract_usage(response_json)

    model = str(response_json.get("model") or settings.openai_model)
    return AnswerEvaluationResult(
        verdict=verdict,
        score=score,
        feedback=feedback,
        missing_points=missing_points,
        model=model,
        usage=usage,
    )


def _build_openai_request(payload: AnswerEvaluationInput) -> dict[str, Any]:
    """Build a structured output request for learner answer evaluation."""
    system_prompt = _load_system_prompt(
        SYSTEM_PROMPT_FILE,
        load_error_message="Failed to load evaluation system prompt",
        empty_error_message="Evaluation system prompt file is empty",
    )
    user_prompt = (
        f"Question: {payload.question}\n"
        f"Expected answer: {payload.expected_answer}\n"
        f"User answer: {payload.user_answer}\n"
        f"Strictness: {payload.strictness}\n"
        f"Allow paraphrase: {'yes' if payload.allow_paraphrase else 'no'}\n"
        f"Output format: {payload.output_format}"
    )

    return {
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "text_format": _StructuredAnswerEvaluationOutput,
    }


def _call_openai_responses_parse(body: dict[str, Any], settings: Settings, api_key: str) -> dict[str, Any]:
    """Call OpenAI Responses parse API and return normalized structured data."""
    client = OpenAI(
        api_key=api_key,
        base_url=settings.openai_base_url.rstrip("/"),
        timeout=settings.openai_timeout_seconds,
    )

    try:
        response = client.responses.parse(
            model=settings.openai_model,
            input=body["input"],
            text_format=body["text_format"],
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

    parsed_output = response.output_parsed
    if parsed_output is None:
        raise ApiError(
            status_code=502,
            code="UPSTREAM_AI_ERROR",
            message="OpenAI response did not match structured output schema",
        )

    if not isinstance(parsed_output, _StructuredAnswerEvaluationOutput):
        raise ApiError(
            status_code=502,
            code="UPSTREAM_AI_ERROR",
            message="OpenAI structured output type mismatch",
        )

    usage = response.usage
    usage_payload = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }

    return {
        "verdict": parsed_output.verdict,
        "score": parsed_output.score,
        "feedback": parsed_output.feedback,
        "missing_points": parsed_output.missing_points,
        "usage": usage_payload,
        "model": getattr(response, "model", settings.openai_model),
    }


def _normalize_score(raw_score: Any) -> float:
    """Clamp provider score into the stable API range [0.0, 1.0]."""
    if isinstance(raw_score, bool):
        return 0.0
    if isinstance(raw_score, (int, float)):
        return max(0.0, min(1.0, float(raw_score)))
    return 0.0


def _normalize_verdict(raw_verdict: Any, score: float) -> Literal["correct", "partial", "incorrect"]:
    """Normalize provider verdict and derive a fallback from score if needed."""
    if isinstance(raw_verdict, str):
        verdict = raw_verdict.strip().lower()
        if verdict in {"correct", "partial", "incorrect"}:
            return verdict

    if score >= 0.9:
        return "correct"
    if score >= 0.4:
        return "partial"
    return "incorrect"


def _normalize_feedback(raw_feedback: Any, output_format: Literal["short", "full"]) -> str:
    """Normalize provider feedback text and provide safe fallback text."""
    if isinstance(raw_feedback, str):
        feedback = raw_feedback.strip()
        if feedback:
            return feedback

    if output_format == "full":
        return "The answer could not be fully evaluated. Please review the expected answer and try again."
    return "The answer could not be fully evaluated."


def _normalize_missing_points(raw_missing_points: Any) -> list[str]:
    """Normalize and deduplicate missing point entries while preserving order."""
    if not isinstance(raw_missing_points, list):
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_missing_points:
        if not isinstance(item, str):
            continue

        normalized = item.strip()
        if not normalized:
            continue

        fingerprint = normalized.casefold()
        if fingerprint in seen:
            continue

        seen.add(fingerprint)
        cleaned.append(normalized)

    return cleaned
