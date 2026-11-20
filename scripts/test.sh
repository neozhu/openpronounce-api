#!/usr/bin/env bash
set -euo pipefail

uv sync --frozen --extra dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q tests/unit tests/api
