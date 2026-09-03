"""Speech-to-text via MLX Whisper (runs on the Apple Silicon GPU, no API cost)."""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import Settings, settings


logger = logging.getLogger(__name__)


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()
    speaker: str | None = None


@dataclass
class Transcript:
    segments: list[Segment]
    language: str | None = None
    engine: str = "mlx-whisper"
    error: str | None = None
    diarization_error: str | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    @property
    def is_empty(self) -> bool:
        return not self.text

    @property
    def has_word_timing(self) -> bool:
        return bool(self.segments) and all(segment.words for segment in self.segments)

    @property
    def has_speakers(self) -> bool:
        return bool(self.segments) and all(segment.speaker for segment in self.segments)


def _mapping_words(value: dict) -> tuple[Word, ...]:
    return tuple(
        Word(
            float(item.get("start") or 0),
            float(item.get("end") or item.get("start") or 0),
            str(item.get("word") or item.get("text") or "").strip(),
            float(item["probability"]) if item.get("probability") is not None else None,
        )
        for item in value.get("words") or []
        if str(item.get("word") or item.get("text") or "").strip()
    )


@lru_cache(maxsize=1)
def available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception as exc:  # noqa: BLE001 - provider imports include native libraries
        logger.warning("MLX Whisper is unavailable (%s): %s", type(exc).__name__, exc)
        return False


@lru_cache(maxsize=1)
def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception as exc:  # noqa: BLE001 - provider imports include native libraries
        logger.warning("faster-whisper is unavailable (%s): %s", type(exc).__name__, exc)
        return False


def _transcribe_sync(audio: Path, config: Settings) -> Transcript:
    import mlx_whisper

    options = {
        "path_or_hf_repo": config.asr_model,
        "language": config.asr_language,
        "condition_on_previous_text": False,
        "verbose": None,
    }
    try:
        result = mlx_whisper.transcribe(
            str(audio), word_timestamps=True, **options
        )
    except TypeError:
        # Older mlx-whisper builds still produce valid segment timing.
        result = mlx_whisper.transcribe(str(audio), **options)
    segments = [
        Segment(
            float(s["start"]),
            float(s["end"]),
            (s.get("text") or "").strip(),
            _mapping_words(s),
        )
        for s in result.get("segments", [])
        if (s.get("text") or "").strip()
    ]
    return Transcript(segments=segments, language=result.get("language"))


@dataclass(frozen=True)
class MLXWhisperProvider:
    config: Settings
    name: str = "mlx-whisper"

    def available(self) -> bool:
        return available()

    async def transcribe(self, audio: Path | None) -> Transcript:
        return await _transcribe(audio, self.config)


_faster_models: dict[str, object] = {}
_faster_models_lock = threading.Lock()


@dataclass(frozen=True)
class FasterWhisperProvider:
    """Portable CPU/CUDA provider, imported only when installed."""

    config: Settings
    name: str = "faster-whisper"

    def available(self) -> bool:
        return faster_whisper_available()

    async def transcribe(self, audio: Path | None) -> Transcript:
        if audio is None:
            return Transcript([], engine=self.name, error="no audio track")
        if not self.available():
            return Transcript(
                [],
                engine=self.name,
                error="faster-whisper is unavailable; install the portable ASR extra",
            )
        try:
            return await asyncio.to_thread(self._transcribe_sync, audio)
        except Exception as exc:  # noqa: BLE001
            return Transcript(
                [],
                engine=self.name,
                error=f"speech transcription failed ({type(exc).__name__}); reprocess after checking the local log",
            )

    def _transcribe_sync(self, audio: Path) -> Transcript:
        from faster_whisper import WhisperModel

        model_name = self.config.faster_whisper_model
        with _faster_models_lock:
            model = _faster_models.get(model_name)
            if model is None:
                model = WhisperModel(model_name, device="auto", compute_type="auto")
                _faster_models[model_name] = model
        segments, info = model.transcribe(
            str(audio),
            language=self.config.asr_language,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        records = [
            Segment(
                float(segment.start),
                float(segment.end),
                segment.text.strip(),
                tuple(
                    Word(
                        float(word.start),
                        float(word.end),
                        word.word.strip(),
                        float(word.probability) if word.probability is not None else None,
                    )
                    for word in (segment.words or ())
                    if word.word.strip()
                ),
            )
            for segment in segments
            if segment.text.strip()
        ]
        return Transcript(
            records,
            language=getattr(info, "language", None),
            engine=self.name,
        )


async def _transcribe(audio: Path | None, config: Settings) -> Transcript:
    """Never raises: a failed transcript degrades the note, it does not kill it."""
    if audio is None:
        return Transcript(segments=[], error="no audio track")
    if not available():
        return Transcript(
            segments=[],
            error="mlx-whisper is unavailable; install dependencies and reprocess",
        )
    try:
        return await asyncio.to_thread(_transcribe_sync, audio, config)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
        return Transcript(
            segments=[],
            error=(
                f"speech transcription failed ({type(exc).__name__}); "
                "check the local log and reprocess"
            ),
        )


async def transcribe(audio: Path | None) -> Transcript:
    """Compatibility entry point using the process-wide default settings."""
    return await _transcribe(audio, settings)
