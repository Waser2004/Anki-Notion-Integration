"""Static AI feature endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.dependencies import get_current_user, get_settings
from app.core.errors import NotImplementedFeatureError
from app.db.models import UserRecord
from app.services.question_variants import (
    QuestionVariantConstraints as ServiceQuestionVariantConstraints,
    QuestionVariantGenerationInput,
    generate_question_variants,
)


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


class GenerateQuestionVariantItem(BaseModel):
    """A generated question variant item for API responses."""

    question: str = Field(min_length=1)
    type: Literal["open"] = "open"


class TokenUsage(BaseModel):
    """Token usage metadata captured from provider responses."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class GenerateQuestionVariantsMeta(BaseModel):
    """Metadata envelope for generated question variant responses."""

    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


class GenerateQuestionVariantsResponse(BaseModel):
    """Response payload for question variant generation results."""

    items: list[GenerateQuestionVariantItem]
    meta: GenerateQuestionVariantsMeta


class TextToSpeechRequest(BaseModel):
    """Final request schema for text-to-speech."""

    text: str = Field(min_length=1)
    voice: str = Field(default="alloy", min_length=1)
    format: Literal["mp3", "wav"] = "mp3"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@router.post("/generate-question-variants", response_model=GenerateQuestionVariantsResponse)
def generate_question_variants_endpoint(
    request: GenerateQuestionVariantsRequest,
    __: UserRecord = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> GenerateQuestionVariantsResponse:
    """Generate unique question variants via the configured AI provider."""
    service_payload = QuestionVariantGenerationInput(
        question=request.question,
        answer=request.answer,
        number_variations=request.number_variations,
        language=request.language,
        style=request.style,
        difficulty=request.difficulty,
        constraints=ServiceQuestionVariantConstraints(
            no_trick_questions=request.constraints.no_trick_questions,
            keep_length_similar=request.constraints.keep_length_similar,
        ),
    )

    service_result = generate_question_variants(service_payload, settings)
    return GenerateQuestionVariantsResponse(
        items=[GenerateQuestionVariantItem(question=item.question, type="open") for item in service_result.items],
        meta=GenerateQuestionVariantsMeta(
            prompt_version=service_result.prompt_version,
            model=service_result.model,
            usage=TokenUsage(
                input_tokens=service_result.usage.input_tokens,
                output_tokens=service_result.usage.output_tokens,
            ),
        ),
    )


@router.post("/text-to-speech")
def text_to_speech_shell(
    _: TextToSpeechRequest,
    __: UserRecord = Depends(get_current_user),
) -> None:
    """Shell endpoint returning a stable NOT_IMPLEMENTED contract."""
    raise NotImplementedFeatureError(feature="text-to-speech", target_phase="ai-provider integration")
