"""Derive the small compatibility preview shown by the current UI.

Uniform "one screenshot every 5 seconds" produces mostly redundant images. The
canonical visual-state set is retained before this module collapses, scores and
selects a capped preview.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from . import media, ocr
from .config import settings
from .media import Frame


def _normalise(lines: list[str]) -> str:
    return "".join(ch for ch in "".join(lines) if not ch.isspace())


async def annotate(frames: list[Frame], ocr_semaphore: asyncio.Semaphore) -> str | None:
    """Attach OCR text to each frame. Returns an error string if OCR misbehaved.

    A frame that fails to OCR simply carries no text; losing on-screen text
    degrades the note but must never sink a job that still has good audio.
    """
    failures: list[str] = []

    async def run(frame: Frame) -> None:
        async with ocr_semaphore:
            try:
                lines = await asyncio.to_thread(ocr.read_text, frame.path)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{type(exc).__name__}: {exc}")
                return
        frame.lines = tuple(lines)
        frame.text = "\n".join(lines)

    await asyncio.gather(*(run(f) for f in frames))
    if not failures:
        return None
    return f"OCR failed on {len(failures)}/{len(frames)} frames: {failures[0]}"


def collapse_builds(frames: list[Frame], window: float = 6.0,
                    coverage: float = 0.8) -> list[Frame]:
    """Drop mid-build frames of a progressively revealed slide.

    These videos animate text in line by line, so the same slide shows up as
    several frames, each a near-subset of the next. Only the finished state is
    worth keeping.
    """
    partial: set[int] = set()
    for index, earlier in enumerate(frames):
        earlier_chars = set(_normalise(list(earlier.lines)))
        if not earlier_chars:
            continue
        for later in frames[index + 1:]:
            if later.timestamp - earlier.timestamp > window:
                break
            later_chars = set(_normalise(list(later.lines)))
            if len(later_chars) <= len(earlier_chars):
                continue
            if len(earlier_chars & later_chars) >= coverage * len(earlier_chars):
                partial.add(id(earlier))
                break
    return [f for f in frames if id(f) not in partial]


def score(frames: list[Frame]) -> None:
    """Novelty = characters this frame shows that no earlier frame showed."""
    seen: set[str] = set()
    previous_hash: int | None = None
    for frame in frames:
        chars = set(_normalise(list(frame.lines)))
        frame.novelty = len(chars - seen)
        seen |= chars
        visual = (
            media.hamming(frame.phash, previous_hash) if previous_hash is not None else 32
        )
        previous_hash = frame.phash

        if chars and frame.novelty == 0:
            # Text we have already read - a recurring title card or watermark.
            # The pixels may have moved, but nothing here is new information.
            frame.score = visual * 0.3
        elif not chars:
            # No text at all, so visual change is the only signal available.
            frame.score = float(visual)
        else:
            # Text carries most of the meaning, so weight it above pixel change,
            # which spikes on cuts that say nothing new.
            frame.score = frame.novelty * 2.0 + visual


def select(frames: list[Frame], limit: int | None = None) -> list[Frame]:
    """Highest-scoring frames, returned in chronological order."""
    limit = settings.max_keyframes if limit is None else limit
    if not frames:
        return []
    frames = collapse_builds(frames) or frames
    score(frames)
    ranked = sorted(frames, key=lambda f: f.score, reverse=True)[:limit]
    # The opening frame is context even when it scores low.
    if not any(f is frames[0] for f in ranked):
        ranked = ([frames[0]] + ranked)[:limit]
    return sorted(ranked, key=lambda f: f.timestamp)


async def promote(video: Path, chosen: list[Frame], dest_dir: Path) -> list[Frame]:
    """Re-grab the winners at full resolution and repoint them at the new files."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    for frame in chosen:
        stamp = f"{int(frame.timestamp // 60):02d}-{int(frame.timestamp % 60):02d}"
        # Two kept frames can share a second at 2 fps; don't let them collide.
        name = stamp
        suffix = 1
        while name in used:
            name = f"{stamp}-{suffix}"
            suffix += 1
        used.add(name)
        dest = dest_dir / f"{name}.jpg"
        try:
            await media.extract_still(video, frame.timestamp, dest)
            frame.path = dest
        except media.MediaError:
            try:
                shutil.copy2(frame.path, dest)
                frame.path = dest
            except OSError:
                pass
    return chosen
