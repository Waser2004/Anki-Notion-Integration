"""Static AI feature endpoints (scaffolded for future implementation)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.core.errors import NotImplementedFeatureError
from app.db.models import UserRecord


router = APIRouter(prefix="/static", tags=["static"])


class GenerateQuestionVariantsConstraints(BaseModel):
    """Constraint knobs for question variant generation."""

    no_trick_questions: bool = True
    keep_length_similar: bool = True


class GenerateQuestionVariantsRequest(BaseModel):
    """Final request schema for question variant generation."""

    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    number_variations: int = Field(ge=1, le=20)
    language: str = Field(default="en", min_length=2, max_length=10)
    style: str = Field(default="exam", min_length=1)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    constraints: GenerateQuestionVariantsConstraints = Field(default_factory=GenerateQuestionVariantsConstraints)


class TextToSpeechRequest(BaseModel):
    """Final request schema for text-to-speech."""

    text: str = Field(min_length=1)
    voice: str = Field(default="alloy", min_length=1)
    format: Literal["mp3", "wav"] = "mp3"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@router.post("/generate-question-variants")
def generate_question_variants_shell(
    _: GenerateQuestionVariantsRequest,
    __: UserRecord = Depends(get_current_user),
) -> None:
    """Shell endpoint returning a stable NOT_IMPLEMENTED contract."""
    raise NotImplementedFeatureError(feature="generate-question-variants", target_phase="ai-provider integration")


@router.post("/text-to-speech")
def text_to_speech_shell(
    _: TextToSpeechRequest,
    __: UserRecord = Depends(get_current_user),
) -> None:
    """Shell endpoint returning a stable NOT_IMPLEMENTED contract."""
    raise NotImplementedFeatureError(feature="text-to-speech", target_phase="ai-provider integration")
