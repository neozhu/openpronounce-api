# Audio Test Fixtures

This directory holds non-sensitive test fixtures and instructions for opt-in real-model testing.

## Included fixtures

- `invalid_audio.bin`: Deliberately invalid non-audio bytes used by fast unit/API tests to verify container probing rejects disguised or unsupported inputs (`415 AUDIO_UNSUPPORTED_FORMAT`).

## Synthesized fast-test fixtures

Fast unit and API tests (`tests/unit`, `tests/api`) generate short deterministic WAV and WebM clips in a temporary directory using Python's `wave` module and `ffmpeg` so CI runs cleanly without checking in large binary media files or downloading multi-GB speech models.

## Adding redistributable real voice fixtures (opt-in model tests)

Before running `scripts/test-model.sh` or `.github/workflows/model-smoke.yml` as a release gate, provide user-authorized or properly licensed English voice recordings (for example, CC0 / CC BY recordings with attribution recorded here):

1. `word_apple.wav` — single English word (e.g., `"apple"`)
2. `phrase_good_morning.wav` — short phrase (e.g., `"good morning"`)
3. `sentence_apples.wav` — full sentence (e.g., `"I like eating apples."`)
4. `quiet_short.wav` — very quiet or insufficient clip expected to trigger `reliable=false` (`status="uncertain"`)

Never commit private learner recordings, unconsented children's speech, or signed storage URLs to this repository.
