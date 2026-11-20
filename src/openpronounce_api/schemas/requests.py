"""Public API request schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AssessmentMode(StrEnum):
    """Display/metadata hint describing expected reading granularity."""

    AUTO = "auto"
    WORD = "word"
    PHRASE = "phrase"
    SENTENCE = "sentence"


class ResolvedAssessmentMode(StrEnum):
    """Concrete display mode returned in assessment responses."""

    WORD = "word"
    PHRASE = "phrase"
    SENTENCE = "sentence"


class AssessUrlRequest(BaseModel):
    """JSON payload for POST /api/v1/assess/url."""

    model_config = ConfigDict(extra="forbid")

    audio_url: str = Field(
        ...,
        description=(
            "Ephemeral HTTPS URL pointing to a WAV, MP3, FLAC, OGG, or WebM recording "
            "hosted on an allowlisted domain."
        ),
        examples=["https://media.example.org/recordings/answer.webm?signature=redacted"],
    )
    text: str = Field(
        ...,
        description="Expected English word, phrase, or sentence read by the learner.",
        examples=["I like eating apples."],
    )
    language: str = Field(
        default="en",
        description="Language code. Supports 'en' and alias 'en-US' (mapped to upstream 'en').",
        examples=["en"],
    )
    mode: AssessmentMode = Field(
        default=AssessmentMode.AUTO,
        description="Metadata/UI hint only; does not switch model weights.",
        examples=["auto"],
    )
