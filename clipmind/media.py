"""ffmpeg wrappers plus perceptual-hash de-duplication of sampled frames."""
from __future__ import annotations

import shutil
import re
from bisect import bisect_left
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import subprocesses
from .config import settings
from .screen_edits import SettledEdits


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
    source_stream_index: int | None = None
    # Optional rank decision derived from the completed canonical timeline.
    information_boundary: bool | None = None


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
    mappings = set(re.findall(rb"(?m)^[ \t]+Stream #0:(\d+) -> #0:\d+", diagnostics or b""))
    stream = int(mappings.pop()) if len(mappings) == 1 else None
    return [Frame(index=i, timestamp=times[i] if times else i / fps, path=p,
                  source_timestamp=times[i] if times else None,
                  source_stream_index=stream) for i, p in enumerate(files)]


async def extract_still(video: Path, timestamp: float, dest: Path) -> Path:
    """Full-resolution grab, used only for frames that made the final cut."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _ffmpeg(["-ss", f"{max(timestamp, 0):.3f}", "-i", str(video),
                   "-frames:v", "1", "-q:v", "2", str(dest)])
    return dest


# Identifies how canonical membership was decided, so a later evaluation can
# tell "the algorithm changed" apart from "the source bytes changed".
DEDUPE_ALGORITHM = "dhash-stationary-strokes-native-onset-v8"


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


def _clamp_stable_durations(kept: list[Frame]) -> None:
    for current, following in zip(kept, kept[1:]):
        current.stable_duration = min(current.stable_duration, max(0.0, following.timestamp - current.timestamp))


def _restore_fade_onsets(samples: list[tuple[float, Path]], kept: list[Frame]) -> None:
    """Time a persistent reveal from its visible lead-in and keep the settled still."""
    positions = {time: index for index, (time, _) in enumerate(samples)}
    if len(positions) != len(samples):
        return

    @lru_cache(maxsize=10)
    def detail(position: int) -> np.ndarray:
        return _detail_signature(samples[position][1])

    previous = previous_capture = -1.0
    for frame in kept:
        position = positions[frame.timestamp]
        original_onset = frame.timestamp
        anchor = position - 4
        if (not frame.dedupe_warning and anchor >= 0 and position + 2 < len(samples)
                and previous < samples[anchor][0] < original_onset
                and original_onset - samples[anchor][0] <= 2.3):
            try:
                before, settled = detail(anchor), detail(position + 2)
                difference = settled - before
                region = (np.abs(difference) >= 30) & (np.abs(settled - detail(position)) <= 8)
                if anchor:
                    region &= np.abs(before - detail(anchor - 1)) <= 8
                if np.count_nonzero(region) >= 60:
                    expected = difference[region]
                    candidate = None
                    previous_blend = 0.0
                    for earlier in range(anchor + 1, position):
                        progress = (detail(earlier)[region] - before[region]) / expected
                        blend = float(np.median(progress))
                        coherent = (np.abs(progress - blend) <= 0.22) & (progress >= 0.12) & (progress <= 1.15)
                        if not (0.22 <= blend <= 1.10 and np.mean(coherent) >= 0.74):
                            candidate, previous_blend = None, 0.0
                            continue
                        if blend < previous_blend - 0.12:
                            candidate = None
                        if candidate is None and samples[earlier][0] > previous_capture:
                            candidate = samples[earlier][0]
                        previous_blend = blend
                    if candidate is not None:
                        frame.timestamp = candidate
            except (OSError, ValueError):
                # Keep the original timestamp if there are no readable witnesses.
                pass
        previous = original_onset
        previous_capture = max(original_onset, frame.source_timestamp or original_onset)


def _time_ranges(ranges: list[tuple[float, float]]) -> str:
    """Match short, ordered decoder windows without scanning every window per frame."""
    if len(ranges) == 1:
        start, end = ranges[0]
        return f"between(t,{start:.9f},{end:.9f})"
    split = len(ranges) // 2
    return f"if(lt(t,{ranges[split][0]:.9f}),{_time_ranges(ranges[:split])},{_time_ranges(ranges[split:])})"


async def refine_native_onsets(
    video: Path, samples: list[tuple[float, Path]], kept: list[Frame], dest: Path,
) -> None:
    """Check source frames just before a sampled change without replacing its screenshot."""
    if len(kept) < 2:
        return
    streams = {frame.source_stream_index for frame in kept}
    if len(streams) != 1 or None in streams:
        return
    stream = streams.pop()
    positions = {time: index for index, (time, _) in enumerate(samples)}
    windows = []
    for previous, frame in zip(kept, kept[1:]):
        start = frame.timestamp
        previous_capture = max(previous.timestamp, previous.source_timestamp or previous.timestamp)
        lower = max(previous_capture + 1e-8, start - 0.151)
        if lower < start - 1e-7 and start in positions and positions[start] and not frame.dedupe_warning:
            windows.append((lower, start - 1e-7, frame, positions[start]))
    if not windows:
        return
    graph = dest / "select.txt"
    source = ["-loglevel", "info", "-i", str(video)]
    capture = [str(graph), "-map", "[lead]", "-fps_mode", "vfr", "-q:v", "4", str(dest / "n_%06d.jpg")]
    try:
        dest.mkdir(parents=True, exist_ok=True)
        graph.write_text(
            f"[0:{stream}]setpts=PTS-STARTPTS,select='"
            + _time_ranges([(lower, upper) for lower, upper, _, _ in windows])
            + "',scale=640:-2,showinfo[lead]\n", encoding="ascii",
        )
        try:
            diagnostics = await _ffmpeg([*source, "-/filter_complex", *capture])
        except MediaError as error:
            if "Unrecognized option '/filter_complex'" not in str(error):
                raise
            diagnostics = await _ffmpeg([*source, "-filter_complex_script", *capture])
    except (MediaError, OSError):
        # Decoder refinements are optional. Matching selection still uses the coarse samples.
        return
    base = re.search(rb"config in time_base:\s*(\d+)/(\d+)", diagnostics)
    pts = re.findall(rb"\bn:\s*\d+\s+pts:\s*(-?\d+)", diagnostics)
    files = sorted(dest.glob("n_*.jpg"))
    if not base or not int(base[2]) or len(pts) != len(files):
        return
    numerator, denominator = map(int, base.groups())
    lead_times = [int(value) * numerator / denominator for value in pts]
    if lead_times != sorted(lead_times):
        return

    for lower, upper, frame, position in windows:
        first = bisect_left(lead_times, lower)
        stop = bisect_left(lead_times, upper)
        if first == stop:
            continue
        try:
            before = _detail_signature(samples[position - 1][1])
            after = _detail_signature(samples[position][1])
            changed = np.abs(after - before) >= 30
            if np.count_nonzero(changed) < 50:
                continue
            confirmed = frame.timestamp
            for prior in range(stop - 1, first - 1, -1):
                error = np.abs(_detail_signature(files[prior]) - after)
                if (confirmed - lead_times[prior] > 0.082 or np.mean(error[changed] <= 16) < 0.88
                        or np.mean(error > 30) > 0.028):
                    break
                confirmed = lead_times[prior]
            frame.timestamp = round(confirmed, 9)
        except (OSError, ValueError):
            continue
    _clamp_stable_durations(kept)


def _restore_settled_edits(
    frames: list[Frame], samples: list[tuple[float, Path]], kept: list[Frame],
    coarse_onsets: list[float],
) -> list[Frame]:
    """Split a near-duplicate interval only when both appearances persist."""
    positions = {time: index for index, (time, _) in enumerate(samples)}
    if len(positions) != len(samples):
        return kept
    starts = [positions[time] for time in coarse_onsets]
    captured_at = {path: time for time, path in samples}
    detector = SettledEdits(samples)
    restored = []
    for number, (start, stop, original) in enumerate(zip(starts, starts[1:] + [len(frames)], kept)):
        if original.dedupe_warning:
            restored.append(original)
            continue
        next_onset = kept[number + 1].timestamp if number + 1 < len(kept) else float("inf")
        previous_onset = original.timestamp
        original_capture = original.source_timestamp
        if original_capture is None:
            original_capture = captured_at[original.path]
        positions_in_interval = [start]
        for position in range(start + 2, stop - 1):
            time, _ = samples[position]
            # Allow a short dwell on both sides of a recovered edit.
            if time - previous_onset < 0.85 or next_onset - time < 0.85:
                continue
            if detector.at(position, replacing_representative=time <= original_capture):
                positions_in_interval.append(position)
                previous_onset = time
        if len(positions_in_interval) == 1:
            restored.append(original)
            continue
        for left, right in zip(positions_in_interval, positions_in_interval[1:] + [stop]):
            frame = original if left == start else frames[left]
            if left == start and right < stop and original_capture >= samples[right][0]:
                still = frames[right - 1]
                frame.index, frame.path, frame.phash = still.index, still.path, still.phash
                frame.source_timestamp = still.source_timestamp
            frame.observed_sample_count = right - left
            frame.stable_duration = samples[right - 1][0] - samples[left][0]
            restored.append(frame)
    return restored


def dedupe(frames: list[Frame], threshold: int | None = None) -> list[Frame]:
    """Drop near-duplicates; retain and mark frames that cannot be compared."""
    threshold = settings.dedupe_threshold if threshold is None else threshold
    samples = [(frame.timestamp, frame.path) for frame in frames]
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
    coarse_onsets = [frame.timestamp for frame in kept]
    _restore_fade_onsets(samples, kept)
    kept = _restore_settled_edits(frames, samples, kept, coarse_onsets)
    _clamp_stable_durations(kept)
    return kept
