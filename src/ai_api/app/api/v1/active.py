"""Active AI feature endpoints (scaffolded for future implementation)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.core.errors import NotImplementedFeatureError
from app.db.models import UserRecord


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


@router.post("/evaluate-answer")
def evaluate_answer_shell(
    _: EvaluateAnswerRequest,
    __: UserRecord = Depends(get_current_user),
) -> None:
    """Shell endpoint returning a stable NOT_IMPLEMENTED contract."""
    raise NotImplementedFeatureError(feature="evaluate-answer", target_phase="ai-provider integration")
