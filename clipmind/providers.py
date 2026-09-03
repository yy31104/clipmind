"""Small provider boundaries for platform-specific speech and OCR engines."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import importlib.util
import platform
from pathlib import Path
import sys
import threading
from typing import Protocol

from . import asr, ocr
from .asr import Transcript
from .config import Settings


class TranscriptProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def transcribe(self, audio: Path | None) -> Transcript: ...


class TextRecognizer(Protocol):
    name: str

    def available(self) -> bool: ...

    def read_text(self, image: Path) -> list[str]: ...


class SpeakerDiarizer(Protocol):
    name: str

    def available(self) -> bool: ...

    async def assign(self, audio: Path | None, transcript: Transcript) -> Transcript: ...


@dataclass(frozen=True)
class NoSpeakerDiarizer:
    """Honest default: preserve an extension point without inventing speakers."""

    name: str = "none"

    def available(self) -> bool:
        return False

    async def assign(self, audio: Path | None, transcript: Transcript) -> Transcript:
        return transcript


def assign_speakers(
    transcript: Transcript,
    turns: list[tuple[float, float, str]],
) -> Transcript:
    """Assign each ASR segment to the speaker with the greatest time overlap."""
    segments = []
    for segment in transcript.segments:
        overlaps = [
            (max(0.0, min(segment.end, end) - max(segment.start, start)), label)
            for start, end, label in turns
        ]
        overlap, speaker = max(overlaps, default=(0.0, None))
        segments.append(replace(segment, speaker=speaker if overlap > 0 else None))
    return replace(transcript, segments=segments, diarization_error=None)


_diarization_models: dict[str, object] = {}
_diarization_lock = threading.Lock()


@dataclass(frozen=True)
class PyannoteSpeakerDiarizer:
    """Optional local diarization provider; never required by the core path."""

    config: Settings
    name: str = "pyannote"

    def available(self) -> bool:
        return bool(
            self.config.diarization_token
            and importlib.util.find_spec("pyannote.audio") is not None
        )

    async def assign(self, audio: Path | None, transcript: Transcript) -> Transcript:
        if audio is None or not transcript.segments:
            return transcript
        if not self.available():
            return replace(
                transcript,
                diarization_error=(
                    "pyannote diarization is unavailable; install the diarization extra "
                    "and set HF_TOKEN"
                ),
            )
        try:
            return await asyncio.to_thread(self._assign_sync, audio, transcript)
        except Exception as exc:  # noqa: BLE001 - diarization is optional evidence
            return replace(
                transcript,
                diarization_error=f"speaker diarization failed ({type(exc).__name__})",
            )

    def _assign_sync(self, audio: Path, transcript: Transcript) -> Transcript:
        from pyannote.audio import Pipeline

        with _diarization_lock:
            pipeline = _diarization_models.get(self.config.diarization_model)
            if pipeline is None:
                try:
                    pipeline = Pipeline.from_pretrained(
                        self.config.diarization_model,
                        token=self.config.diarization_token,
                    )
                except TypeError:
                    pipeline = Pipeline.from_pretrained(
                        self.config.diarization_model,
                        use_auth_token=self.config.diarization_token,
                    )
                _diarization_models[self.config.diarization_model] = pipeline
        annotation = pipeline(str(audio))
        turns = [
            (float(turn.start), float(turn.end), str(label))
            for turn, _track, label in annotation.itertracks(yield_label=True)
        ]
        return assign_speakers(transcript, turns)


@dataclass(frozen=True)
class ProviderBundle:
    transcript: TranscriptProvider
    text: TextRecognizer
    diarization: SpeakerDiarizer = field(default_factory=NoSpeakerDiarizer)


def default_providers(config: Settings) -> ProviderBundle:
    transcript = _transcript_provider(config)
    text = _text_provider(config)
    diarization = (
        PyannoteSpeakerDiarizer(config)
        if config.diarization_provider == "pyannote"
        else NoSpeakerDiarizer()
    )
    return ProviderBundle(
        transcript=transcript,
        text=text,
        diarization=diarization,
    )


def _transcript_provider(config: Settings) -> TranscriptProvider:
    choice = config.asr_provider.casefold()
    if choice == "mlx":
        return asr.MLXWhisperProvider(config)
    if choice in {"faster-whisper", "faster_whisper"}:
        return asr.FasterWhisperProvider(config)
    if choice != "auto":
        raise ValueError(
            f"Unsupported ASR provider {config.asr_provider!r}; "
            "choose auto, mlx, or faster-whisper."
        )
    if _native_apple_silicon():
        return asr.MLXWhisperProvider(config)
    return asr.FasterWhisperProvider(config)


def _text_provider(config: Settings) -> TextRecognizer:
    choice = config.ocr_provider.casefold()
    if choice == "vision":
        return ocr.VisionTextRecognizer(config)
    if choice == "tesseract":
        return ocr.TesseractTextRecognizer(config)
    if choice != "auto":
        raise ValueError(
            f"Unsupported OCR provider {config.ocr_provider!r}; "
            "choose auto, vision, or tesseract."
        )
    if sys.platform == "darwin":
        return ocr.VisionTextRecognizer(config)
    return ocr.TesseractTextRecognizer(config)


def _native_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().casefold() in {
        "arm64",
        "aarch64",
    }
