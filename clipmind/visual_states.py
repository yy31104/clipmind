"""Retain canonical visual states and derive a compact, uncapped preview."""
from __future__ import annotations

import shutil
import statistics
from dataclasses import dataclass, replace
from pathlib import Path

from . import media
from .media import Frame

PREVIEW_ALGORITHM = "adaptive-scene-v1"


@dataclass(frozen=True)
class BuildGroup:
    """A monotonic sequence in which each state adds visible text."""

    id: str
    frames: tuple[Frame, ...]

    @property
    def representative(self) -> Frame:
        return self.frames[-1]


def _characters(frame: Frame) -> set[str]:
    return {
        character
        for character in "".join(frame.lines)
        if not character.isspace()
    }


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


def derive_preview(
    frames: list[Frame],
    *,
    scene_floor: float = 24.0,
    activity_margin: float = 14.0,
    scene_ceiling: float = 32.0,
    latest_readability_ratio: float = 0.6,
) -> list[Frame]:
    """Choose one readable representative per content-driven visual scene.

    Progressive builds first collapse to their completed state. The scene
    threshold then adapts to the video's observed visual activity, but no frame
    count or per-duration budget is applied.
    """
    ordered = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    candidates = [
        frame
        for frame in ordered
        if frame.build_group_id is None
        or frame.build_position == frame.build_size - 1
    ]
    if len(candidates) < 2:
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
    for frame in candidates:
        if frame.dedupe_warning is not None:
            scenes.append([frame])
            continue
        if (
            not scenes
            or scenes[-1][-1].dedupe_warning is not None
            or media.hamming(scenes[-1][0].phash, frame.phash) >= threshold
        ):
            scenes.append([frame])
        else:
            scenes[-1].append(frame)

    preview = []
    for scene in scenes:
        richest = max(
            scene,
            key=lambda frame: (len(_characters(frame)), frame.timestamp, frame.index),
        )
        latest = scene[-1]
        preview.append(
            latest
            if len(_characters(latest))
            >= latest_readability_ratio * len(_characters(richest))
            else richest
        )
    return preview


def materialize_preview(frames: list[Frame], dest_dir: Path) -> list[Frame]:
    """Copy preview representatives while leaving canonical files untouched."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    preview: list[Frame] = []
    for frame in frames:
        dest = dest_dir / frame.path.name
        shutil.copy2(frame.path, dest)
        preview.append(replace(frame, path=dest))
    return preview
