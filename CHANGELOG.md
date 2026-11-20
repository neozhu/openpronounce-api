# Changelog

All notable changes to `openpronounce-api` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-20

### Added
- FastAPI v1 pronunciation assessment service (`POST /api/v1/assess`, `POST /api/v1/assess/url`) wrapping `openpronounce==1.1.0` on CPU only (`acoustic=False`, `prosody=False`, `transcription=True`).
- Operational endpoints (`GET /health/live`, `GET /health/ready`, `GET /version`) with non-blocking liveness and gated readiness.
- Strict audio validation pipeline: streaming byte limits, `ffprobe` container verification (WAV/MP3/FLAC/OGG/WebM), bounded `ffmpeg` 16 kHz mono normalization, duration caps, and guaranteed `0600` temp file cleanup.
- SSRF-hardened HTTPS audio URL fetcher: disabled by default, exact/dot-boundary host allowlist, fail-closed public IP resolution, pinned-IP TLS transport preventing DNS rebinding, redirect rejection, `trust_env=False`, and query-string redaction.
- Constant-time `X-API-Key` authentication, sanitized `X-Request-ID` propagation, structured JSON logging, and bounded CPU concurrency guard (`429 SERVER_BUSY`).
- Fast unit/API/security test suite (`scripts/test.sh`), curl runtime smoke script (`scripts/test-api.sh`), opt-in real-model test script (`scripts/test-model.sh`), and model prefetch/checksum script (`scripts/prepare-models.py`).
- Hardened CPU-only `Dockerfile`, `compose.yaml`, `.env.example`, GitHub Actions CI/model-smoke/GHCR release workflows, and API/deployment/security/licensing documentation.
