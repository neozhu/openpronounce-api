"""Pronunciation assessment orchestration, validation, and CPU concurrency guard."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from openpronounce_api.adapters.openpronounce_adapter import PronunciationEngine
from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.schemas.requests import AssessmentMode, ResolvedAssessmentMode
from openpronounce_api.schemas.responses import AssessResponse
from openpronounce_api.services.audio import (
    AsyncReadableUpload,
    NormalizedAudio,
    normalize_audio_file,
    safe_unlink,
    save_upload_to_temp,
)
from openpronounce_api.services.audio_downloader import AudioDownloader
from openpronounce_api.services.result_mapper import map_upstream_result

logger = logging.getLogger(__name__)

_WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")
_ENGLISH_LETTER_RE = re.compile(r"[A-Za-z]")
_SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "en",
    "en-us": "en",
}


def validate_and_normalize_text(raw_text: str, settings: Settings) -> tuple[str, int]:
    """Validate expected English reading text and return (normalized_text, word_count)."""
    if raw_text is None:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Expected text is required",
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_text if ch not in ("\t", "\n", "\r")):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Expected text contains disallowed control characters",
        )
    normalized = " ".join(raw_text.split())
    if not normalized:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Expected text must not be empty",
        )
    if len(normalized) > settings.max_text_chars:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message=f"Expected text exceeds maximum length of {settings.max_text_chars} characters",
        )
    if not _ENGLISH_LETTER_RE.search(normalized):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Expected text must contain English letters",
        )
    words = _WORD_TOKEN_RE.findall(normalized)
    word_count = len(words)
    if word_count < settings.min_text_words or word_count > settings.max_text_words:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message=(
                f"Expected text must contain between {settings.min_text_words} "
                f"and {settings.max_text_words} words"
            ),
        )
    return normalized, word_count


def normalize_language(raw_language: str | None) -> str:
    """Normalize public language parameter (accepting en and en-US alias -> en)."""
    candidate = (raw_language or "en").strip().lower()
    mapped = _SUPPORTED_LANGUAGES.get(candidate)
    if mapped is None:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Unsupported language; only 'en' (or alias 'en-US') is supported in v1",
        )
    return mapped


def resolve_mode(
    requested_mode: AssessmentMode,
    *,
    text: str,
    word_count: int,
) -> ResolvedAssessmentMode:
    """Derive display mode when mode=auto without altering scoring behavior."""
    if requested_mode == AssessmentMode.WORD:
        return ResolvedAssessmentMode.WORD
    if requested_mode == AssessmentMode.PHRASE:
        return ResolvedAssessmentMode.PHRASE
    if requested_mode == AssessmentMode.SENTENCE:
        return ResolvedAssessmentMode.SENTENCE

    if word_count == 1:
        return ResolvedAssessmentMode.WORD
    if word_count <= 5 and not text.endswith((".", "!", "?")):
        return ResolvedAssessmentMode.PHRASE
    return ResolvedAssessmentMode.SENTENCE


class PronunciationService:
    """Orchestrates upload and URL assessment through one shared pipeline."""

    def __init__(
        self,
        *,
        settings: Settings,
        engine: PronunciationEngine,
        downloader: AudioDownloader | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._downloader = downloader or AudioDownloader()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_inferences)
        self._max_in_flight = settings.max_concurrent_inferences + settings.max_queued_inferences
        self._in_flight = 0
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_concurrent_inferences,
            thread_name_prefix="openpronounce-cpu",
        )

    @property
    def engine(self) -> PronunciationEngine:
        return self._engine

    def shutdown(self) -> None:
        """Release worker executor resources during graceful application shutdown."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def assess_upload(
        self,
        *,
        upload: AsyncReadableUpload,
        text: str,
        language: str,
        mode: AssessmentMode,
        request_id: str,
    ) -> AssessResponse:
        """Assess a multipart uploaded audio file."""
        clean_text, word_count = validate_and_normalize_text(text, self._settings)
        normalized_lang = normalize_language(language)
        resolved_mode = resolve_mode(mode, text=clean_text, word_count=word_count)
        self._ensure_engine_ready()

        raw_path, input_bytes = await save_upload_to_temp(
            upload,
            max_bytes=self._settings.max_audio_bytes,
            temp_dir=self._settings.temp_dir,
        )
        try:
            return await self._assess_raw_audio(
                raw_path=raw_path,
                input_bytes=input_bytes,
                text=clean_text,
                language=normalized_lang,
                mode=resolved_mode,
                request_id=request_id,
                source_kind="upload",
            )
        finally:
            safe_unlink(raw_path)

    async def assess_url(
        self,
        *,
        audio_url: str,
        text: str,
        language: str,
        mode: AssessmentMode,
        request_id: str,
    ) -> AssessResponse:
        """Download an allowlisted HTTPS audio resource and run the shared assessment pipeline."""
        clean_text, word_count = validate_and_normalize_text(text, self._settings)
        normalized_lang = normalize_language(language)
        resolved_mode = resolve_mode(mode, text=clean_text, word_count=word_count)
        self._ensure_engine_ready()

        downloaded = await self._downloader.download(audio_url, settings=self._settings)
        try:
            return await self._assess_raw_audio(
                raw_path=downloaded.raw_path,
                input_bytes=downloaded.size_bytes,
                text=clean_text,
                language=normalized_lang,
                mode=resolved_mode,
                request_id=request_id,
                source_kind="url",
            )
        finally:
            safe_unlink(downloaded.raw_path)

    def _ensure_engine_ready(self) -> None:
        if not self._engine.is_ready:
            raise AppError(
                status_code=503,
                code=ErrorCode.MODEL_UNAVAILABLE,
                message="Pronunciation assessment engine is not ready",
            )

    async def _assess_raw_audio(
        self,
        *,
        raw_path: str,
        input_bytes: int,
        text: str,
        language: str,
        mode: ResolvedAssessmentMode,
        request_id: str,
        source_kind: str,
    ) -> AssessResponse:
        started = time.perf_counter()
        normalized: NormalizedAudio | None = None
        try:
            normalized = await normalize_audio_file(
                raw_path,
                input_bytes=input_bytes,
                settings=self._settings,
            )
            raw_result = await self._run_bounded_inference(
                wav_path=normalized.wav_path,
                text=text,
                language=language,
            )
            response = map_upstream_result(
                raw_result,
                request_id=request_id,
                text=text,
                language=language,
                mode=mode,
                decoded_duration_seconds=normalized.duration_seconds,
                engine_name=self._engine.engine_name,
                engine_version=self._engine.engine_version,
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "Assessment completed",
                extra={
                    "source_kind": source_kind,
                    "status": response.status.value,
                    "reliable": response.reliable,
                    "audio_bytes": input_bytes,
                    "audio_duration_seconds": response.audio_quality.duration_seconds,
                    "latency_ms": elapsed_ms,
                    "engine_version": self._engine.engine_version,
                },
            )
            return response
        finally:
            if normalized is not None:
                normalized.cleanup()

    async def _run_bounded_inference(
        self,
        *,
        wav_path: str,
        text: str,
        language: str,
    ) -> dict:
        if self._in_flight >= self._max_in_flight:
            raise AppError(
                status_code=429,
                code=ErrorCode.SERVER_BUSY,
                message="Inference capacity reached; please retry shortly",
                headers={"Retry-After": "1"},
            )

        self._in_flight += 1
        acquired = False
        try:
            if self._settings.max_queued_inferences == 0:
                if self._semaphore.locked():
                    raise AppError(
                        status_code=429,
                        code=ErrorCode.SERVER_BUSY,
                        message="Inference capacity reached; please retry shortly",
                        headers={"Retry-After": "1"},
                    )
                await self._semaphore.acquire()
                acquired = True
            else:
                try:
                    await asyncio.wait_for(
                        self._semaphore.acquire(),
                        timeout=self._settings.queue_wait_timeout_seconds,
                    )
                    acquired = True
                except TimeoutError as exc:
                    raise AppError(
                        status_code=429,
                        code=ErrorCode.SERVER_BUSY,
                        message="Inference capacity reached; please retry shortly",
                        headers={"Retry-After": "1"},
                    ) from exc

            loop = asyncio.get_running_loop()
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(
                        self._executor,
                        self._engine.assess,
                        wav_path,
                        text,
                        language,
                    ),
                    timeout=self._settings.inference_timeout_seconds,
                )
            except TimeoutError as exc:
                raise AppError(
                    status_code=504,
                    code=ErrorCode.ASSESSMENT_TIMEOUT,
                    message="Pronunciation assessment timed out",
                ) from exc
            except AppError:
                raise
            except Exception as exc:
                logger.exception("Pronunciation engine inference failed")
                raise AppError(
                    status_code=500,
                    code=ErrorCode.INTERNAL_ERROR,
                    message="Pronunciation assessment failed",
                ) from exc
        finally:
            if acquired:
                self._semaphore.release()
            self._in_flight -= 1
