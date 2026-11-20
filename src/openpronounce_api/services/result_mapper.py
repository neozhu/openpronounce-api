"""Deterministic mapping from OpenPronounce 1.1.0 raw output to stable public API schemas."""

from __future__ import annotations

import math
from typing import Any

from openpronounce_api.schemas.requests import ResolvedAssessmentMode
from openpronounce_api.schemas.responses import (
    AssessmentStatus,
    AssessResponse,
    AudioQualitySummary,
    EngineMetadata,
    ExtraSpeechItem,
    PhonemeFeedback,
    ScoreBreakdown,
    WordFeedback,
)

_QUALITY_ISSUE_TO_REASON: dict[str, str] = {
    "too_short": "audio_too_short",
    "silent": "audio_silent",
    "too_quiet": "audio_too_quiet",
    "clipped": "audio_clipped",
    "noisy": "audio_noisy",
}


def _as_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _as_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _join_ipa(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value if value != "" else None
    if isinstance(value, (list, tuple)):
        joined = "".join(str(item) for item in value if item is not None)
        return joined if joined != "" else None
    return str(value)


def _normalize_reason(raw_reason: str) -> str:
    lowered = raw_reason.strip().lower()
    if "too short" in lowered or lowered == "too_short":
        return "audio_too_short"
    if "silent" in lowered:
        return "audio_silent"
    if "too quiet" in lowered or lowered == "too_quiet":
        return "audio_too_quiet"
    if "clipped" in lowered:
        return "audio_clipped"
    if "noisy" in lowered:
        return "audio_noisy"
    if lowered.startswith("not said") or "missing" in lowered:
        return "missing_words"
    if "does not match" in lowered or "mismatch" in lowered:
        return "text_mismatch"
    if "not in the sentence" in lowered or "extra" in lowered:
        return "extra_speech"
    return "unreliable_assessment"


def _map_reasons(raw_reasons: Any, quality_issues: list[str], reliable: bool) -> list[str]:
    ordered: list[str] = []

    def _add(code: str) -> None:
        if code not in ordered:
            ordered.append(code)

    if isinstance(raw_reasons, (list, tuple)):
        for item in raw_reasons:
            if isinstance(item, str) and item.strip():
                _add(_normalize_reason(item))

    for issue in quality_issues:
        mapped = _QUALITY_ISSUE_TO_REASON.get(issue)
        if mapped:
            _add(mapped)

    if not reliable and not ordered:
        _add("unreliable_assessment")

    return ordered


def _map_phonemes(raw_phones: Any) -> list[PhonemeFeedback]:
    if not isinstance(raw_phones, list):
        return []
    result: list[PhonemeFeedback] = []
    for item in raw_phones:
        if not isinstance(item, dict):
            continue
        expected = item.get("expected")
        if expected is None:
            continue
        heard_raw = item.get("heard")
        heard = str(heard_raw) if heard_raw is not None else None
        result.append(
            PhonemeFeedback(
                expected=str(expected),
                heard=heard,
                confidence=_as_optional_float(item.get("confidence")),
                start_seconds=_as_optional_float(item.get("start")),
                end_seconds=_as_optional_float(item.get("end")),
            )
        )
    return result


def _map_words(differences: dict[str, Any]) -> list[WordFeedback]:
    raw_words = differences.get("words")
    if isinstance(raw_words, list):
        mapped: list[WordFeedback] = []
        for idx, item in enumerate(raw_words):
            if not isinstance(item, dict):
                continue
            position = _as_optional_int(item.get("position"))
            if position is None or position < 0:
                position = idx
            mapped.append(
                WordFeedback(
                    text=str(item.get("word", "")),
                    position=position,
                    start_seconds=_as_optional_float(item.get("start")),
                    end_seconds=_as_optional_float(item.get("end")),
                    mispronounced=bool(item.get("mispronounced", False)),
                    missing=bool(item.get("missing", False)),
                    expected_ipa=_join_ipa(item.get("expected")),
                    heard_ipa=_join_ipa(item.get("actual")),
                    confidence=_as_optional_float(item.get("confidence")),
                    phonemes=_map_phonemes(item.get("phones")),
                )
            )
        return mapped

    # Fallback path when upstream does not provide full differences.words (e.g. rule-based path):
    # map differences.errors strictly without inventing timestamps or missing fields.
    raw_errors = differences.get("errors")
    if isinstance(raw_errors, list):
        mapped_errors: list[WordFeedback] = []
        for idx, item in enumerate(raw_errors):
            if not isinstance(item, dict):
                continue
            position = _as_optional_int(item.get("position"))
            if position is None or position < 0:
                position = idx
            mapped_errors.append(
                WordFeedback(
                    text=str(item.get("word", "")),
                    position=position,
                    start_seconds=_as_optional_float(item.get("start")),
                    end_seconds=_as_optional_float(item.get("end")),
                    mispronounced=True,
                    missing=bool(item.get("missing", False)),
                    expected_ipa=_join_ipa(item.get("expected")),
                    heard_ipa=_join_ipa(item.get("actual")),
                    confidence=_as_optional_float(item.get("confidence")),
                    phonemes=_map_phonemes(item.get("phones")),
                )
            )
        return mapped_errors

    return []


def _map_extra_speech(differences: dict[str, Any]) -> list[ExtraSpeechItem]:
    raw_extra = differences.get("extra")
    if not isinstance(raw_extra, list):
        return []
    items: list[ExtraSpeechItem] = []
    for entry in raw_extra:
        if not isinstance(entry, dict):
            continue
        items.append(
            ExtraSpeechItem(
                after_position=_as_optional_int(entry.get("after")),
                before_position=_as_optional_int(entry.get("before")),
                start_seconds=_as_optional_float(entry.get("start")),
                end_seconds=_as_optional_float(entry.get("end")),
                heard_ipa=_join_ipa(entry.get("heard")),
            )
        )
    return items


def _map_scores(raw_result: dict[str, Any]) -> ScoreBreakdown | None:
    raw_scores = raw_result.get("scores")
    if isinstance(raw_scores, dict):
        overall = _as_optional_float(raw_scores.get("total"))
        if overall is None:
            overall = _as_optional_float(raw_result.get("score"))
        if overall is None:
            return None
        return ScoreBreakdown(
            overall=round(overall, 2),
            accuracy=(
                round(val, 2)
                if (val := _as_optional_float(raw_scores.get("accuracy"))) is not None
                else None
            ),
            fluency=(
                round(val, 2)
                if (val := _as_optional_float(raw_scores.get("fluency"))) is not None
                else None
            ),
            prosodic=(
                round(val, 2)
                if (val := _as_optional_float(raw_scores.get("prosodic"))) is not None
                else None
            ),
        )

    fallback_overall = _as_optional_float(raw_result.get("score"))
    if fallback_overall is None:
        return None
    return ScoreBreakdown(overall=round(fallback_overall, 2))


def map_upstream_result(
    raw_result: dict[str, Any],
    *,
    request_id: str,
    text: str,
    language: str,
    mode: ResolvedAssessmentMode,
    decoded_duration_seconds: float,
    engine_name: str,
    engine_version: str,
) -> AssessResponse:
    """Map an OpenPronounce 1.1.0 dict into the stable public AssessResponse contract.

    Reliability policy:
    - ``reliable=True`` -> ``status="assessed"`` and populated ``scores``.
    - ``reliable=False`` -> ``status="uncertain"`` and ``scores=None``.
    - Never fabricates missing phonemes, timestamps, IPA, or pass/fail booleans.
    """
    differences = raw_result.get("differences")
    if not isinstance(differences, dict):
        differences = {}

    raw_quality = raw_result.get("audio_quality")
    if not isinstance(raw_quality, dict):
        raw_quality = {}

    quality_issues = [
        str(issue).strip().lower()
        for issue in (raw_quality.get("issues") or [])
        if isinstance(issue, str) and issue.strip()
    ]
    duration = _as_optional_float(raw_quality.get("duration"))
    if duration is None or duration < 0.0:
        duration = decoded_duration_seconds

    reliable = bool(raw_result.get("reliable", False))
    scores: ScoreBreakdown | None = None
    if reliable:
        scores = _map_scores(raw_result)
        if scores is None:
            # Cannot claim assessed if upstream supplied no finite score.
            reliable = False

    reasons = _map_reasons(raw_result.get("reasons"), quality_issues, reliable)
    status = AssessmentStatus.ASSESSED if reliable else AssessmentStatus.UNCERTAIN

    transcription_raw = raw_result.get("transcribe")
    if transcription_raw is None:
        transcription_raw = differences.get("transcribe")
    transcription = str(transcription_raw) if isinstance(transcription_raw, str) else None

    return AssessResponse(
        request_id=request_id,
        status=status,
        text=text,
        language=language,
        mode=mode,
        reliable=reliable,
        reasons=reasons,
        scores=scores,
        transcription=transcription,
        words=_map_words(differences),
        extra_speech=_map_extra_speech(differences),
        audio_quality=AudioQualitySummary(
            duration_seconds=round(duration, 3),
            issues=quality_issues,
        ),
        engine=EngineMetadata(
            name=engine_name,
            version=engine_version,
        ),
    )
