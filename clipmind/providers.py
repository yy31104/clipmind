"""Small provider boundaries for platform-specific speech and OCR engines."""
from __future__ import annotations

from dataclasses import dataclass
import platform
from pathlib import Path
import sys
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


@dataclass(frozen=True)
class ProviderBundle:
    transcript: TranscriptProvider
    text: TextRecognizer


def default_providers(config: Settings) -> ProviderBundle:
    transcript = _transcript_provider(config)
    text = _text_provider(config)
    return ProviderBundle(
        transcript=transcript,
        text=text,
    )


def _transcript_provider(config: Settings) -> TranscriptProvider:
    if config.asr_provider == "mlx":
        return asr.MLXWhisperProvider(config)
    if config.asr_provider in {"faster-whisper", "faster_whisper"}:
        return asr.FasterWhisperProvider(config)
    if _native_apple_silicon():
        return asr.MLXWhisperProvider(config)
    return asr.FasterWhisperProvider(config)


def _text_provider(config: Settings) -> TextRecognizer:
    if config.ocr_provider == "vision":
        return ocr.VisionTextRecognizer(config)
    if config.ocr_provider == "tesseract":
        return ocr.TesseractTextRecognizer(config)
    if sys.platform == "darwin":
        return ocr.VisionTextRecognizer(config)
    return ocr.TesseractTextRecognizer(config)


def _native_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().casefold() in {
        "arm64",
        "aarch64",
    }
