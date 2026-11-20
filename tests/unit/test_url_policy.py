"""Unit tests for SSRF URL policy, host allowlist matching, IP validation, and redaction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.core.logging import redact_url
from openpronounce_api.core.security import (
    host_matches_allowlist,
    is_public_ip,
    resolve_and_validate_ips,
    validate_audio_url,
)


def test_settings_rejects_wildcard_all_hosts() -> None:
    for bad in ("*", "*.", ".*", "*.*", "*.com", "media.*.example.org"):
        with pytest.raises(ValidationError):
            Settings(ALLOWED_AUDIO_HOSTS=bad, PRELOAD_MODELS=False)


def test_url_fetch_disabled_by_default() -> None:
    settings = Settings(PRELOAD_MODELS=False, ALLOWED_AUDIO_HOSTS="media.example.org")
    assert settings.url_fetch_enabled is False
    with pytest.raises(AppError) as exc_info:
        validate_audio_url("https://media.example.org/a.wav", settings)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == ErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://media.example.org/clip.wav",
        "https://127.0.0.1/clip.wav",
        "https://localhost/clip.wav",
        "https://sub.localhost/clip.wav",
        "https://10.0.0.1/clip.wav",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/clip.wav",
        "https://[::ffff:127.0.0.1]/clip.wav",
        "https://2130706433/clip.wav",
        "https://0177.0.0.1/clip.wav",
        "https://good.example.evil.org/clip.wav",
        "https://trusted-storage.example.com/clip.wav",
        "https://eviltrusted-storage.example.com/clip.wav",
        "https://user:pass@media.example.org/clip.wav",
        "https://media.example.org:8443/clip.wav",
        "https://media.example.org/clip.wav#fragment",
        "https://media.example.org/clip%0d%0a.wav",
        "https://media.example.org\\clip.wav",
        "https://media.example.org/clip.wav ",
        "file:///etc/passwd",
    ],
)
def test_validate_audio_url_rejects_disallowed_targets(
    base_settings: Settings,
    bad_url: str,
) -> None:
    with pytest.raises(AppError) as exc_info:
        validate_audio_url(bad_url, base_settings)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == ErrorCode.INVALID_REQUEST


def test_validate_audio_url_accepts_allowlisted_hosts_and_redacts_query(
    base_settings: Settings,
) -> None:
    url = "https://bucket1.trusted-storage.example.com/clips/audio.webm?X-Amz-Signature=SECRET999"
    validated = validate_audio_url(url, base_settings)
    assert validated.hostname == "bucket1.trusted-storage.example.com"
    assert validated.port == 443
    assert validated.query == "X-Amz-Signature=SECRET999"
    assert "SECRET999" not in validated.redacted_url
    assert validated.redacted_url == "https://bucket1.trusted-storage.example.com/clips/audio.webm"


def test_host_matches_allowlist_dot_boundaries() -> None:
    allowlist = ("media.example.org", "*.trusted-storage.example.com")
    assert host_matches_allowlist("media.example.org", allowlist) is True
    assert host_matches_allowlist("a.trusted-storage.example.com", allowlist) is True
    assert host_matches_allowlist("a.b.trusted-storage.example.com", allowlist) is True
    assert host_matches_allowlist("trusted-storage.example.com", allowlist) is False
    assert host_matches_allowlist("media.example.org.evil.com", allowlist) is False
    assert host_matches_allowlist("evilmedia.example.org", allowlist) is False


@pytest.mark.parametrize(
    ("ip_str", "expected"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("172.16.5.10", False),
        ("192.168.1.10", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("::", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("::ffff:169.254.169.254", False),
        ("64:ff9b::127.0.0.1", False),
    ],
)
def test_is_public_ip(ip_str: str, expected: bool) -> None:
    assert is_public_ip(ip_str) is expected


async def test_resolve_and_validate_ips_fails_closed_on_private_or_mixed_answers() -> None:
    async def private_resolver(_host: str, _port: int) -> list[str]:
        return ["10.0.0.5"]

    with pytest.raises(AppError) as exc_private:
        await resolve_and_validate_ips("media.example.org", 443, resolver=private_resolver)
    assert exc_private.value.status_code == 400

    async def mixed_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34", "169.254.169.254"]

    with pytest.raises(AppError) as exc_mixed:
        await resolve_and_validate_ips("media.example.org", 443, resolver=mixed_resolver)
    assert exc_mixed.value.status_code == 400

    async def public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    ips = await resolve_and_validate_ips("media.example.org", 443, resolver=public_resolver)
    assert ips == ["93.184.216.34"]


def test_redact_url_removes_credentials_query_and_fragment() -> None:
    redacted = redact_url("https://user:secret@media.example.org/p/a.wav?sig=TOPSECRET#frag")
    assert redacted == "https://media.example.org/p/a.wav"
    assert "TOPSECRET" not in redacted
    assert "secret" not in redacted
