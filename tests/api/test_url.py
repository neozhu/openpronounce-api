"""API and security tests for POST /api/v1/assess/url with mocked DNS and pinned transport."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest

from openpronounce_api.core.config import Settings
from openpronounce_api.core.security import ValidatedAudioUrl
from openpronounce_api.main import create_app
from openpronounce_api.services.audio_downloader import AudioDownloader
from tests.conftest import FIXTURES_DIR, TEST_API_KEY, FakeEngine

AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}


def _mock_transport_factory(handler, recorded_pins: list[tuple[str, str]] | None = None):
    def _factory(
        validated: ValidatedAudioUrl,
        pinned_ip: str,
        _settings: Settings,
    ) -> httpx.AsyncBaseTransport:
        if recorded_pins is not None:
            recorded_pins.append((validated.hostname, pinned_ip))
        return httpx.MockTransport(handler)

    return _factory


async def test_url_and_upload_produce_identical_normalized_response(
    base_settings: Settings,
    isolated_temp_dir: Path,
    valid_wav_bytes: bytes,
) -> None:
    recorded_pins: list[tuple[str, str]] = []

    async def public_dns(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav", "Content-Length": str(len(valid_wav_bytes))},
            content=valid_wav_bytes,
        )

    downloader = AudioDownloader(
        dns_resolver=public_dns,
        transport_factory=_mock_transport_factory(handler, recorded_pins),
    )
    engine = FakeEngine(ready=True)
    app = create_app(settings=base_settings, engine=engine, downloader=downloader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        upload_resp = await client.post(
            "/api/v1/assess",
            headers={**AUTH_HEADERS, "X-Request-ID": "same-req-id"},
            files={"audio": ("clip.wav", valid_wav_bytes, "audio/wav")},
            data={"text": "I like eating apples.", "language": "en", "mode": "auto"},
        )
        url_resp = await client.post(
            "/api/v1/assess/url",
            headers={**AUTH_HEADERS, "X-Request-ID": "same-req-id"},
            json={
                "audio_url": "https://media.example.org/recordings/clip.wav?signature=TOPSECRET123",
                "text": "I like eating apples.",
                "language": "en",
                "mode": "auto",
            },
        )

    assert upload_resp.status_code == 200
    assert url_resp.status_code == 200
    assert url_resp.json() == upload_resp.json()
    assert recorded_pins == [("media.example.org", "93.184.216.34")]
    assert "TOPSECRET123" not in url_resp.text
    assert list(isolated_temp_dir.iterdir()) == []


async def test_url_dns_rebinding_uses_validated_pin_and_rejects_rebound_private_ip(
    base_settings: Settings,
    valid_wav_bytes: bytes,
) -> None:
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    recorded_pins: list[tuple[str, str]] = []

    async def rebinding_dns(_host: str, _port: int) -> list[str]:
        return next(answers)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "audio/wav"}, content=valid_wav_bytes)

    downloader = AudioDownloader(
        dns_resolver=rebinding_dns,
        transport_factory=_mock_transport_factory(handler, recorded_pins),
    )
    app = create_app(settings=base_settings, engine=FakeEngine(ready=True), downloader=downloader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # First request resolves public IP once and connects strictly to that pinned IP
        first = await client.post(
            "/api/v1/assess/url",
            headers=AUTH_HEADERS,
            json={"audio_url": "https://media.example.org/a.wav", "text": "apple"},
        )
        assert first.status_code == 200
        assert recorded_pins == [("media.example.org", "93.184.216.34")]

        # Second request resolves rebounded loopback IP and fails closed without connecting
        second = await client.post(
            "/api/v1/assess/url",
            headers=AUTH_HEADERS,
            json={"audio_url": "https://media.example.org/a.wav", "text": "apple"},
        )
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "INVALID_REQUEST"
        assert len(recorded_pins) == 1


@pytest.mark.parametrize(
    "redirect_target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://media.example.org/other.wav",
    ],
)
async def test_url_rejects_redirects(
    base_settings: Settings,
    redirect_target: str,
) -> None:
    async def public_dns(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": redirect_target})

    downloader = AudioDownloader(
        dns_resolver=public_dns,
        transport_factory=_mock_transport_factory(handler),
    )
    app = create_app(settings=base_settings, engine=FakeEngine(ready=True), downloader=downloader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/api/v1/assess/url",
            headers=AUTH_HEADERS,
            json={"audio_url": "https://media.example.org/start.wav", "text": "apple"},
        )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "AUDIO_FETCH_FAILED"
        assert "169.254.169.254" not in resp.text


async def test_url_rejects_oversized_chunked_stream_and_cleans_temp(
    base_settings: Settings,
    isolated_temp_dir: Path,
) -> None:
    settings = base_settings.model_copy(update={"max_audio_bytes": 2048})

    async def public_dns(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    class _OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(10):
                yield b"A" * 512

    def handler(_request: httpx.Request) -> httpx.Response:
        # Intentionally omit Content-Length to test streaming enforcement
        return httpx.Response(200, headers={"Content-Type": "audio/wav"}, stream=_OversizedStream())

    downloader = AudioDownloader(
        dns_resolver=public_dns,
        transport_factory=_mock_transport_factory(handler),
    )
    app = create_app(settings=settings, engine=FakeEngine(ready=True), downloader=downloader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/api/v1/assess/url",
            headers=AUTH_HEADERS,
            json={"audio_url": "https://media.example.org/stream.wav", "text": "apple"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "AUDIO_TOO_LARGE"
    assert list(isolated_temp_dir.iterdir()) == []


async def test_url_rejects_endless_slow_stream_and_cleans_temp(
    base_settings: Settings,
    isolated_temp_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = base_settings.model_copy(update={"url_total_timeout_seconds": 0.25})
    secret_sig = "NEVER_LOG_THIS_SIGNATURE_VALUE_987654"

    async def public_dns(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    class _EndlessSlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b"x" * 64
                await asyncio.sleep(0.15)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            stream=_EndlessSlowStream(),
        )

    downloader = AudioDownloader(
        dns_resolver=public_dns,
        transport_factory=_mock_transport_factory(handler),
    )
    app = create_app(settings=settings, engine=FakeEngine(ready=True), downloader=downloader)

    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/assess/url",
                headers=AUTH_HEADERS,
                json={
                    "audio_url": f"https://media.example.org/slow.wav?X-Amz-Signature={secret_sig}",
                    "text": "apple",
                },
            )

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "ASSESSMENT_TIMEOUT"
    assert secret_sig not in resp.text
    for record in caplog.records:
        assert secret_sig not in record.getMessage()
        assert secret_sig not in str(record.__dict__)
    assert list(isolated_temp_dir.iterdir()) == []


async def test_url_rejects_invalid_mime_and_wrong_decoded_format(
    base_settings: Settings,
    isolated_temp_dir: Path,
) -> None:
    async def public_dns(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    # Case 1: invalid MIME (text/html)
    html_downloader = AudioDownloader(
        dns_resolver=public_dns,
        transport_factory=_mock_transport_factory(
            lambda _req: httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<html>not audio</html>",
            )
        ),
    )
    app_html = create_app(
        settings=base_settings,
        engine=FakeEngine(ready=True),
        downloader=html_downloader,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_html),
        base_url="http://testserver",
    ) as client:
        resp_mime = await client.post(
            "/api/v1/assess/url",
            headers=AUTH_HEADERS,
            json={"audio_url": "https://media.example.org/page.html", "text": "apple"},
        )
        assert resp_mime.status_code == 415
        assert resp_mime.json()["error"]["code"] == "AUDIO_UNSUPPORTED_FORMAT"

    # Case 2: claims audio/wav MIME but delivers invalid binary content
    invalid_bytes = (FIXTURES_DIR / "invalid_audio.bin").read_bytes()
    disguised_downloader = AudioDownloader(
        dns_resolver=public_dns,
        transport_factory=_mock_transport_factory(
            lambda _req: httpx.Response(
                200,
                headers={"Content-Type": "audio/wav"},
                content=invalid_bytes,
            )
        ),
    )
    app_bad_content = create_app(
        settings=base_settings,
        engine=FakeEngine(ready=True),
        downloader=disguised_downloader,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_bad_content),
        base_url="http://testserver",
    ) as client:
        resp_decoded = await client.post(
            "/api/v1/assess/url",
            headers=AUTH_HEADERS,
            json={"audio_url": "https://media.example.org/disguised.wav", "text": "apple"},
        )
        assert resp_decoded.status_code == 415
        assert resp_decoded.json()["error"]["code"] == "AUDIO_UNSUPPORTED_FORMAT"

    assert list(isolated_temp_dir.iterdir()) == []
