#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:-${OPENPRONOUNCE_API_KEY:-}}"
AUDIO_FILE="${AUDIO_FILE:-}"
EXPECTED_TEXT="${EXPECTED_TEXT:-I like eating apples.}"
TEST_AUDIO_URL="${TEST_AUDIO_URL:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

auth_args=()
if [[ -n "$API_KEY" ]]; then
  auth_args+=(-H "X-API-Key: $API_KEY")
fi

echo "[1/4] Checking liveness: ${BASE_URL}/health/live"
live_json="$(curl -fsS "${BASE_URL}/health/live")"
"$PYTHON_BIN" -c '
import json, sys
data = json.loads(sys.argv[1])
assert data.get("status") == "ok", f"Unexpected /health/live payload: {data}"
' "$live_json"

echo "[2/4] Checking readiness: ${BASE_URL}/health/ready"
ready_http="$(curl -sS -o /tmp/op_ready_body.json -w "%{http_code}" "${BASE_URL}/health/ready")"
if [[ "$ready_http" == "503" ]]; then
  echo "Service is alive but NOT READY (HTTP 503): $(cat /tmp/op_ready_body.json)" >&2
  rm -f /tmp/op_ready_body.json
  exit 2
fi
if [[ "$ready_http" != "200" ]]; then
  echo "Unexpected /health/ready HTTP status ${ready_http}: $(cat /tmp/op_ready_body.json)" >&2
  rm -f /tmp/op_ready_body.json
  exit 1
fi
"$PYTHON_BIN" -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
assert data.get("status") == "ready", f"Unexpected /health/ready payload: {data}"
assert data.get("engine", {}).get("name") == "openpronounce"
' /tmp/op_ready_body.json
rm -f /tmp/op_ready_body.json

validate_assess_json() {
  local payload="$1"
  "$PYTHON_BIN" -c '
import json, sys
data = json.loads(sys.argv[1])
for key in ("request_id", "status", "text", "language", "mode", "reliable", "reasons", "audio_quality", "engine"):
    assert key in data, f"Missing required key: {key}"
assert data["engine"]["name"] == "openpronounce"
assert isinstance(data["engine"]["version"], str) and data["engine"]["version"]
if data["reliable"]:
    assert data["status"] == "assessed"
    assert isinstance(data["scores"], dict)
    assert isinstance(data["scores"]["overall"], (int, float))
else:
    assert data["status"] == "uncertain"
    assert data["scores"] is None
assert "correct" not in data, "Public v1 schema must not include a pass/fail correct boolean"
status = data["status"]
reliable = data["reliable"]
version = data["engine"]["version"]
print(f"Validated assessment response: status={status} reliable={reliable} engine={version}")
' "$payload"
}

if [[ -z "$AUDIO_FILE" ]]; then
  echo "ERROR: AUDIO_FILE must point to a real authorized audio recording." >&2
  echo "Example: AUDIO_FILE=tests/fixtures/sentence_apples.wav EXPECTED_TEXT='I like eating apples.' bash scripts/test-api.sh" >&2
  exit 1
fi
if [[ ! -f "$AUDIO_FILE" ]]; then
  echo "ERROR: AUDIO_FILE not found: $AUDIO_FILE" >&2
  exit 1
fi

echo "[3/4] Testing multipart upload: POST ${BASE_URL}/api/v1/assess"
upload_json="$(curl --fail-with-body -sS \
  "${auth_args[@]}" \
  -F "audio=@${AUDIO_FILE}" \
  -F "text=${EXPECTED_TEXT}" \
  "${BASE_URL}/api/v1/assess")"
validate_assess_json "$upload_json"

if [[ -n "$TEST_AUDIO_URL" ]]; then
  if [[ "$TEST_AUDIO_URL" =~ ^https?://(localhost|127\.|\[::1\]) ]]; then
    echo "ERROR: TEST_AUDIO_URL must use an allowlisted external HTTPS host, never localhost." >&2
    exit 1
  fi
  echo "[4/4] Testing allowlisted HTTPS URL: POST ${BASE_URL}/api/v1/assess/url"
  req_body="$("$PYTHON_BIN" -c '
import json, sys
print(json.dumps({"audio_url": sys.argv[1], "text": sys.argv[2], "language": "en", "mode": "auto"}))
' "$TEST_AUDIO_URL" "$EXPECTED_TEXT")"
  url_json="$(curl --fail-with-body -sS \
    "${auth_args[@]}" \
    -H "Content-Type: application/json" \
    -d "$req_body" \
    "${BASE_URL}/api/v1/assess/url")"
  validate_assess_json "$url_json"
else
  echo "[4/4] Skipping URL test (set TEST_AUDIO_URL to an allowlisted external HTTPS fixture to run)."
fi

echo "API smoke test passed."
