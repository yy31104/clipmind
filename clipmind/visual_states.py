"""Retain canonical visual states and derive a compact, uncapped preview."""
from __future__ import annotations

import asyncio
import re
import shutil
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from . import media, ocr
from .media import Frame
from .providers import TextRecognizer

PREVIEW_ALGORITHM = "adaptive-scene-text-v1"


@dataclass(frozen=True)
class BuildGroup:
    """A monotonic sequence in which each state adds visible text."""

    id: str
    frames: tuple[Frame, ...]

    @property
    def representative(self) -> Frame:
        return self.frames[-1]


@dataclass(frozen=True)
class ScrollGroup:
    """Adjacent states that preserve a text viewport while replacing edges."""

    id: str
    frames: tuple[Frame, ...]


def _characters(frame: Frame) -> set[str]:
    return {
        character
        for character in "".join(frame.lines)
        if not character.isspace()
    }


def _terms(frame: Frame) -> set[str]:
    return set(
        re.findall(
            r"[a-z0-9_]+|[\u3400-\u9fff]",
            " ".join(frame.lines).casefold(),
        )
    )


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]")


def _tokens(text: str) -> list[str]:
    """Information-bearing tokens: one per CJK ideograph, one per latin word.

    Normalises width and case first so that full-width digits, latin letters
    and casing differences between OCR and ASR do not read as novel text.
    Returns a list, not a set: repeated terms carry weight.
    """
    return _TOKEN_RE.findall(unicodedata.normalize("NFKC", text).casefold())


def _spoken_tokens(
    spoken_intervals: tuple[tuple[float, float, str], ...],
    start: float,
    end: float,
    padding: float,
) -> list[str]:
    return [
        token
        for interval_start, interval_end, text in spoken_intervals
        if interval_end > start - padding and interval_start < end + padding
        for token in _tokens(text)
    ]


def transcript_alignment(
    frame: Frame,
    spoken_intervals: Iterable[tuple[float, float, str]],
    *,
    end: float | None = None,
    padding: float = 2.0,
) -> tuple[int, int, float]:
    """Measure how much on-screen text the nearby speech does not already carry.

    Burned-in captions repeat what is being said, so the transcript already
    holds that information. A slide, document or code pane shows text nobody is
    reading aloud, and the frame is the only place it exists. This returns
    ``(ocr_char_count, novelty_char_count, overlap_ratio)`` over tokens rather
    than raw characters, and compares as a multiset so a term repeated on
    screen but spoken once is not treated as fully covered.

    It describes information novelty, not importance: a silent video makes
    every frame novel, and code identifiers are novel because nobody says them
    out loud. Callers decide what that is worth.
    """
    visible = _tokens(" ".join(frame.lines))
    total = sum(len(token) for token in visible)
    if not visible:
        return 0, 0, 0.0

    start = frame.timestamp
    spoken = Counter(
        _spoken_tokens(tuple(spoken_intervals), start, end if end is not None else start, padding)
    )
    novel_chars = 0
    for token, count in Counter(visible).items():
        unmatched = count - min(count, spoken.get(token, 0))
        novel_chars += unmatched * len(token)
    overlap = (total - novel_chars) / total if total else 0.0
    return total, novel_chars, round(overlap, 4)


def annotate_transcript_alignment(
    frames: list[Frame],
    spoken_intervals: Iterable[tuple[float, float, str]],
) -> None:
    """Attach the alignment measurements to each frame, in place."""
    intervals = tuple(spoken_intervals)
    ordered = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    for position, frame in enumerate(ordered):
        end = (
            ordered[position + 1].timestamp
            if position + 1 < len(ordered)
            else frame.timestamp
        )
        total, novel, overlap = transcript_alignment(frame, intervals, end=end)
        frame.ocr_char_count = total
        frame.transcript_novelty = novel
        frame.transcript_overlap = overlap


def _extends(
    earlier: Frame,
    later: Frame,
    *,
    window: float,
    coverage: float,
) -> bool:
    earlier_chars = _characters(earlier)
    later_chars = _characters(later)
    return (
        bool(earlier_chars)
        and later.timestamp - earlier.timestamp <= window
        and len(later_chars) > len(earlier_chars)
        and len(earlier_chars & later_chars) >= coverage * len(earlier_chars)
    )


