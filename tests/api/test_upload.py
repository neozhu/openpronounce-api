"""API tests for POST /api/v1/assess (multipart upload), auth, limits, and concurrency."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from openpronounce_api.core.config import Settings
from openpronounce_api.main import create_app
from tests.conftest import (
    FIXTURES_DIR,
    TEST_API_KEY,
    FakeEngine,
    make_wav_bytes,
    sample_upstream_unreliable_result,
)

AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}


async def test_upload_wav_and_webm_succeed_and_clean_temp_files(
    base_settings: Settings,
    isolated_temp_dir: Path,
    valid_wav_bytes: bytes,
    valid_webm_bytes: bytes,
) -> None:
    engine = FakeEngine(ready=True)
    app = create_app(settings=base_settings, engine=engine)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        wav_resp = await client.post(
            "/api/v1/assess",
            headers={**AUTH_HEADERS, "X-Request-ID": "upload-wav-01"},
            files={"audio": ("sentence.wav", valid_wav_bytes, "audio/wav")},
            data={"text": "I like eating apples.", "language": "en-US", "mode": "auto"},
        )
        assert wav_resp.status_code == 200
        wav_body = wav_resp.json()
        assert wav_body["request_id"] == "upload-wav-01"
        assert wav_body["status"] == "assessed"
        assert wav_body["reliable"] is True
        assert wav_body["language"] == "en"
        assert wav_body["mode"] == "sentence"
        assert wav_body["scores"]["overall"] == 82.1
        assert wav_body["engine"] == {"name": "openpronounce", "version": "1.1.0"}

        webm_resp = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("recording.webm", valid_webm_bytes, "audio/webm")},
            data={"text": "apple", "language": "en", "mode": "auto"},
        )
        assert webm_resp.status_code == 200
        webm_body = webm_resp.json()
        assert webm_body["status"] == "assessed"
        assert webm_body["mode"] == "word"

    assert len(engine.assess_calls) == 2
    assert all(
        call["sample_rate"] == 16000 and call["channels"] == 1 for call in engine.assess_calls
    )
    assert list(isolated_temp_dir.iterdir()) == []


async def test_upload_unreliable_returns_200_uncertain_with_null_scores(
    base_settings: Settings,
    valid_wav_bytes: bytes,
) -> None:
    engine = FakeEngine(ready=True, result=sample_upstream_unreliable_result())
    app = create_app(settings=base_settings, engine=engine)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("quiet.wav", valid_wav_bytes, "audio/wav")},
            data={"text": "Hello, I am a good designer."},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "uncertain"
        assert body["reliable"] is False
        assert body["scores"] is None
        assert "missing_words" in body["reasons"]


async def test_upload_rejects_missing_or_invalid_api_key(
    base_settings: Settings,
    valid_wav_bytes: bytes,
) -> None:
    app = create_app(settings=base_settings, engine=FakeEngine(ready=True))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for headers in ({}, {"X-API-Key": "wrong-key"}):
            resp = await client.post(
                "/api/v1/assess",
                headers=headers,
                files={"audio": ("clip.wav", valid_wav_bytes, "audio/wav")},
                data={"text": "apple"},
            )
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_upload_negative_validation_cases(
    base_settings: Settings,
    isolated_temp_dir: Path,
    valid_wav_bytes: bytes,
) -> None:
    settings = base_settings.model_copy(update={"max_audio_bytes": 4096, "max_audio_seconds": 1.5})
    app = create_app(settings=settings, engine=FakeEngine(ready=True))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Empty text
        resp_empty_text = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("clip.wav", valid_wav_bytes[:1024], "audio/wav")},
            data={"text": "   "},
        )
        assert resp_empty_text.status_code == 400
        assert resp_empty_text.json()["error"]["code"] == "INVALID_REQUEST"

        # 2. Unsupported content (invalid_audio.bin disguised as .wav)
        invalid_bytes = (FIXTURES_DIR / "invalid_audio.bin").read_bytes()
        resp_invalid = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("fake.wav", invalid_bytes, "audio/wav")},
            data={"text": "apple"},
        )
        assert resp_invalid.status_code == 415
        assert resp_invalid.json()["error"]["code"] == "AUDIO_UNSUPPORTED_FORMAT"

        # 3. Corrupted truncated WAV header
        corrupted_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        resp_corrupt = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("corrupt.wav", corrupted_wav, "audio/wav")},
            data={"text": "apple"},
        )
        assert resp_corrupt.status_code in (415, 422)
        assert resp_corrupt.json()["error"]["code"] in (
            "AUDIO_UNSUPPORTED_FORMAT",
            "AUDIO_UNPROCESSABLE",
        )

        # 4. Oversized payload
        resp_large = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("big.wav", b"0" * 5000, "audio/wav")},
            data={"text": "apple"},
        )
        assert resp_large.status_code == 413
        assert resp_large.json()["error"]["code"] == "AUDIO_TOO_LARGE"

    # 5. Excessive duration (allow enough bytes for 2.2s WAV, cap duration to 1.0s)
    dur_settings = base_settings.model_copy(update={"max_audio_seconds": 1.0})
    app_dur = create_app(settings=dur_settings, engine=FakeEngine(ready=True))
    long_wav = make_wav_bytes(duration_seconds=2.2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_dur),
        base_url="http://testserver",
    ) as client:
        resp_long = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("long.wav", long_wav, "audio/wav")},
            data={"text": "I like eating apples."},
        )
        assert resp_long.status_code == 422
        assert resp_long.json()["error"]["code"] == "AUDIO_UNPROCESSABLE"

    assert list(isolated_temp_dir.iterdir()) == []


async def test_upload_inference_failure_and_timeout_cleanup_temp_files(
    base_settings: Settings,
    isolated_temp_dir: Path,
    valid_wav_bytes: bytes,
) -> None:
    failing_engine = FakeEngine(ready=True, error=RuntimeError("model boom"))
    app_fail = create_app(settings=base_settings, engine=failing_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_fail),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("clip.wav", valid_wav_bytes, "audio/wav")},
            data={"text": "apple"},
        )
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "model boom" not in resp.text

    timeout_settings = base_settings.model_copy(update={"inference_timeout_seconds": 1.0})
    slow_engine = FakeEngine(ready=True, delay_seconds=1.4)
    app_slow = create_app(settings=timeout_settings, engine=slow_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_slow),
        base_url="http://testserver",
    ) as client:
        resp_timeout = await client.post(
            "/api/v1/assess",
            headers=AUTH_HEADERS,
            files={"audio": ("clip.wav", valid_wav_bytes, "audio/wav")},
            data={"text": "apple"},
        )
        assert resp_timeout.status_code == 504
        assert resp_timeout.json()["error"]["code"] == "ASSESSMENT_TIMEOUT"

    # Wait briefly for worker thread to finish sleeping before asserting cleanup
    await asyncio.sleep(0.5)
    assert list(isolated_temp_dir.iterdir()) == []


async def test_concurrency_beyond_capacity_returns_429_server_busy(
    base_settings: Settings,
    valid_wav_bytes: bytes,
) -> None:
    settings = base_settings.model_copy(
        update={"max_concurrent_inferences": 1, "max_queued_inferences": 0}
    )
    engine = FakeEngine(ready=True, delay_seconds=0.35)
    app = create_app(settings=settings, engine=engine)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:

        async def _send() -> httpx.Response:
            return await client.post(
                "/api/v1/assess",
                headers=AUTH_HEADERS,
                files={"audio": ("clip.wav", valid_wav_bytes, "audio/wav")},
                data={"text": "apple"},
            )

        r1, r2 = await asyncio.gather(_send(), _send())
        statuses = sorted([r1.status_code, r2.status_code])
        assert statuses == [200, 429]
        busy_resp = r1 if r1.status_code == 429 else r2
        assert busy_resp.json()["error"]["code"] == "SERVER_BUSY"
        assert busy_resp.headers.get("Retry-After") == "1"
