"""Static AI feature endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.dependencies import get_current_user, get_settings
from app.db.models import UserRecord
from app.services.cloze_variants import (
    ClozeVariantConstraints as ServiceClozeVariantConstraints,
    ClozeVariantGenerationInput,
    generate_cloze_variants,
)
from app.services.question_variants import (
    QuestionVariantConstraints as ServiceQuestionVariantConstraints,
    QuestionVariantGenerationInput,
    generate_question_variants,
)
from app.services.text_to_speech import TextToSpeechInput, synthesize_text_to_speech


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


class GenerateClozeVariantsConstraints(BaseModel):
    """Constraint knobs for cloze variant generation."""

    no_trick_questions: bool = True
    keep_length_similar: bool = True


class GenerateClozeVariantsRequest(BaseModel):
    """Final request schema for cloze variant generation."""

    cloze_text: str = Field(min_length=1)
    number_variations: int = Field(ge=1, le=20)
    language: str = Field(default="en", min_length=2, max_length=10)
    style: str = Field(default="exam", min_length=1)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    constraints: GenerateClozeVariantsConstraints = Field(default_factory=GenerateClozeVariantsConstraints)


class GenerateClozeVariantItem(BaseModel):
    """A generated cloze variant item for API responses."""

    cloze_text: str = Field(min_length=1)
    type: Literal["cloze"] = "cloze"


class GenerateClozeVariantsMeta(BaseModel):
    """Metadata envelope for generated cloze variant responses."""

    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


class GenerateClozeVariantsResponse(BaseModel):
    """Response payload for cloze variant generation results."""

    items: list[GenerateClozeVariantItem]
    meta: GenerateClozeVariantsMeta


class TextToSpeechRequest(BaseModel):
    """Final request schema for text-to-speech."""

    text: str = Field(min_length=1)
    voice: str = Field(default="alloy", min_length=1)
    format: Literal["mp3", "wav"] = "mp3"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    parse_cloze: bool = False


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


@router.post("/generate-cloze-variants", response_model=GenerateClozeVariantsResponse)
def generate_cloze_variants_endpoint(
    request: GenerateClozeVariantsRequest,
    __: UserRecord = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> GenerateClozeVariantsResponse:
    """Generate unique cloze variants while preserving the same cloze targets."""
    service_payload = ClozeVariantGenerationInput(
        cloze_text=request.cloze_text,
        number_variations=request.number_variations,
        language=request.language,
        style=request.style,
        difficulty=request.difficulty,
        constraints=ServiceClozeVariantConstraints(
            no_trick_questions=request.constraints.no_trick_questions,
            keep_length_similar=request.constraints.keep_length_similar,
        ),
    )

    service_result = generate_cloze_variants(service_payload, settings)
    return GenerateClozeVariantsResponse(
        items=[GenerateClozeVariantItem(cloze_text=item.cloze_text, type="cloze") for item in service_result.items],
        meta=GenerateClozeVariantsMeta(
            prompt_version=service_result.prompt_version,
            model=service_result.model,
            usage=TokenUsage(
                input_tokens=service_result.usage.input_tokens,
                output_tokens=service_result.usage.output_tokens,
            ),
        ),
    )


@router.post("/text-to-speech")
def text_to_speech(
    request: TextToSpeechRequest,
    __: UserRecord = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Convert text into speech audio and return the binary media response."""
    service_result = synthesize_text_to_speech(
        TextToSpeechInput(
            text=request.text,
            voice=request.voice,
            format=request.format,
            speed=request.speed,
            parse_cloze=request.parse_cloze,
        ),
        settings,
    )
    return Response(
        content=service_result.audio_bytes,
        media_type=service_result.content_type,
        headers={"Content-Disposition": f"inline; filename=tts.{request.format}"},
    )
