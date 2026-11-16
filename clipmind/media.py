"""ffmpeg wrappers plus perceptual-hash de-duplication of sampled frames."""
from __future__ import annotations

import shutil
import re
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
    # Actual image time, separate from the beginning of its visual interval.
    source_timestamp: float | None = None
    ocr_layout: tuple[dict, ...] = ()
    scene_id: str | None = None
    scene_boundary: bool = False
    scene_boundary_reason: str | None = None
    scene_change_score: float | None = None
    scroll_group_id: str | None = None
    scroll_position: int | None = None
    scroll_size: int | None = None
    content_hint: str = "visual"
    change_kind: str = "uncertain"
    change_reason: str = ""


async def _ffmpeg(args: list[str]) -> bytes:
    if not shutil.which("ffmpeg"):
        raise MediaError("ffmpeg is not installed (brew install ffmpeg)")
    code, _, err = await subprocesses.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    )
    if code:
        raise MediaError(f"ffmpeg failed: {err.decode(errors='replace').strip()[:400]}")
    return err


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
    diagnostics = await _ffmpeg([
        "-loglevel", "info",
        "-i", str(video),
        # Select original decoded frames, rather than re-timing nearest frames
        # through fps. showinfo preserves each selected image's real local PTS.
        "-vf", f"setpts=PTS-STARTPTS,select='isnan(prev_selected_t)+gt(floor(t*{fps}+0.000001),floor(prev_selected_t*{fps}+0.000001))',scale={width}:-2,showinfo",
        "-fps_mode", "vfr",
        "-q:v", "4", str(dest_dir / "s_%05d.jpg"),
    ])
    files = sorted(dest_dir.glob("s_*.jpg"))
    times = [float(value) for value in re.findall(rb"\bn:\s*\d+.*?\bpts_time:([0-9.eE+-]+)", diagnostics or b"")]
    if diagnostics and len(times) != len(files):
        raise MediaError("FFmpeg image timestamps do not match its sampled frames")
    return [Frame(index=i, timestamp=times[i] if times else i / fps, path=p,
                  source_timestamp=times[i] if times else None) for i, p in enumerate(files)]


async def extract_still(video: Path, timestamp: float, dest: Path) -> Path:
    """Full-resolution grab, used only for frames that made the final cut."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _ffmpeg(["-ss", f"{max(timestamp, 0):.3f}", "-i", str(video),
                   "-frames:v", "1", "-q:v", "2", str(dest)])
    return dest


# Identifies how canonical membership was decided, so a later evaluation can
# tell "the algorithm changed" apart from "the source bytes changed".
DEDUPE_ALGORITHM = "dhash-stationary-strokes-settle-v5"


def _detail_signature(path: Path) -> np.ndarray:
    """Keep local strokes that disappear in the 9-by-8 global hash."""
    with Image.open(path) as image:
        return np.asarray(image.convert("L").resize((320, 180), Image.Resampling.BILINEAR), dtype=np.int16)


def _persistent_detail(before: np.ndarray, current: np.ndarray,
                       following: np.ndarray | None) -> bool:
    # Added strokes on a stable page mostly move in one contrast direction.
    # Test local tiles so a speaker moving beside the page cannot mask a build.
    difference = current - before
    positive = (difference >= 24).reshape(10, 18, 10, 32).sum(axis=(1, 3))
    negative = (difference <= -24).reshape(10, 18, 10, 32).sum(axis=(1, 3))
    total = positive + negative
    tiles = (total >= 12) & (np.maximum(positive, negative) >= 0.8 * total)
    changed = (np.abs(difference) >= 24) & np.repeat(np.repeat(tiles, 18, axis=0), 32, axis=1)
    if np.count_nonzero(changed) < changed.size * 0.001:
        return False
    # The activated strokes must themselves stay still at the next sample,
    # rather than merely stay different from an older scene. The last sample
    # is still retained conservatively.
    return following is None or np.count_nonzero(changed & (np.abs(following - current) <= 12)) >= 0.8 * np.count_nonzero(changed)


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
    last_detail: np.ndarray | None = None
    following_detail: np.ndarray | None = None
    representative_detail: np.ndarray | None = None
    for position, frame in enumerate(frames):
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
            last_detail = None
            following_detail = None
            continue
        try:
            detail = following_detail if following_detail is not None else _detail_signature(frame.path)
        except (OSError, ValueError):
            # The global hash already read this frame. If the optional detail
            # pass cannot read it again, keep the global comparison available.
            detail = None
        try:
            following_detail = _detail_signature(frames[position + 1].path) if position + 1 < len(frames) else None
        except (OSError, ValueError):
            following_detail = None
        if detail is None:
            frame.dedupe_warning = "Local detail comparison unavailable; frame retained"
            kept.append(frame)
            last_hash = last_detail = None
            continue
        if last_hash is not None:
            try:
                duplicate = hamming(frame.phash, last_hash) <= threshold
                if duplicate and last_detail is not None and detail is not None:
                    duplicate = not _persistent_detail(last_detail, detail, following_detail)
            except Exception as exc:  # noqa: BLE001 - fail open to preserve evidence
                frame.dedupe_warning = (
                    f"{type(exc).__name__}: perceptual comparison failed; frame retained"
                )
                kept.append(frame)
                last_hash = None
                last_detail = None
                continue
            if duplicate:
                if kept:
                    kept[-1].observed_sample_count += 1
                    kept[-1].stable_duration = max(
                        kept[-1].stable_duration,
                        frame.timestamp - kept[-1].timestamp,
                    )
                    # A first sample can contain both sides of a crossfade.
                    # Let the near-duplicate interval settle briefly while
                    # retaining its onset and the image's separate real PTS.
                    if (detail is not None and representative_detail is not None
                            and frame.timestamp - kept[-1].timestamp <= 2.0
                            and np.mean(np.abs(detail - representative_detail)) > 0.25):
                        kept[-1].index, kept[-1].path = frame.index, frame.path
                        kept[-1].phash = frame.phash
                        kept[-1].source_timestamp = frame.source_timestamp
                        representative_detail = detail
                continue
        kept.append(frame)
        last_hash = frame.phash
        last_detail = detail
        representative_detail = detail
    return kept
