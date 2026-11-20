"""Shared pytest fixtures, audio generators, and fake engine/transport test doubles."""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from openpronounce_api.core.config import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_API_KEY = "test-secret-api-key-12345"


def make_wav_bytes(
    *,
    duration_seconds: float = 1.2,
    sample_rate: int = 16000,
    channels: int = 1,
    frequency_hz: float = 440.0,
    amplitude: float = 0.25,
) -> bytes:
    """Generate a valid PCM WAV byte buffer in memory."""
    import io

    n_frames = int(duration_seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            sample = int(
                amplitude * 32767.0 * math.sin(2.0 * math.pi * frequency_hz * i / sample_rate)
            )
            packed = struct.pack("<h", sample)
            frames.extend(packed * channels)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def make_webm_bytes(tmp_path: Path, *, duration_seconds: float = 1.2) -> bytes:
    """Encode a valid WebM/Opus audio clip using ffmpeg for decoder tests."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for WebM audio tests")
    wav_file = tmp_path / "src_for_webm.wav"
    webm_file = tmp_path / "sample.webm"
    wav_file.write_bytes(make_wav_bytes(duration_seconds=duration_seconds))
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_file),
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            str(webm_file),
        ],
        check=True,
    )
    return webm_file.read_bytes()


def sample_upstream_reliable_result(duration: float = 1.2) -> dict[str, Any]:
    """Return a realistic OpenPronounce 1.1.0 read-speech dictionary for reliable speech."""
    return {
        "score": 82.1,
        "scores": {
            "total": 82.1,
            "accuracy": 78.0,
            "fluency": 88.0,
            "prosodic": 81.0,
        },
        "formula_score": 80.5,
        "distance": None,
        "acoustic_distance": None,
        "transcribe": "I LIKE EATING APPLES",
        "language": "en",
        "reliable": True,
        "reasons": [],
        "audio_quality": {
            "duration": duration,
            "level_db": -18.4,
            "clipping_ratio": 0.0,
            "snr_db": 28.5,
            "issues": [],
        },
        "differences": {
            "transcribe": "I LIKE EATING APPLES",
            "word_error_rate": 0.0,
            "phoneme_error_rate": 0.08,
            "errors": [
                {
                    "position": 3,
                    "word": "apples",
                    "expected": "æpəlz",
                    "actual": "æpəls",
                    "actual_word": "",
                    "phone_distance": 1,
                    "confidence": 0.79,
                    "phones": [
                        {
                            "expected": "z",
                            "heard": "s",
                            "confidence": 0.65,
                            "start": 0.95,
                            "end": 1.12,
                        }
                    ],
                }
            ],
            "words_with_errors": ["apples"],
            "words": [
                {
                    "position": 0,
                    "word": "I",
                    "expected": ["aɪ"],
                    "actual": ["aɪ"],
                    "start": 0.05,
                    "end": 0.2,
                    "confidence": 0.08,
                    "mispronounced": False,
                    "phones": [
                        {
                            "expected": "aɪ",
                            "heard": "aɪ",
                            "confidence": 0.08,
                            "start": 0.05,
                            "end": 0.2,
                        }
                    ],
                },
                {
                    "position": 3,
                    "word": "apples",
                    "expected": ["æ", "p", "ə", "l", "z"],
                    "actual": ["æ", "p", "ə", "l", "s"],
                    "start": 0.72,
                    "end": 1.12,
                    "confidence": 0.79,
                    "mispronounced": True,
                    "phones": [
                        {
                            "expected": "z",
                            "heard": "s",
                            "confidence": 0.65,
                            "start": 0.95,
                            "end": 1.12,
                        }
                    ],
                },
            ],
            "extra": [],
            "alignment": {
                "log_likelihood_per_phone": -0.45,
                "plausible_phone_ratio": 0.92,
                "sentence_probability": 0.96,
                "reliable": True,
            },
        },
    }


def sample_upstream_unreliable_result(duration: float = 1.2) -> dict[str, Any]:
    """Return a realistic OpenPronounce 1.1.0 dictionary for unreliable speech."""
    return {
        "score": 36.2,
        "scores": {
            "total": 36.2,
            "accuracy": 36.6,
            "fluency": 55.0,
            "prosodic": 45.9,
        },
        "formula_score": 30.0,
        "distance": None,
        "acoustic_distance": None,
        "transcribe": "HELLO I AM A DEVELOPER",
        "language": "en",
        "reliable": False,
        "reasons": ["not said: good", "recording noisy"],
        "audio_quality": {
            "duration": duration,
            "level_db": -43.1,
            "clipping_ratio": 0.0,
            "snr_db": 6.2,
            "issues": ["noisy"],
        },
        "differences": {
            "transcribe": "HELLO I AM A DEVELOPER",
            "errors": [],
            "words": [
                {
                    "position": 4,
                    "word": "good",
                    "expected": ["ɡ", "ʊ", "d"],
                    "actual": [],
                    "start": 0.5,
                    "end": 0.6,
                    "confidence": 0.879,
                    "mispronounced": True,
                    "missing": True,
                    "phones": [],
                }
            ],
            "extra": [
                {
                    "after": 4,
                    "before": 5,
                    "start": 0.62,
                    "end": 0.9,
                    "heard": ["d", "ɪ", "v"],
                }
            ],
            "alignment": {
                "sentence_probability": 0.078,
                "reliable": False,
            },
        },
    }


class FakeEngine:
    """Fast deterministic test double implementing the PronunciationEngine protocol."""

    engine_name = "openpronounce"
    engine_version = "1.1.0"

    def __init__(
        self,
        *,
        ready: bool = True,
        result: dict[str, Any] | None = None,
        delay_seconds: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self._ready = ready
        self._result = result
        self._delay_seconds = delay_seconds
        self._error = error
        self.load_calls = 0
        self.assess_calls: list[dict[str, Any]] = []

    @property
    def is_ready(self) -> bool:
        return self._ready

    def load(self) -> None:
        self.load_calls += 1
        self._ready = True

    def assess(self, audio_path: str, text: str, lang: str = "en") -> dict[str, Any]:
        with wave.open(audio_path, "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            nframes = wf.getnframes()
        self.assess_calls.append(
            {
                "audio_path": audio_path,
                "text": text,
                "lang": lang,
                "sample_rate": sr,
                "channels": ch,
                "duration": round(nframes / float(sr), 3),
            }
        )
        if self._delay_seconds > 0:
            time.sleep(self._delay_seconds)
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return sample_upstream_reliable_result(duration=round(nframes / float(sr), 3))


@pytest.fixture
def isolated_temp_dir(tmp_path: Path) -> Path:
    temp_dir = tmp_path / "audio_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


@pytest.fixture
def base_settings(isolated_temp_dir: Path) -> Settings:
    return Settings(
        PRELOAD_MODELS=False,
        REQUIRE_API_KEY=True,
        OPENPRONOUNCE_API_KEY=SecretStr(TEST_API_KEY),
        URL_FETCH_ENABLED=True,
        ALLOWED_AUDIO_HOSTS="media.example.org,*.trusted-storage.example.com",
        temp_dir=str(isolated_temp_dir),
    )


@pytest.fixture
def valid_wav_bytes() -> bytes:
    return make_wav_bytes(duration_seconds=1.2)


@pytest.fixture
def valid_webm_bytes(tmp_path: Path) -> bytes:
    return make_webm_bytes(tmp_path, duration_seconds=1.2)