def _caption_like(
    frame: Frame,
    spoken_intervals: tuple[tuple[float, float, str], ...],
    *,
    padding: float = 2.0,
    coverage: float = 0.6,
) -> bool:
    visible = _characters(frame)
    if not visible:
        return False
    nearby = {
        character
        for start, end, text in spoken_intervals
        if end > frame.timestamp - padding and start < frame.timestamp + padding
        for character in text
        if not character.isspace()
    }
    return len(visible & nearby) >= coverage * len(visible)


def _replaces_visible_text(
    earlier: Frame,
    later: Frame,
    spoken_intervals: tuple[tuple[float, float, str], ...],
    *,
    minimum_characters: int = 5,
    overlap: float = 0.35,
) -> bool:
    earlier_chars = _characters(earlier)
    later_chars = _characters(later)
    smaller = min(len(earlier_chars), len(later_chars))
    if smaller < minimum_characters:
        return False
    earlier_terms = _terms(earlier)
    later_terms = _terms(later)
    smaller_terms = min(len(earlier_terms), len(later_terms))
    if not smaller_terms:
        return False
    if len(earlier_terms & later_terms) >= overlap * smaller_terms:
        return False
    return not (
        _caption_like(earlier, spoken_intervals)
        and _caption_like(later, spoken_intervals)
    )



async def annotate(
    frames: list[Frame],
    ocr_semaphore: asyncio.Semaphore,
    recognizer: TextRecognizer | None = None,
) -> str | None:
    """Attach OCR text to each frame. Returns an error string if OCR misbehaved.

    A frame that fails to OCR simply carries no text; losing on-screen text
    degrades the note but must never sink a job that still has good audio.
    """
    failures: list[str] = []

    async def run(frame: Frame) -> None:
        frame.ocr_warning = None
        frame.ocr_layout = ()
        async with ocr_semaphore:
            try:
                rich_reader = getattr(recognizer, "recognize", None)
                if rich_reader is not None:
                    result = await asyncio.to_thread(rich_reader, frame.path)
                    lines = list(result.lines)
                    frame.ocr_layout = tuple(
                        block.public() for block in result.blocks
                    )
                else:
                    reader = recognizer.read_text if recognizer is not None else ocr.read_text
                    lines = await asyncio.to_thread(reader, frame.path)
            except Exception as exc:  # noqa: BLE001
                frame.ocr_warning = (
                    f"{type(exc).__name__}: OCR failed; image retained"
                )
                failures.append(frame.ocr_warning)
                return
        frame.lines = tuple(lines)
        frame.text = "\n".join(lines)

    await asyncio.gather(*(run(f) for f in frames))
    if not failures:
        return None
    return f"OCR failed on {len(failures)}/{len(frames)} frames: {failures[0]}"


