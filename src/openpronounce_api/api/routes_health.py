"""Practice page and service status endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.schemas.responses import (
    EngineMetadata,
    ErrorResponse,
    HealthLiveResponse,
    HealthReadyResponse,
    VersionResponse,
)
from openpronounce_api.services.pronunciation import PronunciationService

router = APIRouter(tags=["Operations"])
PRACTICE_PAGE = Path(__file__).resolve().parents[1] / "practice.html"


@router.get("/practice", include_in_schema=False)
async def practice() -> FileResponse:
    return FileResponse(PRACTICE_PAGE, media_type="text/html")


@router.get("/", include_in_schema=False)
async def root_redirect(request: Request) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    if settings.enable_docs:
        return RedirectResponse(url="/docs", status_code=307)
    raise AppError(
        status_code=404,
        code=ErrorCode.INVALID_REQUEST,
        message="Resource not found",
    )


@router.get(
    "/health/live",
    response_model=HealthLiveResponse,
    summary="Liveness probe",
    description=(
        "Returns 200 when the HTTP process loop is responsive. Never triggers model loading."
    ),
)
async def health_live() -> HealthLiveResponse:
    return HealthLiveResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=HealthReadyResponse,
    responses={503: {"model": ErrorResponse, "description": "Engine not ready"}},
    summary="Readiness probe",
    description="Returns 200 when the scoring engine is initialized and ready to accept work.",
)
async def health_ready(request: Request) -> HealthReadyResponse:
    service: PronunciationService = request.app.state.pronunciation_service
    engine = service.engine
    if not engine.is_ready:
        raise AppError(
            status_code=503,
            code=ErrorCode.MODEL_UNAVAILABLE,
            message="Scoring engine is not ready",
        )
    return HealthReadyResponse(
        status="ready",
        engine=EngineMetadata(
            name=engine.engine_name,
            version=engine.engine_version,
        ),
    )


@router.get(
    "/version",
    response_model=VersionResponse,
    summary="Service and engine version metadata",
)
async def version(request: Request) -> VersionResponse:
    settings: Settings = request.app.state.settings
    service: PronunciationService = request.app.state.pronunciation_service
    engine = service.engine
    return VersionResponse(
        app_name=settings.app_name,
        app_version=settings.app_version,
        engine=EngineMetadata(
            name=engine.engine_name,
            version=engine.engine_version,
        ),
        device=settings.openpronounce_device,
    )
