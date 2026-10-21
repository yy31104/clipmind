"""Select positive reading material, rather than representative video scenes.

These are deliberately limited structural heuristics, not semantic recognition.
The original Evidence Pack remains the place to inspect unclassified visuals.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .media import Frame
from .visual_states import transcript_alignment


CONTENT_ALGORITHM = "independent-visual-content-v1"
_LETTERS = re.compile(r"[a-z0-9_\u3400-\u9fff]", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•●▪]|\d{1,2}[.)、]|[一二三四五六七八九十][、.])\s*")
_CODE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class)\s+[a-z_]\w*(?:\([^\n]*\))?\s*:"
    r"|^\s*(?:from\s+[a-z_][\w.]*\s+import\s+|import\s+[a-z_][\w.]*)"
    r"|^\s*(?:const|let|var)\s+[a-z_]\w*\s*="
    r"|^\s*[a-z_]\w*(?:\.[a-z_]\w*)*\s*=\s*(?:[-+]?\d|[\"'\[({]|[a-z_]\w*(?:\.[a-z_]\w*)*\s*\()",
    re.I,
)
_CALL = re.compile(r"^\s*[a-z_]\w*(?:\.[a-z_]\w*)*\([^\n]*\)\s*;?\s*$", re.I)
_COMMAND = re.compile(r"^\s*(?:python3?|uv|pip3?|git|npm|pnpm|curl)\s+[\w./-]+(?:\s+\S+)*\s*$", re.I)


@dataclass(frozen=True)
class _Line:
    text: str
    x: float
    y: float
    width: float
    height: float


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _text_size(text: str) -> int:
    return len(_LETTERS.findall(_normal(text)))


def _layout(frame: Frame) -> list[_Line]:
    result = []
    for block in frame.ocr_layout:
        try:
            confidence = float(block.get("confidence", 0))
            box = block["bbox"]
            text = _normal(str(block.get("text") or ""))
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            x, y, width, height = map(float, box)
            if (not all(math.isfinite(v) for v in (confidence, x, y, width, height))
                    or confidence < .6 or not 0 <= x < 1 or not 0 <= y < 1
                    or not 0 < width <= 1 - x + .000001
                    or not 0 < height <= 1 - y + .000001
                    or _text_size(text) < 3):
                continue
            result.append(_Line(text, x, y, width, height))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return result


def _pixels(frame: Frame) -> np.ndarray | None:
    try:
        with Image.open(frame.path) as image:
            image.thumbnail((320, 320))
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    except (OSError, ValueError):
        return None


def _canvas(pixels: np.ndarray) -> tuple[bool, np.ndarray]:
    values = pixels.astype(np.uint16) // 32
    buckets = values[:, :, 0] * 64 + values[:, :, 1] * 8 + values[:, :, 2]
    counts = np.bincount(buckets.ravel(), minlength=512)
    dominant = int(counts.argmax())
    background = buckets == dominant
    # A page or dark chart panel needs a dominant, low-saturation background.
    colour = np.median(pixels[background], axis=0)
    reading = bool(background.mean() >= .55 and float(colour.max() - colour.min()) <= 45)
    ink = np.abs(pixels.astype(np.float32) - colour).max(axis=2) >= 28
    return reading, ink


def _runs(values: np.ndarray, minimum: int) -> list[tuple[int, int]]:
    positions = np.flatnonzero(values)
    if not len(positions):
        return []
    # Bridge a few pixels removed where a perpendicular grid line crosses.
    starts, ends = [int(positions[0])], []
    for earlier, later in zip(positions, positions[1:]):
        if later - earlier > 5:
            ends.append(int(earlier))
            starts.append(int(later))
    ends.append(int(positions[-1]))
    return [(a, b) for a, b in zip(starts, ends)
            if b - a + 1 >= minimum and values[a:b+1].mean() >= .75]


def _chart(ink: np.ndarray) -> bool:
    """Require thin intersecting axes/grid and actual marks inside that area."""
    height, width = ink.shape
    if min(height, width) < 24:
        return False
    up, down, left, right = [np.ones_like(ink) for _ in range(4)]
    up[4:] = ink[:-4]
    down[:-4] = ink[4:]
    left[:, 4:] = ink[:, :-4]
    right[:, :-4] = ink[:, 4:]
    horizontal, vertical = ink & ~up & ~down, ink & ~left & ~right
    hs = [(y, a, b) for y in range(height)
          for a, b in _runs(horizontal[y], max(24, int(width * .3)))]
    vs = [(x, a, b) for x in range(width)
          for a, b in _runs(vertical[:, x], max(20, int(height * .25)))]
    for y, x1, x2 in hs:
        for x, y1, y2 in vs:
            if not (x1 - 5 <= x <= x2 + 5 and y1 - 5 <= y <= y2 + 5):
                continue
            # Inspect the larger side of the axis intersection, excluding borders.
            xa, xb = (x + 5, x2 - 4) if x2 - x >= x - x1 else (x1 + 4, x - 5)
            ya, yb = (y1 + 4, y - 5) if y - y1 >= y2 - y else (y + 5, y2 - 4)
            if xb - xa < width * .2 or yb - ya < height * .15:
                continue
            interior = ink[max(0, ya):min(height, yb), max(0, xa):min(width, xb)]
            if interior.size and 12 <= interior.sum() and .001 <= interior.mean() <= .25:
                return True
    return False


def _caption(line: _Line, frame: Frame, spoken: tuple) -> bool:
    if not spoken or line.y < .65 or line.height > .18:
        return False
    total, novel, overlap = transcript_alignment(
        Frame(frame.index, frame.timestamp, frame.path, lines=(line.text,)), spoken)
    return bool(total and overlap >= .8 and novel <= 3)


def _aligned_reading(lines: list[_Line]) -> bool:
    for anchor in lines:
        column = sorted((line for line in lines if abs(line.x - anchor.x) <= .06
                         and line.width >= .08 and line.height <= .12), key=lambda line: line.y)
        separated = []
        for line in column:
            if not separated or line.y >= separated[-1].y + max(.012, separated[-1].height * .65):
                separated.append(line)
        if len(separated) < 2:
            continue
        span = separated[-1].y + separated[-1].height - separated[0].y
        total = sum(_text_size(line.text) for line in separated)
        bullets = sum(bool(_BULLET.match(line.text)) for line in separated)
        if (span >= .12 and min(line.y for line in separated) < .65
                and ((len(separated) >= 3 and total >= 30) or (bullets >= 2 and total >= 24))):
            return True
    return False


def select_content_frames(frames: list[Frame], spoken_intervals=()) -> list[Frame]:
    """Keep only positive code, reading-layout or plotted-data evidence.

    No first/last/context image is added. Empty OCR is assessed visually; an
    unreadable image or arbitrary OCR volume is not positive content evidence.
    Unavailable speech does not force a context image into the selection.
    """
    spoken = tuple(spoken_intervals)
    selected = []
    for frame in sorted(frames, key=lambda item: (item.timestamp, item.index)):
        lines = _layout(frame)
        pixels = _pixels(frame)
        if pixels is None:
            continue
        canvas, ink = _canvas(pixels)
        code_lines = [line.text for line in lines if line.y < .7]
        if canvas:
            code_lines.extend(_normal(line) for line in frame.lines)
        if any(_CODE.search(text) or _CALL.fullmatch(text) or _COMMAND.fullmatch(text)
               for text in code_lines):
            selected.append(frame)
            continue
        body = [line for line in lines if not _caption(line, frame, spoken)]
        if _aligned_reading(body) or (canvas and _chart(ink)):
            selected.append(frame)
    return selected
