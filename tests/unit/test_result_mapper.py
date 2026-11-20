"""Unit tests for OpenPronounce 1.1.0 -> public response result mapping."""

from __future__ import annotations

from openpronounce_api.schemas.requests import ResolvedAssessmentMode
from openpronounce_api.schemas.responses import AssessmentStatus
from openpronounce_api.services.result_mapper import map_upstream_result
from tests.conftest import (
    sample_upstream_reliable_result,
    sample_upstream_unreliable_result,
)


def test_map_reliable_result_preserves_scores_and_zero_based_words() -> None:
    raw = sample_upstream_reliable_result(duration=1.25)
    mapped = map_upstream_result(
        raw,
        request_id="01TESTREQ00000000000000001",
        text="I like eating apples.",
        language="en",
        mode=ResolvedAssessmentMode.SENTENCE,
        decoded_duration_seconds=1.2,
        engine_name="openpronounce",
        engine_version="1.1.0",
    )

    assert mapped.status == AssessmentStatus.ASSESSED
    assert mapped.reliable is True
    assert mapped.reasons == []
    assert mapped.scores is not None
    assert mapped.scores.overall == 82.1
    assert mapped.scores.accuracy == 78.0
    assert mapped.scores.fluency == 88.0
    assert mapped.scores.prosodic == 81.0
    assert mapped.transcription == "I LIKE EATING APPLES"
    assert len(mapped.words) == 2
    assert mapped.words[0].position == 0
    assert mapped.words[0].text == "I"
    assert mapped.words[0].mispronounced is False
    assert mapped.words[1].position == 3
    assert mapped.words[1].text == "apples"
    assert mapped.words[1].mispronounced is True
    assert mapped.words[1].missing is False
    assert mapped.words[1].expected_ipa == "æpəlz"
    assert mapped.words[1].heard_ipa == "æpəls"
    assert mapped.words[1].phonemes[0].expected == "z"
    assert mapped.words[1].phonemes[0].heard == "s"
    assert mapped.words[1].phonemes[0].start_seconds == 0.95
    assert mapped.words[1].phonemes[0].end_seconds == 1.12
    assert mapped.audio_quality.duration_seconds == 1.25
    assert mapped.engine.name == "openpronounce"
    assert mapped.engine.version == "1.1.0"
    assert "correct" not in mapped.model_dump()


def test_map_unreliable_result_hides_scores_and_redacts_raw_reasons() -> None:
    raw = sample_upstream_unreliable_result(duration=2.9)
    mapped = map_upstream_result(
        raw,
        request_id="01TESTREQ00000000000000002",
        text="Hello, I am a good designer.",
        language="en",
        mode=ResolvedAssessmentMode.SENTENCE,
        decoded_duration_seconds=2.9,
        engine_name="openpronounce",
        engine_version="1.1.0",
    )

    assert mapped.status == AssessmentStatus.UNCERTAIN
    assert mapped.reliable is False
    assert mapped.scores is None
    assert "missing_words" in mapped.reasons
    assert "audio_noisy" in mapped.reasons
    # Safe reason codes must not echo arbitrary raw target words from upstream reason text
    assert all("good" not in reason for reason in mapped.reasons)
    assert mapped.words[0].missing is True
    assert mapped.words[0].mispronounced is True
    assert mapped.words[0].phonemes == []
    assert len(mapped.extra_speech) == 1
    assert mapped.extra_speech[0].after_position == 4
    assert mapped.extra_speech[0].before_position == 5
    assert mapped.extra_speech[0].heard_ipa == "dɪv"


def test_map_errors_fallback_never_invents_timestamps_or_phonemes() -> None:
    raw = {
        "score": 71.0,
        "scores": None,
        "reliable": True,
        "reasons": [],
        "transcribe": "APPLE",
        "audio_quality": {"duration": 0.9, "issues": []},
        "differences": {
            "errors": [
                {
                    "position": 0,
                    "word": "apple",
                    "expected": "æpəl",
                    "actual": "æpə",
                    "confidence": 0.62,
                    "phones": [{"expected": "l", "heard": "", "confidence": 0.8}],
                }
            ]
        },
    }
    mapped = map_upstream_result(
        raw,
        request_id="01TESTREQ00000000000000003",
        text="apple",
        language="en",
        mode=ResolvedAssessmentMode.WORD,
        decoded_duration_seconds=0.9,
        engine_name="openpronounce",
        engine_version="1.1.0",
    )

    assert mapped.status == AssessmentStatus.ASSESSED
    assert mapped.scores is not None
    assert mapped.scores.overall == 71.0
    assert mapped.scores.accuracy is None
    assert mapped.scores.fluency is None
    assert mapped.scores.prosodic is None
    assert len(mapped.words) == 1
    word = mapped.words[0]
    assert word.start_seconds is None
    assert word.end_seconds is None
    assert word.expected_ipa == "æpəl"
    assert word.heard_ipa == "æpə"
    assert len(word.phonemes) == 1
    assert word.phonemes[0].start_seconds is None
    assert word.phonemes[0].end_seconds is None


def test_reliable_true_without_finite_score_degrades_to_uncertain() -> None:
    raw = {
        "score": None,
        "scores": None,
        "reliable": True,
        "reasons": [],
        "differences": {},
        "audio_quality": {"issues": []},
    }
    mapped = map_upstream_result(
        raw,
        request_id="01TESTREQ00000000000000004",
        text="apple",
        language="en",
        mode=ResolvedAssessmentMode.WORD,
        decoded_duration_seconds=1.0,
        engine_name="openpronounce",
        engine_version="1.1.0",
    )
    assert mapped.reliable is False
    assert mapped.status == AssessmentStatus.UNCERTAIN
    assert mapped.scores is None
    assert mapped.reasons == ["unreliable_assessment"]
