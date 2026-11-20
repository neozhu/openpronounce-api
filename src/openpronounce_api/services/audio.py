"""Audio streaming ingestion, container probing, 16 kHz mono normalization, and temp cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import wave
from dataclasses import dataclass
from typing import Protocol

import anyio

from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode

ALLOWED_FORMAT_TOKENS: frozenset[str] = frozenset({"wav", "mp3", "flac", "ogg", "webm"})
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH_BYTES = 2  # 16-bit signed PCM
_STREAM_CHUNK_SIZE = 64 * 1024


class AsyncReadableUpload(Protocol):
    """Minimal protocol satisfied by FastAPI UploadFile."""

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(slots=True)
class NormalizedAudio:
    """Normalized 16 kHz mono WAV temporary artifact with deterministic cleanup."""

    wav_path: str
    duration_seconds: float
    input_bytes: int
    decoded_bytes: int

    def cleanup(self) -> None:
        safe_unlink(self.wav_path)


def safe_unlink(path: str | None) -> None:
    """Best-effort deletion of a temporary file."""
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        return


def create_temp_file(*, suffix: str = ".bin", temp_dir: str | None = None) -> str:
    """Create a unique temporary file with strict owner-only (0600) permissions."""
    fd, path = tempfile.mkstemp(prefix="op_", suffix=suffix, dir=temp_dir)
    try:
        os.chmod(path, 0o600)
    finally:
        os.close(fd)
    return path


async def save_upload_to_temp(
    upload: AsyncReadableUpload,
    *,
    max_bytes: int,
    temp_dir: str | None = None,
) -> tuple[str, int]:
    """Stream an uploaded file to a strict-permission temp file while enforcing max_bytes."""
    raw_path = create_temp_file(suffix=".upload", temp_dir=temp_dir)
    total_bytes = 0
    try:
        async with await anyio.open_file(raw_path, "wb") as handle:
            while True:
                chunk = await upload.read(_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise AppError(
                        status_code=413,
                        code=ErrorCode.AUDIO_TOO_LARGE,
                        message="Audio payload exceeds maximum allowed size",
                    )
                await handle.write(chunk)
    except BaseException:
        safe_unlink(raw_path)
        raise

    if total_bytes == 0:
        safe_unlink(raw_path)
        raise AppError(
            status_code=422,
            code=ErrorCode.AUDIO_UNPROCESSABLE,
            message="Audio file is empty",
        )

    return raw_path, total_bytes


async def _run_bounded_subprocess(
    cmd: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int, bytes, bytes]:
    """Execute a subprocess with hard timeout and guaranteed termination."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise AppError(
            status_code=504,
            code=ErrorCode.ASSESSMENT_TIMEOUT,
            message="Audio processing timed out",
        ) from exc
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stdout, stderr


