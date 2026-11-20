# syntax=docker/dockerfile:1.7
# Pin python:3.12-slim to a reviewed immutable digest in release builds.
FROM python:3.12-slim AS runtime

ARG PREFETCH_MODELS=true
ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENPRONOUNCE_DEVICE=cpu \
    HF_HOME=/opt/models/huggingface \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install uv from official distroless binary image for deterministic frozen installs
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE LICENSE-MIT LICENSE-APACHE NOTICE ./
COPY src ./src
COPY scripts/prepare-models.py ./scripts/prepare-models.py

RUN uv sync --frozen --no-dev --no-editable \
    && python -c "from importlib.resources import files; assert files('openpronounce_api').joinpath('practice.html').is_file(), 'Missing practice page'" \
    && rm -rf /root/.cache/uv

RUN <<'SH'
set -eu
mkdir -p "${HF_HOME}"
if [ "${PREFETCH_MODELS}" = "true" ]; then
    python scripts/prepare-models.py
    python - <<'PY'
import json
import os
from pathlib import Path

reviewed = {
    "facebook/wav2vec2-lv-60-espeak-cv-ft": "ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4",
    "facebook/wav2vec2-large-960h": "bdeaacdf88f7a155f50a2704bc967aa81fbbb2ab",
}
manifest = json.loads((Path(os.environ["HF_HOME"]) / "model-manifest.json").read_text())
bundled = {model["repo_id"]: model["resolved_revision"] for model in manifest["models"]}
if bundled != reviewed:
    raise SystemExit(f"Bundled model revisions require a license review: {bundled}")
PY
fi
SH

RUN groupadd --gid "${APP_GID}" appuser \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --home-dir /nonexistent --shell /usr/sbin/nologin appuser \
    && chown -R "${APP_UID}:${APP_GID}" /app "${HF_HOME}"

ENV HF_HUB_OFFLINE=1

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

CMD ["uvicorn", "openpronounce_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
