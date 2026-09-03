"""Small provider boundaries for platform-specific speech and OCR engines."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
    return ProviderBundle(
        transcript=asr.MLXWhisperProvider(config),
        text=ocr.VisionTextRecognizer(config),
    )
