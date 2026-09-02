"""Retain canonical visual states and derive a compact, uncapped preview."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from .media import Frame


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


def derive_preview(frames: list[Frame]) -> list[Frame]:
    """Keep all ungrouped states and only the completed state of each build."""
    ordered = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    return [
        frame
        for frame in ordered
        if frame.build_group_id is None
        or frame.build_position == frame.build_size - 1
    ]


def materialize_preview(frames: list[Frame], dest_dir: Path) -> list[Frame]:
    """Copy preview representatives while leaving canonical files untouched."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    preview: list[Frame] = []
    for frame in frames:
        dest = dest_dir / frame.path.name
        shutil.copy2(frame.path, dest)
        preview.append(replace(frame, path=dest))
    return preview
