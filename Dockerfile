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

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY scripts/prepare-models.py ./scripts/prepare-models.py

RUN uv sync --frozen --no-dev --no-editable \
    && rm -rf /root/.cache/uv

RUN mkdir -p "${HF_HOME}" \
    && if [ "${PREFETCH_MODELS}" = "true" ]; then \
        python scripts/prepare-models.py; \
    fi

RUN groupadd --gid "${APP_GID}" appuser \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --home-dir /nonexistent --shell /usr/sbin/nologin appuser \
    && chown -R "${APP_UID}:${APP_GID}" /app "${HF_HOME}"

ENV HF_HUB_OFFLINE=1

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

CMD ["uvicorn", "openpronounce_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
