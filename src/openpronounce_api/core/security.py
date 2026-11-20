"""API-key verification and strict HTTPS audio URL / SSRF policy validation."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.core.logging import redact_url

_ENCODED_CONTROL_RE = re.compile(r"%(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7[fF])")
_HOSTNAME_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HEX_OR_INT_LABEL_RE = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)$", re.IGNORECASE)

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL_PREFIX = ipaddress.ip_network("64:ff9b:1::/48")
_DISCARD_ONLY_V6 = ipaddress.ip_network("100::/64")

AsyncDnsResolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class ValidatedAudioUrl:
    """Validated HTTPS audio URL separated from its redacted logging representation."""

    scheme: str
    hostname: str
    port: int
    path: str
    query: str
    raw_url: str
    redacted_url: str


def verify_api_key(provided_key: str | None, settings: Settings) -> None:
    """Validate X-API-Key with constant-time comparison when API key auth is enabled."""
    if not settings.require_api_key:
        return
    expected_secret = settings.openpronounce_api_key
    if expected_secret is None:
        raise AppError(
            status_code=401,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid or missing API key",
        )
    expected = expected_secret.get_secret_value()
    if not expected or not provided_key:
        raise AppError(
            status_code=401,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid or missing API key",
        )
    if not hmac.compare_digest(provided_key.encode("utf-8"), expected.encode("utf-8")):
        raise AppError(
            status_code=401,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid or missing API key",
        )


def host_matches_allowlist(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    """Return True if normalized ASCII hostname matches an exact or dot-boundary wildcard entry."""
    candidate = hostname.lower()
    for pattern in allowed_hosts:
        rule = pattern.lower()
        if rule.startswith("*."):
            base_domain = rule[2:]
            if candidate.endswith("." + base_domain) and len(candidate) > len(base_domain) + 1:
                return True
        elif candidate == rule:
            return True
    return False


def is_public_ip(ip_text: str) -> bool:
    """Return True only for globally routable unicast IPv4/IPv6 addresses."""
    try:
        addr = ipaddress.ip_address(ip_text)
    except ValueError:
        return False

    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return is_public_ip(str(addr.ipv4_mapped))
        if addr.sixtofour is not None and not is_public_ip(str(addr.sixtofour)):
            return False
        if addr.teredo is not None:
            server, client = addr.teredo
            if not is_public_ip(str(server)) or not is_public_ip(str(client)):
                return False
        if (
            addr in _NAT64_WELL_KNOWN_PREFIX
            or addr in _NAT64_LOCAL_PREFIX
            or addr in _DISCARD_ONLY_V6
        ):
            return False
    elif addr in _CGNAT_NETWORK:
        return False

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False

    return bool(addr.is_global)


def _is_ip_literal(hostname: str) -> bool:
    stripped = hostname.strip("[]")
    try:
        ipaddress.ip_address(stripped)
        return True
    except ValueError:
        pass
    # Reject numeric/octal/hex shorthand IPv4 forms such as 2130706433 or 0177.0.0.1
    parts = stripped.split(".")
    return all(_HEX_OR_INT_LABEL_RE.fullmatch(part) for part in parts)


def _normalize_hostname(raw_host: str) -> str:
    if not raw_host:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL must include a valid hostname",
        )
    if raw_host.startswith(".") or raw_host.endswith(".") or ".." in raw_host:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL contains an invalid hostname",
        )
    if _is_ip_literal(raw_host):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="IP literals are not allowed in audio_url",
        )
    try:
        ascii_host = raw_host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL contains a malformed internationalized hostname",
        ) from exc

    if _is_ip_literal(ascii_host):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="IP literals are not allowed in audio_url",
        )

    labels = ascii_host.split(".")
    if len(labels) < 2:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL hostname must be a fully qualified domain name",
        )
    if any(not _HOSTNAME_LABEL_RE.fullmatch(label) for label in labels):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL contains an invalid hostname",
        )
    if ascii_host == "localhost" or ascii_host.endswith((".localhost", ".local", ".internal")):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL host is not permitted",
        )
    return ascii_host


def validate_audio_url(raw_url: str, settings: Settings) -> ValidatedAudioUrl:
    """Validate audio_url syntax, scheme, port, and host allowlist without leaking the URL."""
    if not settings.url_fetch_enabled:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL ingestion is disabled",
        )
    if not settings.allowed_audio_hosts:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL ingestion has no permitted hosts configured",
        )

    if not raw_url or len(raw_url) > 2048:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Invalid audio_url length",
        )

    if raw_url != raw_url.strip():
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL must not contain leading or trailing whitespace",
        )

    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in raw_url):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL contains disallowed control or whitespace characters",
        )

    if "\\" in raw_url or _ENCODED_CONTROL_RE.search(raw_url):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL contains disallowed characters",
        )

    if "#" in raw_url:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL fragments are not permitted",
        )

    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Malformed audio_url",
        ) from exc

    if parsed.scheme.lower() != "https":
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Only HTTPS audio URLs are supported",
        )

    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Userinfo credentials are not permitted in audio_url",
        )

    if not parsed.netloc or parsed.hostname is None:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL must include a hostname",
        )

    try:
        port = parsed.port if parsed.port is not None else 443
    except ValueError as exc:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL contains an invalid port",
        ) from exc

    if port not in settings.allowed_audio_ports:
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL port is not permitted",
        )

    # Extract raw host without port to preserve bracketed IPv6 detection before urlsplit strips []
    host_part = parsed.netloc
    if host_part.startswith("["):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="IP literals are not allowed in audio_url",
        )
    if ":" in host_part:
        host_part = host_part.rsplit(":", 1)[0]

    normalized_host = _normalize_hostname(host_part)

    if not host_matches_allowlist(normalized_host, settings.allowed_audio_hosts):
        raise AppError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Audio URL host is not in the allowlist",
        )

    path = parsed.path or "/"
    return ValidatedAudioUrl(
        scheme="https",
        hostname=normalized_host,
        port=port,
        path=path,
        query=parsed.query,
        raw_url=raw_url,
        redacted_url=redact_url(raw_url),
    )


async def default_dns_resolver(hostname: str, port: int) -> list[str]:
    """Resolve all A and AAAA records for hostname via the system resolver."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip_str = str(sockaddr[0])
        if ip_str not in seen:
            seen.add(ip_str)
            addresses.append(ip_str)
    return addresses


async def resolve_and_validate_ips(
    hostname: str,
    port: int,
    resolver: AsyncDnsResolver | None = None,
) -> list[str]:
    """Resolve all A/AAAA records and fail closed if any address is non-public."""
    resolve_fn = resolver or default_dns_resolver
    try:
        addresses = await resolve_fn(hostname, port)
    except OSError as exc:
        raise AppError(
            status_code=502,
            code=ErrorCode.AUDIO_FETCH_FAILED,
            message="Failed to resolve audio host",
        ) from exc

    if not addresses:
        raise AppError(
            status_code=502,
            code=ErrorCode.AUDIO_FETCH_FAILED,
            message="Audio host resolved to no addresses",
        )

    for ip_str in addresses:
        if not is_public_ip(ip_str):
            raise AppError(
                status_code=400,
                code=ErrorCode.INVALID_REQUEST,
                message="Audio URL resolved to a disallowed network address",
            )

    return addresses
