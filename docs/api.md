# API Reference (`v1`)

Base path: `/api/v1`

All error responses across the service use the standardized error envelope and include `X-Request-ID` in response headers.

## Authentication

When `REQUIRE_API_KEY=true` (recommended default), `/api/v1/*` routes require:

```http
X-API-Key: <OPENPRONOUNCE_API_KEY>
```

Operational endpoints (`/health/live`, `/health/ready`, `/version`) are intentionally unauthenticated so container orchestrators and reverse proxies can probe health without storing API keys. Expose them only on private networks or restrict them at your reverse proxy.

## Endpoints

### `POST /api/v1/assess` — Multipart audio upload

Content-Type: `multipart/form-data`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `audio` | file | yes | — | WAV, MP3, FLAC, OGG, or WebM (max 20 MiB by default; validated via container probing) |
| `text` | string | yes | — | Expected English reading text (1–80 words, max 500 chars) |
| `language` | string | no | `en` | `en` or alias `en-US` (mapped to upstream `en`) |
| `mode` | enum | no | `auto` | `auto`, `word`, `phrase`, `sentence` (display metadata hint only; does not change model weights) |

### `POST /api/v1/assess/url` — Allowlisted HTTPS audio URL

Content-Type: `application/json`

```json
{
  "audio_url": "https://media.example.org/recordings/answer.webm?signature=redacted",
  "text": "I like eating apples.",
  "language": "en",
  "mode": "auto"
}
```

URL ingestion is **disabled by default** (`URL_FETCH_ENABLED=false`). When enabled, only `https://` URLs matching `ALLOWED_AUDIO_HOSTS` on permitted ports (default `443`) resolving solely to globally routable public IPs are accepted. Redirects (`3xx`) are rejected. Signed query strings are never logged or echoed back.

### `GET /health/live`

Returns `200 {"status": "ok"}` whenever the HTTP event loop is responsive. Never triggers model initialization or inference.

### `GET /health/ready`

Returns `200 {"status": "ready", "engine": {"name": "openpronounce", "version": "1.1.0"}}` once OpenPronounce CPU checkpoints are loaded. Returns `503 MODEL_UNAVAILABLE` while warming up or if initialization failed.

### `GET /version`

Returns service name, application version, engine name/version, and compute device (`cpu`). Never exposes environment secrets.

## Unified Success Response (`200 OK`)

```json
{
  "request_id": "01JXYZEXAMPLE0000000000000",
  "status": "assessed",
  "text": "I like eating apples.",
  "language": "en",
  "mode": "sentence",
  "reliable": true,
  "reasons": [],
  "scores": {
    "overall": 82.1,
    "accuracy": 78.0,
    "fluency": 88.0,
    "prosodic": 81.0
  },
  "transcription": "I LIKE EATING APPLES",
  "words": [
    {
      "text": "apples",
      "position": 3,
      "start_seconds": 0.72,
      "end_seconds": 1.12,
      "mispronounced": true,
      "missing": false,
      "expected_ipa": "æpəlz",
      "heard_ipa": "æpəls",
      "confidence": 0.79,
      "phonemes": [
        {
          "expected": "z",
          "heard": "s",
          "confidence": 0.65,
          "start_seconds": 0.95,
          "end_seconds": 1.12
        }
      ]
    }
  ],
  "extra_speech": [],
  "audio_quality": {
    "duration_seconds": 1.2,
    "issues": []
  },
  "engine": {
    "name": "openpronounce",
    "version": "1.1.0"
  }
}
```

### Contract conventions

- **0-based word positions**: `words[].position` and `extra_speech[].after_position` / `before_position` follow OpenPronounce 1.1.0's 0-based indexing.
- **Reliability gating**: When `reliable=false`, `status` is `"uncertain"`, `scores` is `null`, and `reasons` contains safe machine-readable codes (`audio_too_short`, `audio_silent`, `audio_too_quiet`, `audio_clipped`, `audio_noisy`, `missing_words`, `text_mismatch`, `extra_speech`, `unreliable_assessment`).
- **No fabricated fields**: Nullable timestamps, IPA strings, and score dimensions remain `null` when upstream does not provide them.
- **No pass/fail boolean**: v1 intentionally omits a `correct` boolean or default threshold because calibration varies across accents, ages, and single-word utterances.

## Error Envelope

```json
{
  "request_id": "01JXYZEXAMPLE0000000000000",
  "error": {
    "code": "AUDIO_UNSUPPORTED_FORMAT",
    "message": "Unsupported or invalid audio format"
  }
}
```

| HTTP | Error Code | Meaning |
|---|---|---|
| 400 | `INVALID_REQUEST` | Invalid text/language/mode, disabled URL fetch, or disallowed URL target |
| 401 | `UNAUTHORIZED` | Missing or invalid `X-API-Key` |
| 413 | `AUDIO_TOO_LARGE` | Upload or remote stream exceeds `MAX_AUDIO_BYTES` |
| 415 | `AUDIO_UNSUPPORTED_FORMAT` | Non-audio media type or unsupported container format |
| 422 | `AUDIO_UNPROCESSABLE` | Empty, undecodable waveform, or duration outside `[MIN_AUDIO_SECONDS, MAX_AUDIO_SECONDS]` |
| 429 | `SERVER_BUSY` | CPU inference concurrency capacity reached (`Retry-After: 1`) |
| 502 | `AUDIO_FETCH_FAILED` | Remote host unreachable, non-200 status, or redirect rejected |
| 503 | `MODEL_UNAVAILABLE` | Scoring engine still initializing or failed to load |
| 504 | `ASSESSMENT_TIMEOUT` | Download, `ffmpeg` decode, or CPU inference timed out |
| 500 | `INTERNAL_ERROR` | Unexpected failure (sanitized public message) |