def retain_all(
    frames: list[Frame],
    dest_dir: Path,
    *,
    evidence_sources: dict[int, Path] | None = None,
) -> list[Frame]:
    """Move every deduped candidate into canonical storage in time order."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    retained = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    for position, frame in enumerate(retained):
        seconds = max(frame.timestamp, 0.0)
        stamp = f"{int(seconds // 60):02d}-{int(seconds % 60):02d}"
        dest = dest_dir / f"{stamp}-{position:05d}.jpg"
        source = (evidence_sources or {}).get(frame.index, frame.path)
        shutil.move(source, dest)
        frame.path = dest
    return retained


def group_progressive_builds(
    frames: list[Frame],
    *,
    window: float = 6.0,
    coverage: float = 0.8,
) -> list[BuildGroup]:
    """Label adjacent monotonic OCR builds without removing canonical states.

    A replacement or disappearance breaks the chain. This is deliberately
    conservative: compact preview must never erase information that does not
    survive into the next state.
    """
    ordered = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    for frame in ordered:
        frame.build_group_id = None
        frame.build_position = None
        frame.build_size = None

    chains: list[list[Frame]] = []
    current: list[Frame] = []
    for frame in ordered:
        if current and _extends(
            current[-1], frame, window=window, coverage=coverage
        ):
            current.append(frame)
            continue
        if len(current) > 1:
            chains.append(current)
        current = [frame]
    if len(current) > 1:
        chains.append(current)

    groups: list[BuildGroup] = []
    for number, chain in enumerate(chains, start=1):
        group_id = f"build-{number:05d}"
        size = len(chain)
        for position, frame in enumerate(chain):
            frame.build_group_id = group_id
            frame.build_position = position
            frame.build_size = size
        groups.append(BuildGroup(group_id, tuple(chain)))
    return groups


def _scrolls(
    earlier: Frame,
    later: Frame,
    *,
    window: float,
    minimum_terms: int,
    minimum_overlap: float,
    maximum_overlap: float,
) -> bool:
    earlier_terms = _terms(earlier)
    later_terms = _terms(later)
    smaller = min(len(earlier_terms), len(later_terms))
    if smaller < minimum_terms or later.timestamp - earlier.timestamp > window:
        return False
    shared = len(earlier_terms & later_terms) / smaller
    return (
        minimum_overlap <= shared < maximum_overlap
        and len(earlier_terms - later_terms) >= 2
        and len(later_terms - earlier_terms) >= 2
    )


def group_scroll_sequences(
    frames: list[Frame],
    *,
    window: float = 6.0,
    minimum_terms: int = 8,
    minimum_overlap: float = 0.4,
    maximum_overlap: float = 0.9,
) -> list[ScrollGroup]:
    """Label conservative OCR-overlap scroll sequences without dropping states."""
    ordered = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    for frame in ordered:
        frame.scroll_group_id = None
        frame.scroll_position = None
        frame.scroll_size = None

    chains: list[list[Frame]] = []
    current: list[Frame] = []
    for frame in ordered:
        if current and _scrolls(
            current[-1],
            frame,
            window=window,
            minimum_terms=minimum_terms,
            minimum_overlap=minimum_overlap,
            maximum_overlap=maximum_overlap,
        ):
            current.append(frame)
            continue
        if len(current) > 1:
            chains.append(current)
        current = [frame]
    if len(current) > 1:
        chains.append(current)

    groups = []
    for number, chain in enumerate(chains, start=1):
        group_id = f"scroll-{number:05d}"
        for position, frame in enumerate(chain):
            frame.scroll_group_id = group_id
            frame.scroll_position = position
            frame.scroll_size = len(chain)
        groups.append(ScrollGroup(group_id, tuple(chain)))
    return groups


def annotate_content_hints(frames: list[Frame]) -> None:
    """Attach conservative, non-semantic content hints for humans and agents."""
    code_markers = re.compile(
        r"(?:\b(?:def|class|import|from|return|const|function|async|await)\b|"
        r"[{}();]|</?[a-z][^>]*>|[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)",
        re.IGNORECASE,
    )
    for frame in frames:
        text = "\n".join(frame.lines)
        marker_count = len(code_markers.findall(text))
        if not text.strip():
            frame.content_hint = "visual"
        elif frame.transcript_overlap >= 0.65 and frame.ocr_char_count < 120:
            frame.content_hint = "caption"
        elif marker_count >= 2:
            frame.content_hint = "code_ui"
        elif frame.ocr_char_count >= 80 or len(frame.lines) >= 8:
            frame.content_hint = "document_slide"
        else:
            frame.content_hint = "textual"


def derive_preview(
    frames: list[Frame],
    *,
    spoken_intervals: Iterable[tuple[float, float, str]] = (),
    scene_floor: float = 24.0,
    activity_margin: float = 14.0,
    scene_ceiling: float = 32.0,
    latest_readability_ratio: float = 0.6,
    replacement_visual_floor: float = 6.0,
    novelty_floor: int = 40,
    novelty_overlap: float = 0.5,
) -> list[Frame]:
    """Choose one readable representative per content-driven visual scene.

    Progressive builds first collapse to their completed state. The scene
    threshold then adapts to the video's observed visual activity, but no frame
    count or per-duration budget is applied.
    """
    speech = tuple(spoken_intervals)
    ordered = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    annotate_content_hints(ordered)
    for position, frame in enumerate(ordered):
        frame.scene_id = None
        frame.scene_boundary = False
        frame.scene_boundary_reason = None
        frame.scene_change_score = None
        if position and frame.dedupe_warning is None and ordered[position - 1].dedupe_warning is None:
            frame.scene_change_score = round(
                media.hamming(ordered[position - 1].phash, frame.phash) / 64.0,
                4,
            )
    candidates = [
        frame
        for frame in ordered
        if frame.build_group_id is None
        or frame.build_position == frame.build_size - 1
    ]
    if len(candidates) < 2:
        for position, frame in enumerate(ordered):
            frame.scene_id = "scene-00001"
            frame.scene_boundary = position == 0
            frame.scene_boundary_reason = "initial" if position == 0 else None
        return candidates

    comparable_pairs = [
        (earlier, later)
        for earlier, later in zip(candidates, candidates[1:])
        if earlier.dedupe_warning is None and later.dedupe_warning is None
    ]
    activity = statistics.median(
        media.hamming(earlier.phash, later.phash)
        for earlier, later in comparable_pairs
    ) if comparable_pairs else scene_floor
    threshold = min(scene_ceiling, max(scene_floor, activity + activity_margin))

    scenes: list[list[Frame]] = []
    scene_reasons: list[str] = []
    for frame in candidates:
        reason = None
        if not scenes:
            reason = "initial"
        elif frame.dedupe_warning is not None:
            reason = "hash_warning"
        elif scenes[-1][-1].dedupe_warning is not None:
            reason = "hash_recovery"
        elif (
            media.hamming(scenes[-1][-1].phash, frame.phash)
            > replacement_visual_floor
            and _replaces_visible_text(scenes[-1][-1], frame, speech)
        ):
            reason = "text_replacement"
        elif media.hamming(scenes[-1][0].phash, frame.phash) >= threshold:
            reason = "visual_change"
        if reason is not None:
            scenes.append([frame])
            scene_reasons.append(reason)
        else:
            scenes[-1].append(frame)

    for number, (scene, reason) in enumerate(zip(scenes, scene_reasons), start=1):
        scene_id = f"scene-{number:05d}"
        for position, frame in enumerate(scene):
            frame.scene_id = scene_id
            frame.scene_boundary = position == 0
            frame.scene_boundary_reason = reason if position == 0 else None
    group_scenes = {
        frame.build_group_id: frame.scene_id
        for frame in candidates
        if frame.build_group_id and frame.scene_id
    }
    for frame in ordered:
        if frame.scene_id is None and frame.build_group_id in group_scenes:
            frame.scene_id = group_scenes[frame.build_group_id]

    preview = []
    for scene in scenes:
        richest = max(
            scene,
            key=lambda frame: (
                len(_characters(frame)),
                frame.observed_sample_count,
                frame.stable_duration,
                frame.timestamp,
                frame.index,
            ),
        )
        latest = scene[-1]
        preview.append(
            latest
            if len(_characters(latest))
            >= latest_readability_ratio * len(_characters(richest))
            and latest.observed_sample_count * 2 >= richest.observed_sample_count
            else richest
        )

    # A document shown briefly inside one long visual scene would otherwise be
    # represented by whatever else that scene chose. Any state carrying enough
    # text the speech never covers earns its own slot, unless a selected state
    # already shows substantially the same terms.
    selected = {frame.index for frame in preview}
    for frame in candidates:
        if frame.index in selected or frame.transcript_novelty < novelty_floor:
            continue
        terms = _terms(frame)
        if not terms:
            continue
        if any(
            len(terms & _terms(chosen)) >= novelty_overlap * len(terms)
            for chosen in preview
        ):
            continue
        preview.append(frame)
        selected.add(frame.index)

    return sorted(preview, key=lambda frame: (frame.timestamp, frame.index))


def materialize_preview(frames: list[Frame], dest_dir: Path) -> list[Frame]:
    """Copy preview representatives while leaving canonical files untouched."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    preview: list[Frame] = []
    for frame in frames:
        dest = dest_dir / frame.path.name
        shutil.copy2(frame.path, dest)
        preview.append(replace(frame, path=dest))
    return preview
