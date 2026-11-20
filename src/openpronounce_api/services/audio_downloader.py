"""SSRF-hardened HTTPS audio downloader with DNS pinning and strict streaming bounds."""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass

import anyio
import httpcore
import httpx

from openpronounce_api.core.config import Settings
from openpronounce_api.core.errors import AppError, ErrorCode
from openpronounce_api.core.security import (
    AsyncDnsResolver,
    ValidatedAudioUrl,
    resolve_and_validate_ips,
    validate_audio_url,
)
from openpronounce_api.services.audio import create_temp_file, safe_unlink

logger = logging.getLogger(__name__)

_DISALLOWED_CONTENT_TYPE_PREFIXES = (
    "text/",
    "image/",
    "multipart/",
)
_DISALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/javascript",
        "application/pdf",
        "application/zip",
    }
)
_DOWNLOAD_CHUNK_SIZE = 64 * 1024

TransportFactory = Callable[[ValidatedAudioUrl, str, Settings], httpx.AsyncBaseTransport]


@dataclass(frozen=True, slots=True)
class DownloadedAudio:
    """Temporary file downloaded from an allowlisted HTTPS URL."""

    raw_path: str
    size_bytes: int
    redacted_url: str


class _PinnedIPNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect TCP exclusively to a pre-validated IP while preserving TLS SNI and cert checks."""

    def __init__(self, pinned_ip: str, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._pinned_ip = pinned_ip
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - required by httpcore interface
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        # Connect directly to the already-validated public IP (preventing DNS rebinding / TOCTOU).
        # httpcore still passes the original validated hostname as server_hostname for TLS SNI
        # and X.509 hostname verification.
        return await self._inner.connect_tcp(
            host=self._pinned_ip,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - required by httpcore interface
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AppError(
            status_code=502,
            code=ErrorCode.AUDIO_FETCH_FAILED,
            message="Unix domain sockets are not permitted for remote audio fetch",
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """httpx transport backed by a pinned-IP httpcore connection pool."""

    def __init__(self, pinned_ip: str) -> None:
        ssl_context = ssl.create_default_context()
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_PinnedIPNetworkBackend(pinned_ip),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        resp = await self._pool.handle_async_request(req)
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=_HttpcoreAsyncByteStream(resp.stream),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class _HttpcoreAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, httpcore_stream: httpcore.AsyncByteStream) -> None:
        self._stream = httpcore_stream

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


def _default_transport_factory(
    validated: ValidatedAudioUrl,
    pinned_ip: str,
    settings: Settings,
) -> httpx.AsyncBaseTransport:
    del validated, settings
    return _PinnedAsyncHTTPTransport(pinned_ip=pinned_ip)


def _check_content_type(content_type_header: str | None) -> None:
    if not content_type_header:
        return
    media_type = content_type_header.split(";", 1)[0].strip().lower()
    if not media_type:
        return
    if media_type in _DISALLOWED_CONTENT_TYPES or media_type.startswith(
        _DISALLOWED_CONTENT_TYPE_PREFIXES
    ):
        raise AppError(
            status_code=415,
            code=ErrorCode.AUDIO_UNSUPPORTED_FORMAT,
            message="Remote resource returned a non-audio Content-Type",
        )


def _check_content_length(content_length_header: str | None, max_bytes: int) -> None:
    if not content_length_header:
        return
    try:
        length = int(content_length_header.strip())
    except ValueError as exc:
        raise AppError(
            status_code=502,
            code=ErrorCode.AUDIO_FETCH_FAILED,
            message="Remote audio host returned an invalid Content-Length",
        ) from exc
    if length < 0:
        raise AppError(
            status_code=502,
            code=ErrorCode.AUDIO_FETCH_FAILED,
            message="Remote audio host returned an invalid Content-Length",
        )
    if length > max_bytes:
        raise AppError(
            status_code=413,
            code=ErrorCode.AUDIO_TOO_LARGE,
            message="Remote audio exceeds maximum allowed size",
        )


class AudioDownloader:
    """Restricted HTTPS audio downloader enforcing allowlist, DNS pinning, and strict caps."""

    def __init__(
        self,
        *,
        dns_resolver: AsyncDnsResolver | None = None,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self._dns_resolver = dns_resolver
        self._transport_factory = transport_factory or _default_transport_factory

    async def download(self, raw_url: str, *, settings: Settings) -> DownloadedAudio:
        """Validate URL, resolve & pin a public IP, and stream into a bounded temp file."""
        validated = validate_audio_url(raw_url, settings)
        resolved_ips = await resolve_and_validate_ips(
            validated.hostname,
            validated.port,
            resolver=self._dns_resolver,
        )
        pinned_ip = resolved_ips[0]

        raw_path = create_temp_file(suffix=".urlaudio", temp_dir=settings.temp_dir)
        timeout = httpx.Timeout(
            connect=settings.url_connect_timeout_seconds,
            read=settings.url_read_timeout_seconds,
            write=settings.url_connect_timeout_seconds,
            pool=settings.url_connect_timeout_seconds,
        )
        transport = self._transport_factory(validated, pinned_ip, settings)

        total_bytes = 0
        try:
            async with (
                asyncio.timeout(settings.url_total_timeout_seconds),
                httpx.AsyncClient(
                    transport=transport,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=timeout,
                    headers={
                        "User-Agent": f"{settings.app_name}/{settings.app_version}",
                        "Accept": "audio/*,application/octet-stream;q=0.8",
                    },
                ) as client,
                client.stream("GET", validated.raw_url) as response,
            ):
                if 300 <= response.status_code < 400:
                    raise AppError(
                        status_code=502,
                        code=ErrorCode.AUDIO_FETCH_FAILED,
                        message="Redirects are not allowed for audio URLs",
                    )
                if response.status_code != 200:
                    raise AppError(
                        status_code=502,
                        code=ErrorCode.AUDIO_FETCH_FAILED,
                        message="Remote audio host returned an unsuccessful HTTP status",
                    )

                _check_content_type(response.headers.get("content-type"))
                _check_content_length(
                    response.headers.get("content-length"),
                    settings.max_audio_bytes,
                )

                async with await anyio.open_file(raw_path, "wb") as handle:
                    async for chunk in response.aiter_bytes(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > settings.max_audio_bytes:
                            raise AppError(
                                status_code=413,
                                code=ErrorCode.AUDIO_TOO_LARGE,
                                message="Remote audio exceeds maximum allowed size",
                            )
                        await handle.write(chunk)
        except AppError:
            safe_unlink(raw_path)
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            safe_unlink(raw_path)
            logger.warning(
                "Remote audio download timed out",
                extra={"source_url": validated.redacted_url},
            )
            raise AppError(
                status_code=504,
                code=ErrorCode.ASSESSMENT_TIMEOUT,
                message="Remote audio download timed out",
            ) from exc
        except httpx.HTTPError as exc:
            safe_unlink(raw_path)
            logger.warning(
                "Remote audio download failed",
                extra={"source_url": validated.redacted_url, "error_type": type(exc).__name__},
            )
            raise AppError(
                status_code=502,
                code=ErrorCode.AUDIO_FETCH_FAILED,
                message="Failed to fetch audio from remote host",
            ) from exc
        except BaseException:
            safe_unlink(raw_path)
            raise

        if total_bytes == 0:
            safe_unlink(raw_path)
            raise AppError(
                status_code=422,
                code=ErrorCode.AUDIO_UNPROCESSABLE,
                message="Remote audio resource is empty",
            )

        return DownloadedAudio(
            raw_path=raw_path,
            size_bytes=total_bytes,
            redacted_url=validated.redacted_url,
        )
