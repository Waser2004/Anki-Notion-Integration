"""Active AI feature endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.dependencies import get_current_user, get_settings
from app.db.models import UserRecord
from app.services.evaluate_answer import AnswerEvaluationInput, evaluate_answer


router = APIRouter(prefix="/active", tags=["active"])


class GradingRequest(BaseModel):
    """Grading options for answer evaluation."""

    strictness: Literal["low", "medium", "high"] = "medium"
    allow_paraphrase: bool = True


class EvaluateAnswerRequest(BaseModel):
    """Final request schema for answer evaluation."""

    question: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    user_answer: str = Field(min_length=1)
    grading: GradingRequest = Field(default_factory=GradingRequest)
    output_format: Literal["short", "full"] = "short"


class TokenUsage(BaseModel):
    """Token usage metadata captured from provider responses."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class EvaluateAnswerMeta(BaseModel):
    """Metadata envelope for evaluate-answer responses."""

    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


class EvaluateAnswerResponse(BaseModel):
    """Response payload for answer evaluation results."""

    verdict: Literal["correct", "partial", "incorrect"]
    score: float = Field(ge=0.0, le=1.0)
    feedback: str = Field(min_length=1)
    missing_points: list[str]
    meta: EvaluateAnswerMeta


@router.post("/evaluate-answer", response_model=EvaluateAnswerResponse)
def evaluate_answer_endpoint(
    request: EvaluateAnswerRequest,
    __: UserRecord = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> EvaluateAnswerResponse:
    """Evaluate a user answer against a reference answer via the configured AI provider."""
    service_result = evaluate_answer(
        AnswerEvaluationInput(
            question=request.question,
            expected_answer=request.expected_answer,
            user_answer=request.user_answer,
            strictness=request.grading.strictness,
            allow_paraphrase=request.grading.allow_paraphrase,
            output_format=request.output_format,
        ),
        settings,
    )

    return EvaluateAnswerResponse(
        verdict=service_result.verdict,
        score=service_result.score,
        feedback=service_result.feedback,
        missing_points=service_result.missing_points,
        meta=EvaluateAnswerMeta(
            prompt_version=service_result.prompt_version,
            model=service_result.model,
            usage=TokenUsage(
                input_tokens=service_result.usage.input_tokens,
                output_tokens=service_result.usage.output_tokens,
            ),
        ),
    )
