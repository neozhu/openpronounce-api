"""Public API response schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from openpronounce_api.schemas.requests import ResolvedAssessmentMode


class AssessmentStatus(StrEnum):
    """Reliability-gated assessment status."""

    ASSESSED = "assessed"
    UNCERTAIN = "uncertain"


class ScoreBreakdown(BaseModel):
    """Pronunciation dimension scores on a 0-100 scale (present only when reliable=true)."""

    overall: float = Field(..., description="Overall trained pronunciation score (0-100).")
    accuracy: float | None = Field(default=None, description="Segmental accuracy score (0-100).")
    fluency: float | None = Field(default=None, description="Fluency score (0-100).")
    prosodic: float | None = Field(default=None, description="Prosodic quality score (0-100).")


class PhonemeFeedback(BaseModel):
    """Phoneme-level alignment and error probability from upstream OpenPronounce."""

    expected: str = Field(..., description="Expected IPA phoneme.")
    heard: str | None = Field(default=None, description="Recognized IPA phoneme, if supplied.")
    confidence: float | None = Field(
        default=None,
        description="Model probability that this phoneme was mispronounced (0-1).",
    )
    start_seconds: float | None = Field(default=None, description="Phoneme start time in seconds.")
    end_seconds: float | None = Field(default=None, description="Phoneme end time in seconds.")


class WordFeedback(BaseModel):
    """Word-level assessment report using 0-based word positions."""

    text: str = Field(..., description="Target word from the expected text.")
    position: int = Field(..., ge=0, description="0-based index of the word in the target text.")
    start_seconds: float | None = Field(default=None, description="Word start time in seconds.")
    end_seconds: float | None = Field(default=None, description="Word end time in seconds.")
    mispronounced: bool = Field(..., description="Whether upstream flagged the word as incorrect.")
    missing: bool = Field(..., description="Whether upstream flagged the word as not spoken.")
    expected_ipa: str | None = Field(default=None, description="Expected IPA pronunciation.")
    heard_ipa: str | None = Field(default=None, description="Recognized IPA pronunciation.")
    confidence: float | None = Field(
        default=None,
        description="Upstream rejection confidence (0-1), when available.",
    )
    phonemes: list[PhonemeFeedback] = Field(
        default_factory=list,
        description="Per-phoneme breakdown supplied by upstream (empty if not available).",
    )


class ExtraSpeechItem(BaseModel):
    """Speech detected outside the expected reading text."""

    after_position: int | None = Field(
        default=None,
        description="0-based position of the preceding target word, if any.",
    )
    before_position: int | None = Field(
        default=None,
        description="0-based position of the following target word, if any.",
    )
    start_seconds: float | None = Field(default=None, description="Extra speech start time.")
    end_seconds: float | None = Field(default=None, description="Extra speech end time.")
    heard_ipa: str | None = Field(default=None, description="Recognized IPA phones heard.")


class AudioQualitySummary(BaseModel):
    """Recording duration and upstream signal-quality flags."""

    duration_seconds: float = Field(..., ge=0.0, description="Decoded audio duration in seconds.")
    issues: list[str] = Field(
        default_factory=list,
        description="Safe audio quality issue flags reported by upstream.",
    )


class EngineMetadata(BaseModel):
    """Scoring engine identifier and pinned version."""

    name: str = Field(..., examples=["openpronounce"])
    version: str = Field(..., examples=["1.1.0"])


class AssessResponse(BaseModel):
    """Unified successful response for both multipart upload and HTTPS URL endpoints."""

    request_id: str = Field(..., examples=["01JXYZEXAMPLE0000000000000"])
    status: AssessmentStatus
    text: str
    language: str = Field(..., examples=["en"])
    mode: ResolvedAssessmentMode
    reliable: bool
    reasons: list[str] = Field(default_factory=list)
    scores: ScoreBreakdown | None = None
    transcription: str | None = None
    words: list[WordFeedback] = Field(default_factory=list)
    extra_speech: list[ExtraSpeechItem] = Field(default_factory=list)
    audio_quality: AudioQualitySummary
    engine: EngineMetadata


class ErrorDetail(BaseModel):
    """Sanitized machine-readable error detail."""

    code: str = Field(..., examples=["AUDIO_UNSUPPORTED_FORMAT"])
    message: str = Field(..., examples=["Unsupported or invalid audio format"])


class ErrorResponse(BaseModel):
    """Standardized error JSON envelope for all non-2xx responses."""

    request_id: str
    error: ErrorDetail


class HealthLiveResponse(BaseModel):
    """Liveness probe response."""

    status: str = Field(default="ok", examples=["ok"])


class HealthReadyResponse(BaseModel):
    """Readiness probe response."""

    status: str = Field(..., examples=["ready"])
    engine: EngineMetadata


class VersionResponse(BaseModel):
    """Application and scoring engine version metadata."""

    app_name: str
    app_version: str
    engine: EngineMetadata
    device: str = Field(default="cpu", examples=["cpu"])