async def probe_audio_container(raw_path: str, *, timeout_seconds: float) -> str:
    """Verify the actual file container via ffprobe rather than trusting MIME or extension."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise AppError(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Audio probing utility is unavailable",
        )

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        raw_path,
    ]
    returncode, stdout, _stderr = await _run_bounded_subprocess(
        cmd,
        timeout_seconds=timeout_seconds,
    )
    if returncode != 0 or not stdout:
        raise AppError(
            status_code=415,
            code=ErrorCode.AUDIO_UNSUPPORTED_FORMAT,
            message="Unsupported or invalid audio format",
        )

    try:
        metadata = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AppError(
            status_code=415,
            code=ErrorCode.AUDIO_UNSUPPORTED_FORMAT,
            message="Unsupported or invalid audio format",
        ) from exc

    format_info = metadata.get("format") or {}
    format_name = str(format_info.get("format_name") or "").lower()
    format_tokens = {token.strip() for token in format_name.split(",") if token.strip()}
    if not (format_tokens & ALLOWED_FORMAT_TOKENS):
        raise AppError(
            status_code=415,
            code=ErrorCode.AUDIO_UNSUPPORTED_FORMAT,
            message="Unsupported or invalid audio format",
        )

    streams = metadata.get("streams") or []
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise AppError(
            status_code=415,
            code=ErrorCode.AUDIO_UNSUPPORTED_FORMAT,
            message="File does not contain an audio stream",
        )

    # Reject containers with non-attached video streams (e.g. video files disguised as webm)
    for stream in streams:
        if stream.get("codec_type") == "video":
            disposition = stream.get("disposition") or {}
            if not disposition.get("attached_pic"):
                raise AppError(
                    status_code=415,
                    code=ErrorCode.AUDIO_UNSUPPORTED_FORMAT,
                    message="Video streams are not supported; supply audio only",
                )

    return format_name


def _read_wav_duration_seconds(wav_path: str) -> float:
    try:
        with wave.open(wav_path, "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            channels = wav_file.getnchannels()
    except (wave.Error, OSError) as exc:
        raise AppError(
            status_code=422,
            code=ErrorCode.AUDIO_UNPROCESSABLE,
            message="Decoded audio waveform is unreadable",
        ) from exc

    if frame_rate != TARGET_SAMPLE_RATE or channels != TARGET_CHANNELS or frame_count <= 0:
        raise AppError(
            status_code=422,
            code=ErrorCode.AUDIO_UNPROCESSABLE,
            message="Decoded audio contains no valid samples",
        )
    return frame_count / float(frame_rate)


async def normalize_audio_file(
    raw_path: str,
    *,
    input_bytes: int,
    settings: Settings,
) -> NormalizedAudio:
    """Probe, decode to bounded 16 kHz mono WAV, and validate duration."""
    await probe_audio_container(raw_path, timeout_seconds=settings.ffmpeg_timeout_seconds)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AppError(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Audio decoding utility is unavailable",
        )

    wav_path = create_temp_file(suffix=".16k.wav", temp_dir=settings.temp_dir)
    # Decode slightly beyond max_audio_seconds so oversized files are capped in RAM/disk
    # and still reliably detected as exceeding max_audio_seconds.
    decode_cap_seconds = settings.max_audio_seconds + 0.5
    max_decoded_bytes = (
        int(
            (settings.max_audio_seconds + 1.0)
            * TARGET_SAMPLE_RATE
            * TARGET_CHANNELS
            * TARGET_SAMPLE_WIDTH_BYTES
        )
        + 8192
    )

    try:
        cmd = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-threads",
            "1",
            "-i",
            raw_path,
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-t",
            f"{decode_cap_seconds:.3f}",
            "-f",
            "wav",
            wav_path,
        ]
        returncode, _stdout, _stderr = await _run_bounded_subprocess(
            cmd,
            timeout_seconds=settings.ffmpeg_timeout_seconds,
        )
        if returncode != 0:
            raise AppError(
                status_code=422,
                code=ErrorCode.AUDIO_UNPROCESSABLE,
                message="Audio content could not be decoded",
            )

        decoded_bytes = (await anyio.Path(wav_path).stat()).st_size
        if decoded_bytes <= 44:
            raise AppError(
                status_code=422,
                code=ErrorCode.AUDIO_UNPROCESSABLE,
                message="Audio content decoded to an empty waveform",
            )
        if decoded_bytes > max_decoded_bytes:
            raise AppError(
                status_code=422,
                code=ErrorCode.AUDIO_UNPROCESSABLE,
                message="Decoded audio exceeds maximum permitted output size",
            )

        duration = _read_wav_duration_seconds(wav_path)
        if duration < settings.min_audio_seconds:
            raise AppError(
                status_code=422,
                code=ErrorCode.AUDIO_UNPROCESSABLE,
                message=(
                    f"Audio duration is below the minimum of {settings.min_audio_seconds:.1f}s"
                ),
            )
        if duration > settings.max_audio_seconds:
            raise AppError(
                status_code=422,
                code=ErrorCode.AUDIO_UNPROCESSABLE,
                message=(
                    f"Audio duration exceeds the maximum of {settings.max_audio_seconds:.1f}s"
                ),
            )

        return NormalizedAudio(
            wav_path=wav_path,
            duration_seconds=round(duration, 3),
            input_bytes=input_bytes,
            decoded_bytes=decoded_bytes,
        )
    except BaseException:
        safe_unlink(wav_path)
        raise
