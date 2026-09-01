"""Speech-to-text via MLX Whisper (runs on the Apple Silicon GPU, no API cost)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .config import settings


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    language: str | None = None
    engine: str = "mlx-whisper"
    error: str | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    @property
    def is_empty(self) -> bool:
        return not self.text


def available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _transcribe_sync(audio: Path) -> Transcript:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=settings.asr_model,
        language=settings.asr_language,
        condition_on_previous_text=False,  # avoids runaway repetition on short clips
        verbose=None,
    )
    segments = [
        Segment(float(s["start"]), float(s["end"]), (s.get("text") or "").strip())
        for s in result.get("segments", [])
        if (s.get("text") or "").strip()
    ]
    return Transcript(segments=segments, language=result.get("language"))


async def transcribe(audio: Path | None) -> Transcript:
    """Never raises: a failed transcript degrades the note, it does not kill it."""
    if audio is None:
        return Transcript(segments=[], error="no audio track")
    if not available():
        return Transcript(segments=[], error="mlx-whisper not installed")
    try:
        return await asyncio.to_thread(_transcribe_sync, audio)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
        return Transcript(segments=[], error=f"{type(exc).__name__}: {exc}")
