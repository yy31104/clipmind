"""Retain the untruncated visual-state set before preview selection."""
from __future__ import annotations

import shutil
from pathlib import Path

from .media import Frame


def retain_all(frames: list[Frame], dest_dir: Path) -> list[Frame]:
    """Move every deduped candidate into canonical storage in time order."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    retained = sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    for position, frame in enumerate(retained):
        seconds = max(frame.timestamp, 0.0)
        stamp = f"{int(seconds // 60):02d}-{int(seconds % 60):02d}"
        dest = dest_dir / f"{stamp}-{position:05d}.jpg"
        shutil.move(frame.path, dest)
        frame.path = dest
    return retained
