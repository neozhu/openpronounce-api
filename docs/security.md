# Security & Privacy Architecture

## Authentication

- `REQUIRE_API_KEY=true` by default for externally reachable deployments.
- `X-API-Key` is verified using constant-time comparison (`hmac.compare_digest`).
- If `REQUIRE_API_KEY=true` and `OPENPRONOUNCE_API_KEY` is unset/empty, the service **fails closed** (`401 UNAUTHORIZED`).
- Store production keys in `.env` (gitignored) or Docker/orchestrator secrets; rotate by updating the secret and restarting the container.
- Operational routes (`/health/live`, `/health/ready`, `/version`) do not require `X-API-Key` so infrastructure probes remain simple; expose them only internally.

## SSRF Defenses for `POST /api/v1/assess/url`

1. **Disabled by default:** `URL_FETCH_ENABLED=false` until an explicit `ALLOWED_AUDIO_HOSTS` list is configured.
2. **No wildcard-all:** Configuration validation rejects `*`, `*.`, `.*`, `*.*`, and malformed wildcard entries. Hostname matching is exact or strict dot-boundary (`*.trusted-storage.example.com` matches `bucket.trusted-storage.example.com` and rejects `trusted-storage.example.com` and `good.example.evil.org`).
3. **Strict URL grammar:** Only `https://` on permitted ports (default `443`). Rejects userinfo (`@`), fragments (`#`), IP literals (IPv4, IPv6, IPv4-mapped IPv6, decimal/octal/hex shorthand), `localhost`/`.local`/`.internal`, backslashes, whitespace, and raw or percent-encoded control characters.
4. **Fail-closed DNS inspection:** Resolves all A and AAAA records and rejects loopback, RFC1918 private, link-local (`169.254.0.0/16`, `fe80::/10`), CGNAT (`100.64.0.0/10`), multicast, reserved, unspecified, NAT64, and IPv4-mapped IPv6 private addresses. If **any** returned IP is non-public, the request is rejected.
5. **DNS rebinding / TOCTOU defense:** Connections use a custom `httpcore` network backend that opens TCP directly to the pre-validated public IP while passing the validated hostname for TLS SNI, X.509 certificate verification, and the HTTP `Host` header.
6. **No redirects & no ambient proxies:** `follow_redirects=False` (all `3xx` responses rejected with `502 AUDIO_FETCH_FAILED`) and `trust_env=False`.
7. **Bounded streaming & timeouts:** Enforces connect (`5s`), read (`15s`), and total download (`20s`) timeouts, plus streaming byte caps (`MAX_AUDIO_BYTES`, default 20 MiB) even when `Content-Length` is omitted or falsified.
8. **Deployment-level egress control:** For defense in depth, also restrict container outbound network traffic at the firewall/proxy layer to only the allowlisted storage domains (or block all egress when `URL_FETCH_ENABLED=false`).

## Audio Handling & Privacy

- **Zero persistence:** Uploaded and downloaded audio files are written to unique owner-only (`0600`) temporary files and unlinked in `finally` blocks across success, decode errors, timeouts, cancellations, and model failures.
- **No third-party TTS calls:** `acoustic=False` ensures expected learner text is never sent to Google TTS or any external service.
- **Log redaction:** Structured logs record only `request_id`, endpoint, HTTP status, latency, audio byte size, decoded duration, reliability flag, and engine version. Raw audio, expected text, API keys, and signed URL query strings are never logged or returned in error responses.
- **CORS:** Disabled by default (`CORS_ALLOW_ORIGINS=""`). Wildcard `*` is rejected by configuration validation when `REQUIRE_API_KEY=true`.
