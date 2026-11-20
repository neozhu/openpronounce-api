"""Isolated adapter around OpenPronounce 1.1.0 read-speech assessment (CPU-only, no TTS)."""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class PronunciationEngine(Protocol):
    """Adapter protocol allowing dependency injection of a fake engine in fast tests."""

    engine_name: str
    engine_version: str

    @property
    def is_ready(self) -> bool:
        """Return True when the engine models are loaded and ready to serve requests."""
        ...

    def load(self) -> None:
        """Initialize scoring models synchronously (called during app startup)."""
        ...

    def assess(self, audio_path: str, text: str, lang: str = "en") -> dict[str, Any]:
        """Run read-speech assessment on a normalized 16 kHz mono audio file."""
        ...


class OpenPronounceAdapter:
    """Production CPU-only adapter for ``openpronounce==1.1.0``.

    Enforces:
    - ``OPENPRONOUNCE_DEVICE=cpu``
    - ``acoustic=False`` (no TTS synthesis, no DTW synthetic reference)
    - ``prosody=False`` (no pitch/energy contour extraction; trained ``prosodic`` score retained)
    - ``transcription=True`` (retains ASR word transcript for mismatch/missing-word diagnostics)
    """

    engine_name: str = "openpronounce"
    engine_version: str = "1.1.0"

    def __init__(self, *, hf_home: str | None = None, hf_hub_offline: bool = False) -> None:
        self._hf_home = hf_home
        self._hf_hub_offline = hf_hub_offline
        self._ready = False
        self._last_error: str | None = None
        self._load_audio_fn: Any = None
        self._assess_fn: Any = None

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def load(self) -> None:
        """Warm and cache the OpenPronounce CPU checkpoints without any TTS calls."""
        os.environ["OPENPRONOUNCE_DEVICE"] = "cpu"
        if self._hf_home:
            os.environ["HF_HOME"] = self._hf_home
        if self._hf_hub_offline:
            os.environ["HF_HUB_OFFLINE"] = "1"

        try:
            import openpronounce
            from openpronounce import assess, gop, load_audio, phones, scorer, speech

            self.engine_version = getattr(openpronounce, "__version__", self.engine_version)

            # Warm phone recognizer, English ASR recognizer, and English GOP/scorer JSON models.
            # This does not invoke TTS (gTTS/Piper/Kokoro).
            phones._load_model()
            speech._get_processor("en")
            speech._get_model_ctc("en")
            gop.load_model("en")
            gop.load_missing_model("en")
            scorer.load_scorer("en")

            self._load_audio_fn = load_audio
            self._assess_fn = assess
            self._ready = True
            self._last_error = None
            logger.info(
                "OpenPronounce engine initialized",
                extra={
                    "engine_version": self.engine_version,
                    "device": "cpu",
                    "offline": self._hf_hub_offline,
                },
            )
        except Exception as exc:
            self._ready = False
            self._last_error = type(exc).__name__
            logger.exception("Failed to initialize OpenPronounce engine")
            raise

    def assess(self, audio_path: str, text: str, lang: str = "en") -> dict[str, Any]:
        """Load waveform and invoke openpronounce.assess with locked v1 flags."""
        if not self._ready or self._load_audio_fn is None or self._assess_fn is None:
            raise RuntimeError("OpenPronounce engine is not initialized")

        waveform = self._load_audio_fn(audio_path)
        result: dict[str, Any] = self._assess_fn(
            waveform,
            text,
            lang=lang,
            acoustic=False,
            prosody=False,
            transcription=True,
        )
        return result
