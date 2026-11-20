"""Pronunciation assessment routes for multipart file upload and allowlisted HTTPS URL."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile

from openpronounce_api.core.config import Settings
from openpronounce_api.core.logging import get_request_id
from openpronounce_api.core.security import verify_api_key
from openpronounce_api.schemas.requests import AssessmentMode, AssessUrlRequest
from openpronounce_api.schemas.responses import AssessResponse, ErrorResponse
from openpronounce_api.services.pronunciation import PronunciationService

router = APIRouter(prefix="/api/v1", tags=["Pronunciation Assessment"])

_COMMON_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid request parameters or disallowed URL"},
    401: {"model": ErrorResponse, "description": "Missing or invalid X-API-Key"},
    413: {"model": ErrorResponse, "description": "Audio payload exceeds size limit"},
    415: {"model": ErrorResponse, "description": "Unsupported or invalid audio format"},
    422: {"model": ErrorResponse, "description": "Empty, undecodable, or out-of-range duration"},
    429: {"model": ErrorResponse, "description": "CPU inference capacity reached"},
    502: {"model": ErrorResponse, "description": "Remote audio host fetch failed"},
    503: {"model": ErrorResponse, "description": "Scoring engine unavailable"},
    504: {"model": ErrorResponse, "description": "Download, decode, or inference timed out"},
}


async def require_api_key_dependency(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Enforce constant-time API key authentication when REQUIRE_API_KEY is enabled."""
    settings: Settings = request.app.state.settings
    verify_api_key(x_api_key, settings)


@router.post(
    "/assess",
    response_model=AssessResponse,
    responses=_COMMON_ERROR_RESPONSES,
    dependencies=[Depends(require_api_key_dependency)],
    summary="Assess pronunciation from uploaded audio file",
    description=(
        "Accepts a WAV, MP3, FLAC, OGG, or WebM recording via multipart/form-data along with "
        "the expected English reading text, evaluates pronunciation on CPU, and returns a "
        "reliability-gated JSON assessment."
    ),
)
async def assess_upload(
    request: Request,
    audio: UploadFile = File(..., description="Audio recording (WAV, MP3, FLAC, OGG, or WebM)."),
    text: str = Form(..., description="Expected English word, phrase, or sentence."),
    language: str = Form(
        default="en",
        description="Language code ('en' or alias 'en-US').",
    ),
    mode: AssessmentMode = Form(
        default=AssessmentMode.AUTO,
        description="Metadata/UI hint: auto, word, phrase, or sentence.",
    ),
) -> AssessResponse:
    service: PronunciationService = request.app.state.pronunciation_service
    try:
        return await service.assess_upload(
            upload=audio,
            text=text,
            language=language,
            mode=mode,
            request_id=get_request_id(),
        )
    finally:
        await audio.close()


@router.post(
    "/assess/url",
    response_model=AssessResponse,
    responses=_COMMON_ERROR_RESPONSES,
    dependencies=[Depends(require_api_key_dependency)],
    summary="Assess pronunciation from allowlisted HTTPS audio URL",
    description=(
        "Downloads an ephemeral audio recording from an allowlisted HTTPS host using pinned-IP "
        "connections with SSRF controls, then executes the same audio validation and assessment "
        "pipeline as multipart upload."
    ),
)
async def assess_url(
    request: Request,
    payload: AssessUrlRequest,
) -> AssessResponse:
    service: PronunciationService = request.app.state.pronunciation_service
    return await service.assess_url(
        audio_url=payload.audio_url,
        text=payload.text,
        language=payload.language,
        mode=payload.mode,
        request_id=get_request_id(),
    )
