"""Application configuration with minimal required env vars and sensible built-in defaults."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from openpronounce_api import __version__


def _split_csv(value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise ValueError("Expected comma-separated string or sequence")


class Settings(BaseSettings):
    """Runtime settings for OpenPronounce API.

    Only operational/security settings are intended to be configured via environment variables;
    all audio, text, timeout, and backpressure bounds carry production-safe built-in defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application & engine metadata
    app_name: str = "openpronounce-api"
    app_version: str = __version__
    engine_name: str = "openpronounce"
    engine_version: str = "1.1.0"

    # Primary deployment env vars
    require_api_key: bool = Field(default=True, alias="REQUIRE_API_KEY")
    openpronounce_api_key: SecretStr | None = Field(default=None, alias="OPENPRONOUNCE_API_KEY")
    url_fetch_enabled: bool = Field(default=False, alias="URL_FETCH_ENABLED")
    allowed_audio_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        alias="ALLOWED_AUDIO_HOSTS",
    )
    max_concurrent_inferences: int = Field(
        default=1,
        ge=1,
        le=16,
        alias="MAX_CONCURRENT_INFERENCES",
    )

    # Optional operational overrides
    cors_allow_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        alias="CORS_ALLOW_ORIGINS",
    )
    enable_docs: bool = Field(default=True, alias="ENABLE_DOCS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    openpronounce_device: str = Field(default="cpu", alias="OPENPRONOUNCE_DEVICE")
    hf_home: str = Field(default="/opt/models/huggingface", alias="HF_HOME")
    hf_hub_offline: bool = Field(default=False, alias="HF_HUB_OFFLINE")
    preload_models: bool = Field(default=True, alias="PRELOAD_MODELS")

    # Built-in defaults (no env configuration required in normal deployments)
    allowed_audio_ports: tuple[int, ...] = (443,)
    url_connect_timeout_seconds: float = 5.0
    url_read_timeout_seconds: float = 15.0
    url_total_timeout_seconds: float = 20.0
    max_text_chars: int = 500
    min_text_words: int = 1
    max_text_words: int = 80
    max_audio_bytes: int = 20 * 1024 * 1024
    min_audio_seconds: float = 0.3
    max_audio_seconds: float = 60.0
    ffmpeg_timeout_seconds: float = 15.0
    inference_timeout_seconds: float = 60.0
    max_queued_inferences: int = 0
    queue_wait_timeout_seconds: float = 5.0
    temp_dir: str | None = None

    @field_validator("openpronounce_device")
    @classmethod
    def _validate_cpu_only(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "cpu":
            raise ValueError("OPENPRONOUNCE_DEVICE must be 'cpu' in v1")
        return normalized

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> tuple[str, ...]:
        return _split_csv(value)

    @field_validator("allowed_audio_hosts", mode="before")
    @classmethod
    def _parse_allowed_hosts(cls, value: object) -> tuple[str, ...]:
        hosts = tuple(h.lower() for h in _split_csv(value))
        for host in hosts:
            if host in {"*", "*.", ".*", "*.*"}:
                raise ValueError("Wildcard-all host patterns are forbidden in ALLOWED_AUDIO_HOSTS")
            if "*" in host:
                if not host.startswith("*.") or host.count("*") > 1:
                    raise ValueError(
                        f"Invalid wildcard pattern in ALLOWED_AUDIO_HOSTS: {host!r}; "
                        "only '*.example.com' is permitted"
                    )
                suffix = host[2:]
                if (
                    not suffix
                    or "." not in suffix
                    or suffix.startswith(".")
                    or suffix.endswith(".")
                ):
                    raise ValueError(f"Invalid wildcard domain in ALLOWED_AUDIO_HOSTS: {host!r}")
        return hosts

    @model_validator(mode="after")
    def _validate_cross_field_rules(self) -> Settings:
        if self.min_audio_seconds >= self.max_audio_seconds:
            raise ValueError("min_audio_seconds must be less than max_audio_seconds")
        if self.min_text_words > self.max_text_words:
            raise ValueError("min_text_words must be <= max_text_words")
        if self.require_api_key and "*" in self.cors_allow_origins:
            raise ValueError("CORS_ALLOW_ORIGINS cannot contain '*' when REQUIRE_API_KEY is true")
        return self

    def apply_runtime_env(self) -> None:
        """Ensure CPU-only and Hugging Face cache env vars are set before importing torch/engine."""
        os.environ["OPENPRONOUNCE_DEVICE"] = "cpu"
        if self.hf_home:
            os.environ["HF_HOME"] = self.hf_home
        if self.hf_hub_offline:
            os.environ["HF_HUB_OFFLINE"] = "1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached runtime settings."""
    return Settings()
