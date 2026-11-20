"""Unit tests for audio validation, normalization, temp cleanup, and text validation."""

from __future__ import annotations

import os
import stat
import wave
from pathlib import Path

import pytest

from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.schemas.requests import AssessmentMode, ResolvedAssessmentMode
from openpronounce_api.services.audio import (
    normalize_audio_file,
    safe_unlink,
    save_upload_to_temp,
)
from openpronounce_api.services.pronunciation import (
    normalize_language,
    resolve_mode,
    validate_and_normalize_text,
)
from tests.conftest import FIXTURES_DIR, make_wav_bytes, make_webm_bytes


class _ChunkedUpload:
    def __init__(self, payload: bytes, chunk_size: int = 16384) -> None:
        self._payload = payload
        self._offset = 0
        self._chunk_size = chunk_size

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        step = self._chunk_size if size < 0 else min(size, self._chunk_size)
        chunk = self._payload[self._offset : self._offset + step]
        self._offset += len(chunk)
        return chunk


async def test_save_upload_enforces_streaming_size_limit_and_cleans_up(
    isolated_temp_dir: Path,
) -> None:
    oversized = b"x" * 4096
    with pytest.raises(AppError) as exc_info:
        await save_upload_to_temp(
            _ChunkedUpload(oversized, chunk_size=512),
            max_bytes=1024,
            temp_dir=str(isolated_temp_dir),
        )
    assert exc_info.value.status_code == 413
    assert exc_info.value.code == ErrorCode.AUDIO_TOO_LARGE
    assert list(isolated_temp_dir.iterdir()) == []


async def test_save_upload_rejects_empty_and_sets_0600_permissions(
    isolated_temp_dir: Path,
) -> None:
    with pytest.raises(AppError) as exc_info:
        await save_upload_to_temp(
            _ChunkedUpload(b""),
            max_bytes=4096,
            temp_dir=str(isolated_temp_dir),
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == ErrorCode.AUDIO_UNPROCESSABLE

    path, size = await save_upload_to_temp(
        _ChunkedUpload(b"abcd"),
        max_bytes=4096,
        temp_dir=str(isolated_temp_dir),
    )
    try:
        assert size == 4
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
    finally:
        safe_unlink(path)
    assert list(isolated_temp_dir.iterdir()) == []


async def test_normalize_wav_and_webm_to_16khz_mono(
    base_settings: Settings,
    isolated_temp_dir: Path,
    tmp_path: Path,
) -> None:
    stereo_44k = make_wav_bytes(duration_seconds=1.1, sample_rate=44100, channels=2)
    raw_wav = isolated_temp_dir / "input.wav"
    raw_wav.write_bytes(stereo_44k)

    normalized = await normalize_audio_file(
        str(raw_wav),
        input_bytes=len(stereo_44k),
        settings=base_settings,
    )
    try:
        with wave.open(normalized.wav_path, "rb") as wf:
            assert wf.getframerate() == 16000
            assert wf.getnchannels() == 1
        assert 1.0 <= normalized.duration_seconds <= 1.2
    finally:
        normalized.cleanup()
    assert not Path(normalized.wav_path).exists()

    webm_bytes = make_webm_bytes(tmp_path, duration_seconds=1.0)
    raw_webm = isolated_temp_dir / "input.webm"
    raw_webm.write_bytes(webm_bytes)
    normalized_webm = await normalize_audio_file(
        str(raw_webm),
        input_bytes=len(webm_bytes),
        settings=base_settings,
    )
    try:
        with wave.open(normalized_webm.wav_path, "rb") as wf:
            assert wf.getframerate() == 16000
            assert wf.getnchannels() == 1
        assert normalized_webm.duration_seconds >= 0.8
    finally:
        normalized_webm.cleanup()


async def test_normalize_rejects_invalid_and_out_of_range_durations(
    base_settings: Settings,
    isolated_temp_dir: Path,
) -> None:
    invalid_path = FIXTURES_DIR / "invalid_audio.bin"
    with pytest.raises(AppError) as exc_info:
        await normalize_audio_file(
            str(invalid_path),
            input_bytes=invalid_path.stat().st_size,
            settings=base_settings,
        )
    assert exc_info.value.status_code == 415
    assert exc_info.value.code == ErrorCode.AUDIO_UNSUPPORTED_FORMAT

    short_wav = isolated_temp_dir / "short.wav"
    short_bytes = make_wav_bytes(duration_seconds=0.1)
    short_wav.write_bytes(short_bytes)
    with pytest.raises(AppError) as exc_short:
        await normalize_audio_file(
            str(short_wav),
            input_bytes=len(short_bytes),
            settings=base_settings,
        )
    assert exc_short.value.status_code == 422
    assert exc_short.value.code == ErrorCode.AUDIO_UNPROCESSABLE

    strict_settings = base_settings.model_copy(update={"max_audio_seconds": 1.0})
    long_wav = isolated_temp_dir / "long.wav"
    long_bytes = make_wav_bytes(duration_seconds=1.8)
    long_wav.write_bytes(long_bytes)
    with pytest.raises(AppError) as exc_long:
        await normalize_audio_file(
            str(long_wav),
            input_bytes=len(long_bytes),
            settings=strict_settings,
        )
    assert exc_long.value.status_code == 422
    assert exc_long.value.code == ErrorCode.AUDIO_UNPROCESSABLE

    # Only the input test files we created remain; no leaked normalized .16k.wav files.
    leaked = [p for p in isolated_temp_dir.iterdir() if p.name.endswith(".16k.wav")]
    assert leaked == []


def test_text_language_and_mode_validation(base_settings: Settings) -> None:
    clean, count = validate_and_normalize_text("  I   like apples. ", base_settings)
    assert clean == "I like apples."
    assert count == 3

    for bad_text in ["", "   ", "12345 !!!", "hello\x00world", "a " * 90, "a" * 501]:
        with pytest.raises(AppError) as exc_info:
            validate_and_normalize_text(bad_text, base_settings)
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == ErrorCode.INVALID_REQUEST

    assert normalize_language("en") == "en"
    assert normalize_language("en-US") == "en"
    assert normalize_language("EN-us") == "en"
    with pytest.raises(AppError) as exc_lang:
        normalize_language("fr")
    assert exc_lang.value.status_code == 400

    assert (
        resolve_mode(AssessmentMode.AUTO, text="apple", word_count=1) == ResolvedAssessmentMode.WORD
    )
    assert (
        resolve_mode(AssessmentMode.AUTO, text="good morning", word_count=2)
        == ResolvedAssessmentMode.PHRASE
    )
    assert (
        resolve_mode(AssessmentMode.AUTO, text="I like eating apples.", word_count=4)
        == ResolvedAssessmentMode.SENTENCE
    )
    assert (
        resolve_mode(AssessmentMode.WORD, text="I like eating apples.", word_count=4)
        == ResolvedAssessmentMode.WORD
    )
