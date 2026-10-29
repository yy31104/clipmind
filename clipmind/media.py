"""ffmpeg wrappers plus perceptual-hash de-duplication of sampled frames."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import subprocesses
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
    # Still-image collections have source order but no measured video time.
    # ``timestamp`` stays zero for compatibility; this field marks that it is
    # an ordinal image record, never a synthetic second in a made-up video.
    source_image_index: int | None = None
    # LivePhoto samples retain their owning page and the measured local clip
    # position, without inventing a playback timeline for the whole post.
    source_motion_timestamp: float | None = None


async def _ffmpeg(args: list[str]) -> None:
    if not shutil.which("ffmpeg"):
        raise MediaError("ffmpeg is not installed (brew install ffmpeg)")
    code, _, err = await subprocesses.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    )
    if code:
        raise MediaError(f"ffmpeg failed: {err.decode(errors='replace').strip()[:400]}")


async def extract_audio(video: Path, dest: Path) -> Path | None:
    """16 kHz mono WAV, the format Whisper wants. None if the video is silent."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        await _ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                       "-c:a", "pcm_s16le", str(dest)])
    except MediaError as exc:
        if "does not contain any stream" in str(exc):
            return None
        raise
    if not dest.is_file() or not dest.stat().st_size:
        raise MediaError("Audio extraction completed without a readable output file.")
    return dest


async def sample_frames(
    video: Path,
    dest_dir: Path,
    *,
    fps: float | None = None,
    width: int | None = None,
    strict: bool = False,
) -> list[Frame]:
    """Uniformly sample frames at a requested width."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    fps = settings.sample_fps if fps is None else fps
    width = settings.sample_width if width is None else width
    decode_options = ["-xerror"] if strict else []
    # Strict short motion clips retain the final partial sampling interval:
    # e.g. 2.67 seconds at 2 fps includes the real clip's 2.5-second state.
    sample_filter = f"fps={fps}" + (":round=up" if strict else "")
    await _ffmpeg([
        *decode_options, "-i", str(video),
        "-vf", f"{sample_filter},scale={width}:-2",
        "-q:v", "4", "-pix_fmt", "yuvj420p", str(dest_dir / "s_%05d.jpg"),
    ])
    files = sorted(dest_dir.glob("s_*.jpg"))
    if not files:
        # Very short clips/animations can end before the fps filter emits its
        # first sample. Retain the real first decoded frame rather than
        # publishing a successful pack with no visual evidence.
        first = dest_dir / "s_00001.jpg"
        await _ffmpeg([
            *decode_options, "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2",
            "-q:v", "4", "-pix_fmt", "yuvj420p", str(first),
        ])
        if not first.is_file() or not first.stat().st_size:
            raise MediaError("No readable visual frame was found in this media.")
        files = [first]
    return [Frame(index=i, timestamp=i / fps, path=p) for i, p in enumerate(files)]


async def extract_still(video: Path, timestamp: float, dest: Path) -> Path:
    """Full-resolution grab, used only for frames that made the final cut."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _ffmpeg(["-ss", f"{max(timestamp, 0):.3f}", "-i", str(video),
                   "-frames:v", "1", "-q:v", "2", "-pix_fmt", "yuvj420p", str(dest)])
    return dest


# Identifies how canonical membership was decided, so a later evaluation can
# tell "the algorithm changed" apart from "the source bytes changed".
DEDUPE_ALGORITHM = "dhash-local-change-v2"


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


def _comparison_pixels(path: Path) -> np.ndarray:
    # Compare at the sampled resolution. A second thumbnail would erase exactly
    # the small numbers, operators and chart marks this guard must preserve.
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.int16)


def _same_sample_content(earlier: np.ndarray, later: np.ndarray) -> bool:
    """Veto hash collisions using local changes, tolerating tiny codec noise.

    This is a sampling safeguard, not proof of semantic equivalence. Selection
    after OCR handles captions/portraits; canonical sampling must not assume a
    small changed area is unimportant merely because its 64-bit hash is close.
    """
    if earlier.shape != later.shape:
        return False
    difference = np.abs(earlier - later).max(axis=2)
    if not np.any(difference):
        return True
    if float(difference.mean()) >= 4:
        return False
    # Shift the second tiling to catch marks crossing a tile boundary.
    for offset in (0, 8):
        values = difference[offset:, offset:]
        if not values.size:
            continue
        padded = np.pad(values, ((0, (-values.shape[0]) % 16),
                                 (0, (-values.shape[1]) % 16)))
        blocks = padded.reshape(padded.shape[0] // 16, 16,
                                padded.shape[1] // 16, 16)
        changed = (blocks >= 12).sum(axis=(1, 3))
        strong = (blocks >= 32).sum(axis=(1, 3))
        if np.any(changed >= 4) or np.any(strong >= 2):
            return False
    return True


def dedupe(frames: list[Frame], threshold: int | None = None) -> list[Frame]:
    """Drop only hash-near samples without material local pixel changes."""
    threshold = settings.dedupe_threshold if threshold is None else threshold
    kept: list[Frame] = []
    last_hash: int | None = None
    last_pixels: np.ndarray | None = None
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
            last_pixels = None
            continue
        if last_hash is not None:
            try:
                duplicate = hamming(frame.phash, last_hash) <= threshold
                if duplicate:
                    if last_pixels is None:
                        last_pixels = _comparison_pixels(kept[-1].path)
                    pixels = _comparison_pixels(frame.path)
                    duplicate = _same_sample_content(last_pixels, pixels)
            except Exception as exc:  # noqa: BLE001 - fail open to preserve evidence
                frame.dedupe_warning = (
                    f"{type(exc).__name__}: perceptual comparison failed; frame retained"
                )
                kept.append(frame)
                last_hash = None
                last_pixels = None
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
        last_pixels = None
    return kept
