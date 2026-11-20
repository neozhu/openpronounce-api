"""Unit tests for API-key auth, request ID sanitization, and adapter contract."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from openpronounce_api.adapters.openpronounce_adapter import OpenPronounceAdapter
from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.core.logging import sanitize_request_id
from openpronounce_api.core.security import verify_api_key
from tests.conftest import TEST_API_KEY


def test_verify_api_key_enforces_secret_when_enabled(base_settings: Settings) -> None:
    verify_api_key(TEST_API_KEY, base_settings)

    for bad_key in (None, "", "wrong-key", TEST_API_KEY + "x"):
        with pytest.raises(AppError) as exc_info:
            verify_api_key(bad_key, base_settings)
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_verify_api_key_fails_closed_if_server_key_unset() -> None:
    settings = Settings(PRELOAD_MODELS=False, REQUIRE_API_KEY=True, OPENPRONOUNCE_API_KEY=None)
    with pytest.raises(AppError) as exc_info:
        verify_api_key("anything", settings)
    assert exc_info.value.status_code == 401


def test_verify_api_key_allows_when_disabled() -> None:
    settings = Settings(PRELOAD_MODELS=False, REQUIRE_API_KEY=False)
    verify_api_key(None, settings)


def test_settings_enforces_cpu_only_and_safe_cors() -> None:
    with pytest.raises(ValidationError):
        Settings(PRELOAD_MODELS=False, OPENPRONOUNCE_DEVICE="cuda")

    with pytest.raises(ValidationError):
        Settings(
            PRELOAD_MODELS=False,
            REQUIRE_API_KEY=True,
            OPENPRONOUNCE_API_KEY=SecretStr(TEST_API_KEY),
            CORS_ALLOW_ORIGINS="*",
        )


def test_sanitize_request_id() -> None:
    assert sanitize_request_id("req-123_abc.01") == "req-123_abc.01"
    generated = sanitize_request_id("bad\r\nheader-injection")
    assert generated != "bad\r\nheader-injection"
    assert len(generated) == 26


def test_openpronounce_adapter_calls_locked_v1_flags() -> None:
    adapter = OpenPronounceAdapter()
    fake_waveform = [0.0, 0.1, -0.1]
    mock_load_audio = MagicMock(return_value=fake_waveform)
    mock_assess = MagicMock(return_value={"reliable": True, "score": 80.0})
    adapter._ready = True
    adapter._load_audio_fn = mock_load_audio
    adapter._assess_fn = mock_assess

    result = adapter.assess("/tmp/sample.wav", "I like apples.", lang="en")
    assert result["reliable"] is True
    mock_load_audio.assert_called_once_with("/tmp/sample.wav")
    mock_assess.assert_called_once_with(
        fake_waveform,
        "I like apples.",
        lang="en",
        acoustic=False,
        prosody=False,
        transcription=True,
    )
