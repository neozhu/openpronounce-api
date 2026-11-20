"""API tests for /health/live, /health/ready, /version, and OpenAPI exposure controls."""

from __future__ import annotations

import httpx

from openpronounce_api.core.config import Settings
from openpronounce_api.main import create_app
from tests.conftest import FakeEngine


async def test_health_live_never_triggers_model_load(base_settings: Settings) -> None:
    engine = FakeEngine(ready=False)
    app = create_app(settings=base_settings, engine=engine)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert "X-Request-ID" in resp.headers
        assert engine.load_calls == 0

        ready_resp = await client.get("/health/ready")
        assert ready_resp.status_code == 503
        body = ready_resp.json()
        assert body["error"]["code"] == "MODEL_UNAVAILABLE"
        assert "request_id" in body


async def test_health_ready_and_version_when_engine_ready(base_settings: Settings) -> None:
    engine = FakeEngine(ready=True)
    app = create_app(settings=base_settings, engine=engine)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ready_resp = await client.get("/health/ready")
        assert ready_resp.status_code == 200
        assert ready_resp.json() == {
            "status": "ready",
            "engine": {"name": "openpronounce", "version": "1.1.0"},
        }

        version_resp = await client.get("/version", headers={"X-Request-ID": "probe-123"})
        assert version_resp.status_code == 200
        assert version_resp.headers["X-Request-ID"] == "probe-123"
        payload = version_resp.json()
        assert payload["app_name"] == "openpronounce-api"
        assert payload["device"] == "cpu"
        assert payload["engine"] == {"name": "openpronounce", "version": "1.1.0"}
        # Must not leak API key or environment secrets
        assert "test-secret-api-key" not in version_resp.text


async def test_openapi_docs_can_be_disabled(base_settings: Settings) -> None:
    settings = base_settings.model_copy(update={"enable_docs": False})
    app = create_app(settings=settings, engine=FakeEngine(ready=True))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        docs_resp = await client.get("/docs")
        assert docs_resp.status_code == 404
        assert docs_resp.json()["error"]["code"] == "INVALID_REQUEST"
