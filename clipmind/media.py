"""ffmpeg wrappers plus perceptual-hash de-duplication of sampled frames."""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .config import settings


class MediaError(RuntimeError):
    code = "media_processing_failed"
    user_message = "Local media processing failed."
    action = "Check that FFmpeg is installed, then reprocess this video."


@dataclass
class Frame:
    index: int
    timestamp: float
    path: Path
    phash: int = 0
    text: str = ""
    lines: tuple[str, ...] = ()
    dedupe_warning: str | None = None
    # Filled in by visual_states.annotate_transcript_alignment; describes how
    # much of this frame's text the nearby speech does not already carry.
    ocr_char_count: int = 0
    transcript_novelty: int = 0
    transcript_overlap: float = 0.0
    ocr_warning: str | None = None
    build_group_id: str | None = None
    build_position: int | None = None
    build_size: int | None = None
    # Safe measurements and grouping labels. None of these decide canonical
    # membership; they enrich the pack and the derived preview only.
    observed_sample_count: int = 1
    stable_duration: float = 0.0
    ocr_layout: tuple[dict, ...] = ()
    scene_id: str | None = None
    scene_boundary: bool = False
    scene_boundary_reason: str | None = None
    scene_change_score: float | None = None
    scroll_group_id: str | None = None
    scroll_position: int | None = None
    scroll_size: int | None = None
    content_hint: str = "visual"


async def _ffmpeg(args: list[str]) -> None:
    if not shutil.which("ffmpeg"):
        raise MediaError("ffmpeg is not installed (brew install ffmpeg)")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode:
        raise MediaError(f"ffmpeg failed: {err.decode(errors='replace').strip()[:400]}")


async def extract_audio(video: Path, dest: Path) -> Path | None:
    """16 kHz mono WAV, the format Whisper wants. None if the video is silent."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        await _ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                       "-c:a", "pcm_s16le", str(dest)])
    except MediaError as exc:
        if "does not contain any stream" in str(exc) or "Output file" in str(exc):
            return None
        raise
    return dest if dest.exists() and dest.stat().st_size > 1024 else None


async def sample_frames(
    video: Path,
    dest_dir: Path,
    *,
    fps: float | None = None,
    width: int | None = None,
) -> list[Frame]:
    """Uniformly sample frames at a requested width."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    fps = settings.sample_fps if fps is None else fps
    width = settings.sample_width if width is None else width
    await _ffmpeg([
        "-i", str(video),
        "-vf", f"fps={fps},scale={width}:-2",
        "-q:v", "4", str(dest_dir / "s_%05d.jpg"),
    ])
    files = sorted(dest_dir.glob("s_*.jpg"))
    return [Frame(index=i, timestamp=i / fps, path=p) for i, p in enumerate(files)]


async def extract_still(video: Path, timestamp: float, dest: Path) -> Path:
    """Full-resolution grab, used only for frames that made the final cut."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _ffmpeg(["-ss", f"{max(timestamp, 0):.3f}", "-i", str(video),
                   "-frames:v", "1", "-q:v", "2", str(dest)])
    return dest


def dhash(path: Path, size: int = 8) -> int:
    """Difference hash: robust to compression, sensitive to real content change."""
    with Image.open(path) as im:
        small = im.convert("L").resize((size + 1, size), Image.LANCZOS)
        arr = np.asarray(small, dtype=np.int16)
    bits = (arr[:, 1:] > arr[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dedupe(frames: list[Frame], threshold: int | None = None) -> list[Frame]:
    """Drop near-duplicates; retain and mark frames that cannot be compared."""
    threshold = settings.dedupe_threshold if threshold is None else threshold
    kept: list[Frame] = []
    last_hash: int | None = None
    for frame in frames:
        frame.dedupe_warning = None
        frame.observed_sample_count = 1
        frame.stable_duration = 0.0
        try:
            frame.phash = dhash(frame.path)
        except Exception as exc:  # noqa: BLE001 - fail open to preserve evidence
            frame.dedupe_warning = (
                f"{type(exc).__name__}: perceptual hash failed; frame retained"
            )
            kept.append(frame)
            last_hash = None
            continue
        if last_hash is not None:
            try:
                duplicate = hamming(frame.phash, last_hash) <= threshold
            except Exception as exc:  # noqa: BLE001 - fail open to preserve evidence
                frame.dedupe_warning = (
                    f"{type(exc).__name__}: perceptual comparison failed; frame retained"
                )
                kept.append(frame)
                last_hash = None
                continue
            if duplicate:
                if kept:
                    kept[-1].observed_sample_count += 1
                    kept[-1].stable_duration = max(
                        kept[-1].stable_duration,
                        frame.timestamp - kept[-1].timestamp,
                    )
                continue
        kept.append(frame)
        last_hash = frame.phash
    return kept
