"""Structured JSON logging, request ID generation/sanitization, and URL redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

_REQUEST_ID_VAR: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_STANDARD_LOG_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def generate_request_id() -> str:
    """Generate a 26-character ULID-compatible Crockford Base32 request identifier."""
    timestamp_ms = int(time.time() * 1000)
    rand_bits = int.from_bytes(os.urandom(10), "big")
    value = (timestamp_ms << 80) | rand_bits
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def sanitize_request_id(raw_value: str | None) -> str:
    """Accept caller X-Request-ID only if it matches a strict safe character set."""
    if raw_value:
        candidate = raw_value.strip()
        if _SAFE_REQUEST_ID_RE.fullmatch(candidate):
            return candidate
    return generate_request_id()


def set_request_id(request_id: str) -> contextvars.Token[str]:
    """Bind the active request ID in contextvars."""
    return _REQUEST_ID_VAR.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    """Restore previous request ID context."""
    _REQUEST_ID_VAR.reset(token)


def get_request_id() -> str:
    """Return the active request ID."""
    return _REQUEST_ID_VAR.get()


def redact_url(raw_url: str | None) -> str:
    """Return scheme://host/path with userinfo, query string, and fragment removed."""
    if not raw_url:
        return "<empty>"
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return "<redacted>"
    if not parsed.scheme or not parsed.hostname:
        return "<redacted>"
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port else host
    path = parsed.path or "/"
    return f"{parsed.scheme.lower()}://{netloc}{path}"


class StructuredJsonFormatter(logging.Formatter):
    """Emit structured JSON log records without leaking secrets or user media."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": get_request_id(),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str, ensure_ascii=True)


def configure_logging(level: str = "INFO") -> None:
    """Configure root and service loggers to emit structured JSON."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)

    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
