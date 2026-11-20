#!/usr/bin/env bash
set -euo pipefail

export OPENPRONOUNCE_DEVICE=cpu
export RUN_REAL_MODEL_TESTS=1

echo "Running opt-in real-model integration tests (CPU-only, acoustic=False, transcription=True)..."
echo "HF_HOME=${HF_HOME:-$HOME/.cache/huggingface} HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}"

uv sync --frozen --extra dev
uv run pytest -v -m integration tests/integration/test_real_model.py
