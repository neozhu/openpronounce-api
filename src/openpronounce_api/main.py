"""FastAPI application factory, lifespan management, middleware, and error handlers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from openpronounce_api.adapters.openpronounce_adapter import (
    OpenPronounceAdapter,
    PronunciationEngine,
)
from openpronounce_api.api.routes_assess import router as assess_router
from openpronounce_api.api.routes_health import router as health_router
from openpronounce_api.core.config import Settings, get_settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.core.logging import (
    configure_logging,
    get_request_id,
    reset_request_id,
    sanitize_request_id,
    set_request_id,
)
from openpronounce_api.schemas.responses import ErrorDetail, ErrorResponse
from openpronounce_api.services.audio_downloader import AudioDownloader
from openpronounce_api.services.pronunciation import PronunciationService

logger = logging.getLogger(__name__)


def _error_json(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        request_id=request_id,
        error=ErrorDetail(code=code.value, message=message),
    )
    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=response_headers,
    )


def _map_http_status_to_code(status_code: int) -> tuple[ErrorCode, str]:
    mapping: dict[int, tuple[ErrorCode, str]] = {
        400: (ErrorCode.INVALID_REQUEST, "Invalid request"),
        401: (ErrorCode.UNAUTHORIZED, "Unauthorized"),
        404: (ErrorCode.INVALID_REQUEST, "Resource not found"),
        405: (ErrorCode.INVALID_REQUEST, "Method not allowed"),
        413: (ErrorCode.AUDIO_TOO_LARGE, "Payload exceeds maximum allowed size"),
        415: (ErrorCode.AUDIO_UNSUPPORTED_FORMAT, "Unsupported media type"),
        422: (ErrorCode.INVALID_REQUEST, "Invalid request parameters"),
        429: (ErrorCode.SERVER_BUSY, "Server busy"),
        502: (ErrorCode.AUDIO_FETCH_FAILED, "Upstream fetch failed"),
        503: (ErrorCode.MODEL_UNAVAILABLE, "Service unavailable"),
        504: (ErrorCode.ASSESSMENT_TIMEOUT, "Request timed out"),
    }
    return mapping.get(status_code, (ErrorCode.INTERNAL_ERROR, "Internal server error"))


def create_app(
    *,
    settings: Settings | None = None,
    engine: PronunciationEngine | None = None,
    downloader: AudioDownloader | None = None,
) -> FastAPI:
    """Create and wire the OpenPronounce FastAPI application."""
    cfg = settings or get_settings()
    cfg.apply_runtime_env()
    configure_logging(cfg.log_level)

    selected_engine = engine or OpenPronounceAdapter(
        hf_home=cfg.hf_home,
        hf_hub_offline=cfg.hf_hub_offline,
    )
    service = PronunciationService(
        settings=cfg,
        engine=selected_engine,
        downloader=downloader,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        warmup_task: asyncio.Task[None] | None = None

        async def _warm_engine() -> None:
            try:
                await asyncio.to_thread(selected_engine.load)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Background model initialization failed; readiness remains false")

        if cfg.preload_models and not selected_engine.is_ready:
            warmup_task = asyncio.create_task(_warm_engine(), name="openpronounce-warmup")
            app.state.warmup_task = warmup_task
        try:
            yield
        finally:
            if warmup_task is not None and not warmup_task.done():
                warmup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await warmup_task
            service.shutdown()

    app = FastAPI(
        title="OpenPronounce API",
        version=cfg.app_version,
        description=(
            "CPU-only pronunciation assessment service for English words, phrases, and sentences "
            "wrapping OpenPronounce 1.1.0."
        ),
        docs_url="/docs" if cfg.enable_docs else None,
        redoc_url="/redoc" if cfg.enable_docs else None,
        openapi_url="/openapi.json" if cfg.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.pronunciation_service = service

    if cfg.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_allow_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = sanitize_request_id(request.headers.get("X-Request-ID"))
        token = set_request_id(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "HTTP request processed",
                extra={
                    "method": request.method,
                    "endpoint": request.url.path,
                    "status_code": status_code,
                    "latency_ms": elapsed_ms,
                },
            )
            reset_request_id(token)

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return _error_json(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request_id=get_request_id(),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Sanitize validation feedback: never echo raw input values (e.g. signed URLs or secrets).
        fields = [
            ".".join(str(part) for part in err.get("loc", ()) if part != "body")
            for err in exc.errors()
        ]
        safe_fields = ", ".join(f for f in fields if f) or "request"
        return _error_json(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid request payload ({safe_fields})",
            request_id=get_request_id(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        code, message = _map_http_status_to_code(exc.status_code)
        return _error_json(
            status_code=exc.status_code,
            code=code,
            message=message,
            request_id=get_request_id(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server exception")
        return _error_json(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Internal server error",
            request_id=get_request_id(),
        )

    app.include_router(health_router)
    app.include_router(assess_router)
    return app


app = create_app()
