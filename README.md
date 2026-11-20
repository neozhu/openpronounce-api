# openpronounce-api

CPU-only FastAPI service wrapping [`openpronounce==1.1.0`](https://pypi.org/project/openpronounce/1.1.0/) for English word, phrase, and sentence reading assessment. Designed for reuse by .NET/Blazor, mobile, and automation clients with a stable JSON contract, SSRF-hardened HTTPS audio URL ingestion, and reproducible Docker / GHCR delivery.

## Key Characteristics (v1)

- **Runtime:** Python 3.12 (`>=3.12,<3.13`), FastAPI, Pydantic v2, Uvicorn (single worker), locked via `uv.lock` with CPU-only PyTorch wheels.
- **Engine Policy:** `OPENPRONOUNCE_DEVICE=cpu`, `acoustic=False` (no TTS synthesis or network calls), `prosody=False` (pitch contours skipped while retaining the trained `prosodic` score), `transcription=True` (word mismatch/missing-word diagnostics retained).
- **Inputs:** Multipart upload (`POST /api/v1/assess`) and allowlisted HTTPS URL (`POST /api/v1/assess/url`, disabled by default) feeding the exact same audio validation, inference, and mapping pipeline.
- **Audio Support:** WAV, MP3, FLAC, OGG, and WebM validated by container probing (`ffprobe`) and normalized to 16 kHz mono PCM via bounded `ffmpeg`.
- **Reliability Policy:** Upstream `reliable=false` returns `200 OK` with `status="uncertain"`, `scores=null`, and safe reason codes. No uncalibrated pass/fail `correct` boolean in v1.
- **Zero Persistence:** Temporary audio files use `0600` permissions and are deleted in `finally` across all paths.

## Quick Start (Local Development)

Prerequisites: Python 3.12, [`uv`](https://docs.astral.sh/uv/), and `ffmpeg` (`ffprobe` + `ffmpeg`).

```bash
cp .env.example .env
# Edit .env and set OPENPRONOUNCE_API_KEY=<your-strong-key>

# 1. Run fast unit, API, and SSRF security tests (no model download):
bash scripts/test.sh

# 2. Prefetch OpenPronounce 1.1.0 CPU checkpoints (~2.4 GiB) once:
HF_HOME=./.models/huggingface uv run python scripts/prepare-models.py

# 3. Start the API server offline on CPU:
OPENPRONOUNCE_DEVICE=cpu \
HF_HOME=./.models/huggingface \
HF_HUB_OFFLINE=1 \
OPENPRONOUNCE_API_KEY=dev-key \
ENABLE_DOCS=true \
uv run uvicorn openpronounce_api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

## Usage Examples

### Multipart File Upload (`POST /api/v1/assess`)

```bash
curl --fail-with-body \
  -H "X-API-Key: $OPENPRONOUNCE_API_KEY" \
  -F 'audio=@tests/fixtures/sentence_apples.wav' \
  -F 'text=I like eating apples.' \
  -F 'language=en' \
  -F 'mode=auto' \
  http://127.0.0.1:8000/api/v1/assess
```

### Allowlisted HTTPS Audio URL (`POST /api/v1/assess/url`)

Requires `URL_FETCH_ENABLED=true` and explicit `ALLOWED_AUDIO_HOSTS` (e.g., `media.example.org,*.trusted-storage.example.com`).

```bash
curl --fail-with-body \
  -H "X-API-Key: $OPENPRONOUNCE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://media.example.org/recordings/answer.webm?signature=redacted",
    "text": "I like eating apples.",
    "language": "en",
    "mode": "auto"
  }' \
  http://127.0.0.1:8000/api/v1/assess/url
```

### .NET / Blazor `HttpClient` Integration Snippet

```csharp
using System.Net.Http.Headers;
using System.Net.Http.Json;

using var http = new HttpClient { BaseAddress = new Uri("http://127.0.0.1:8000") };
http.DefaultRequestHeaders.Add("X-API-Key", Environment.GetEnvironmentVariable("OPENPRONOUNCE_API_KEY"));

// Multipart upload
using var form = new MultipartFormDataContent();
await using var audioStream = File.OpenRead("recording.webm");
var audioContent = new StreamContent(audioStream);
audioContent.Headers.ContentType = new MediaTypeHeaderValue("audio/webm");
form.Add(audioContent, "audio", "recording.webm");
form.Add(new StringContent("I like eating apples."), "text");
form.Add(new StringContent("en"), "language");
form.Add(new StringContent("auto"), "mode");

using var response = await http.PostAsync("/api/v1/assess", form);
response.EnsureSuccessStatusCode();
var json = await response.Content.ReadAsStringAsync();

// Or allowlisted HTTPS URL
using var urlResponse = await http.PostAsJsonAsync("/api/v1/assess/url", new
{
    audio_url = "https://media.example.org/recordings/answer.webm?signature=...",
    text = "I like eating apples.",
    language = "en",
    mode = "auto"
});
urlResponse.EnsureSuccessStatusCode();
```

## Docker & Offline Operation

```bash
cp .env.example .env
# Set a strong OPENPRONOUNCE_API_KEY in .env

# Build self-contained offline CPU image (prefetches the two HF checkpoints):
docker compose build

# Start hardened non-root container bound to 127.0.0.1:8000:
docker compose up -d

# Check liveness (immediate) and readiness (after model warm-up):
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

The image serves the microphone practice page and scoring API on the same port.
Open [the practice page](http://127.0.0.1:8000/practice) after the readiness check succeeds.
Set `OPENPRONOUNCE_API_KEY` in `.env` at runtime. When authentication is enabled,
enter that same key in the practice page when prompted, or send it as `X-API-Key`
from an API client. Use HTTPS when opening the practice page from a remote browser.

## Testing

| Command | Purpose | Downloads Models? |
|---|---|---|
| `bash scripts/test.sh` | Frozen `uv sync`, `ruff check`, `ruff format --check`, unit/API/SSRF security tests | No |
| `AUDIO_FILE=... EXPECTED_TEXT='...' API_KEY=... bash scripts/test-api.sh` | Live runtime smoke test against running server (`/health/live`, `/health/ready`, `/api/v1/assess`, optional `/api/v1/assess/url`) | Uses running server |
| `bash scripts/test-model.sh` | Opt-in real-model CPU integration tests (`RUN_REAL_MODEL_TESTS=1`) | Yes (or uses `HF_HOME` cache) |

## Configuration Reference

Most limits and timeouts use production-safe built-in defaults (20 MiB max audio, 0.3–60 s duration, 500 chars / 80 words max text, HTTPS port 443, 5/15/20 s URL timeouts, 60 s inference timeout, CPU-only). In normal deployments you only need `.env` for:

- `OPENPRONOUNCE_API_KEY` (required when `REQUIRE_API_KEY=true`, the default)
- `URL_FETCH_ENABLED` (default `false`) and `ALLOWED_AUDIO_HOSTS` (only when enabling `/api/v1/assess/url`)
- Optional overrides when needed: `REQUIRE_API_KEY`, `MAX_CONCURRENT_INFERENCES` (default `1`), `ENABLE_DOCS`, `CORS_ALLOW_ORIGINS`, `HF_HOME`, `HF_HUB_OFFLINE`

See `.env.example` for the complete minimal template.

## Known Limitations & Calibration Notes

- **Sentence vs. short-word reliability:** OpenPronounce 1.1.0 was calibrated primarily on sentence readings (e.g., speechocean762). On single short words, a single altered phoneme can dominate the score; always display cautious feedback and respect `reliable=false` (`status="uncertain"`).
- **Children vs. adult voices:** Model ratings depend on speaker age, accent, microphone quality, and recording length. Empirically calibrate any client-side UI bands on consented target-audience recordings before showing scores to learners.
- **CPU latency & memory:** Keep `--workers 1` and budget ~4 GiB RAM per instance. Concurrency above `MAX_CONCURRENT_INFERENCES` returns `429 SERVER_BUSY` cleanly instead of exhausting host memory.

## Documentation & Licensing

- [API Reference](docs/api.md)
- [Deployment & Upgrade Guide](docs/deployment.md)
- [Security & Privacy Architecture](docs/security.md)
- [Licensing & GHCR Publication Gate](docs/license.md) — **Important:** OpenPronounce 1.1.0 and its shipped models are licensed under **AGPL-3.0-or-later or a commercial license**. Review `docs/license.md` before public deployment or GHCR image publication.
