# Deployment Guide

## Model Delivery Policy

OpenPronounce 1.1.0 with `acoustic=False, prosody=False, transcription=True` loads two Hugging Face checkpoints (~2.4 GiB combined on disk):

1. `facebook/wav2vec2-lv-60-espeak-cv-ft` (multilingual IPA phone recognizer)
2. `facebook/wav2vec2-large-960h` (English CTC word transcription)

No TTS checkpoints or network speech synthesis (gTTS, Piper, Kokoro) are installed or invoked.

### Recommended production mode: Prebundled offline image (`PREFETCH_MODELS=true`)

The default `Dockerfile` runs `scripts/prepare-models.py` during image build, writes `/opt/models/huggingface/model-manifest.json` (containing resolved commit revisions and file SHA-256 checksums), and sets `HF_HUB_OFFLINE=1` at runtime.

- **Pros:** Deterministic startup, zero runtime Hugging Face downloads, supports air-gapped or egress-restricted hosts.
- **Trade-off:** Large image (~3–4 GiB including CPU PyTorch + two Wav2Vec2 checkpoints).

### Alternative mode: External persistent `HF_HOME` volume (`PREFETCH_MODELS=false`)

Build with `--build-arg PREFETCH_MODELS=false`, pre-populate a host volume using `scripts/prepare-models.py`, mount it read-only at `/opt/models/huggingface`, and set `HF_HUB_OFFLINE=1` only after the cache is complete. Never claim offline operation if `HF_HUB_OFFLINE=0`.

## CPU, Memory, and Concurrency Sizing

- **Workers:** Run **1 Uvicorn worker** (`--workers 1`). Multiplying workers duplicates ~4 GiB of resident model weights in RAM.
- **Memory:** Budget at least **4 GiB RAM per instance** (6 GiB limit recommended; 8 GiB+ host when co-located with other services).
- **Concurrency:** Default `MAX_CONCURRENT_INFERENCES=1` and `MAX_QUEUED_INFERENCES=0` (fail-fast `429 SERVER_BUSY`). Tune `OMP_NUM_THREADS` and `TORCH_NUM_THREADS` empirically on the target CPU.
- **Health probes:**
  - Point liveness checks at `GET /health/live` (fast, never blocks on model loading).
  - Gate traffic routing on `GET /health/ready` with a generous startup period (`60s–120s` on cold CPU disks).

## Hardened Reverse Proxy (Traefik / Nginx)

Bind the container to loopback (`127.0.0.1:8000:8000` in `compose.yaml`) and place Traefik or Nginx in front to enforce:

- TLS termination
- Request body size cap matching `MAX_AUDIO_BYTES` (e.g. `client_max_body_size 22m;`)
- Per-client rate limiting
- Optional restriction of `/docs`, `/openapi.json`, `/health/*`, and `/version` to internal networks (or set `ENABLE_DOCS=false` in production)

## Container Hardening

`compose.yaml` runs the service with:

- Dedicated non-root user (`appuser`, UID `10001`)
- `read_only: true` root filesystem with bounded `tmpfs` at `/tmp` (`size=128m`)
- `cap_drop: [ALL]`
- `security_opt: [no-new-privileges:true]`
- `OPENPRONOUNCE_DEVICE=cpu` and `HF_HUB_OFFLINE=1`

## Upgrading OpenPronounce

1. Bump `openpronounce==<new_version>` in `pyproject.toml` in a dedicated PR.
2. Regenerate `uv.lock` via `uv lock` on Python 3.12 and verify CPU PyTorch wheel resolution.
3. Run fast CI (`bash scripts/test.sh`).
4. Trigger `.github/workflows/model-smoke.yml` (or run `bash scripts/test-model.sh`) to verify upstream output schema, `reliable` semantics, and memory/latency regressions.
5. Audit updated model licenses in `docs/license.md` before tagging `vX.Y.Z`.
