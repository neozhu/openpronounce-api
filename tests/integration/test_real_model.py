"""Opt-in integration tests validating real OpenPronounce 1.1.0 CPU inference.

Run explicitly via ``RUN_REAL_MODEL_TESTS=1 bash scripts/test-model.sh``.
Never executed by the fast PR CI suite.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from openpronounce_api.adapters.openpronounce_adapter import OpenPronounceAdapter
from openpronounce_api.core.config import Settings
from openpronounce_api.schemas.requests import ResolvedAssessmentMode
from openpronounce_api.services.audio import normalize_audio_file
from openpronounce_api.services.result_mapper import map_upstream_result
from tests.conftest import make_wav_bytes

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_REAL_MODEL_TESTS") != "1",
        reason="Set RUN_REAL_MODEL_TESTS=1 to run heavyweight real-model integration tests",
    ),
]


@pytest.fixture(scope="module")
def loaded_real_adapter() -> OpenPronounceAdapter:
    adapter = OpenPronounceAdapter(
        hf_home=os.environ.get("HF_HOME"),
        hf_hub_offline=os.environ.get("HF_HUB_OFFLINE") == "1",
    )
    adapter.load()
    assert adapter.is_ready is True
    return adapter


async def test_real_model_quiet_clip_maps_to_uncertain_without_tts(
    loaded_real_adapter: OpenPronounceAdapter,
    tmp_path: Path,
) -> None:
    """Verify quiet/non-speech audio runs on CPU without TTS and yields uncertain status."""
    quiet_path = tmp_path / "quiet_clip.wav"
    quiet_bytes = make_wav_bytes(duration_seconds=0.8, amplitude=0.00001)
    quiet_path.write_bytes(quiet_bytes)

    settings = Settings(PRELOAD_MODELS=False, temp_dir=str(tmp_path))
    normalized = await normalize_audio_file(
        str(quiet_path),
        input_bytes=len(quiet_bytes),
        settings=settings,
    )
    try:
        with patch(
            "openpronounce.audio.text2speech",
            side_effect=AssertionError("TTS must never be called when acoustic=False"),
        ):
            raw = loaded_real_adapter.assess(
                normalized.wav_path, "I like eating apples.", lang="en"
            )
    finally:
        normalized.cleanup()

    mapped = map_upstream_result(
        raw,
        request_id="01REALMODELTEST00000000001",
        text="I like eating apples.",
        language="en",
        mode=ResolvedAssessmentMode.SENTENCE,
        decoded_duration_seconds=normalized.duration_seconds,
        engine_name=loaded_real_adapter.engine_name,
        engine_version=loaded_real_adapter.engine_version,
    )
    assert mapped.reliable is False
    assert mapped.status.value == "uncertain"
    assert mapped.scores is None
    assert len(mapped.reasons) >= 1
    assert raw.get("acoustic_distance") is None


async def test_real_model_consented_fixtures_if_present(
    loaded_real_adapter: OpenPronounceAdapter,
    tmp_path: Path,
) -> None:
    """Validate structure and score ranges on consented real voice fixtures when provided."""
    fixture_dir = Path(os.environ.get("REAL_VOICE_FIXTURES_DIR", "tests/fixtures"))
    cases = [
        ("word_apple.wav", "apple", ResolvedAssessmentMode.WORD),
        ("phrase_good_morning.wav", "good morning", ResolvedAssessmentMode.PHRASE),
        ("sentence_apples.wav", "I like eating apples.", ResolvedAssessmentMode.SENTENCE),
    ]
    existing = [
        (fixture_dir / fname, text, mode)
        for fname, text, mode in cases
        if (fixture_dir / fname).is_file()
    ]
    if not existing:
        pytest.skip("No consented real voice fixtures found in REAL_VOICE_FIXTURES_DIR")

    settings = Settings(PRELOAD_MODELS=False, temp_dir=str(tmp_path))
    for audio_file, expected_text, mode in existing:
        normalized = await normalize_audio_file(
            str(audio_file),
            input_bytes=audio_file.stat().st_size,
            settings=settings,
        )
        try:
            with patch(
                "openpronounce.audio.text2speech",
                side_effect=AssertionError("TTS must never be called when acoustic=False"),
            ):
                raw = loaded_real_adapter.assess(normalized.wav_path, expected_text, lang="en")
        finally:
            normalized.cleanup()

        mapped = map_upstream_result(
            raw,
            request_id="01REALMODELTEST00000000002",
            text=expected_text,
            language="en",
            mode=mode,
            decoded_duration_seconds=normalized.duration_seconds,
            engine_name=loaded_real_adapter.engine_name,
            engine_version=loaded_real_adapter.engine_version,
        )
        assert mapped.engine.name == "openpronounce"
        assert mapped.engine.version == "1.1.0"
        if mapped.reliable:
            assert mapped.status.value == "assessed"
            assert mapped.scores is not None
            assert 0.0 <= mapped.scores.overall <= 100.0
        else:
            assert mapped.status.value == "uncertain"
            assert mapped.scores is None
