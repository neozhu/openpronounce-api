"""Standardized API error codes and exception types."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable machine-readable error codes returned by the public API."""

    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    AUDIO_TOO_LARGE = "AUDIO_TOO_LARGE"
    AUDIO_UNSUPPORTED_FORMAT = "AUDIO_UNSUPPORTED_FORMAT"
    AUDIO_UNPROCESSABLE = "AUDIO_UNPROCESSABLE"
    SERVER_BUSY = "SERVER_BUSY"
    AUDIO_FETCH_FAILED = "AUDIO_FETCH_FAILED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    ASSESSMENT_TIMEOUT = "ASSESSMENT_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """Domain exception mapped to the standardized error JSON envelope."""

    def __init__(
        self,
        *,
        status_code: int,
        code: ErrorCode,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
