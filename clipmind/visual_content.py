"""Select positive reading material, rather than representative video scenes.

These are deliberately limited structural heuristics, not semantic recognition.
The original Evidence Pack remains the place to inspect unclassified visuals.
"""
from __future__ import annotations

import hashlib
import keyword
import math
import re
import unicodedata
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .media import Frame
from . import reading_views
from .visual_states import transcript_alignment


CONTENT_ALGORITHM = "independent-visual-content-v4"
_LETTERS = re.compile(r"[a-z0-9_\u3400-\u9fff]", re.I)
_SPEECH_TOKENS = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]")
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
_BARE_ASSIGNMENT = re.compile(r"^\s*([a-z_]\w*)\s*=(?!=)", re.I)
_CLOSING = re.compile(r"下期[见見]|下次[见見]|感[谢謝](?:[观觀]看|收看)|[谢謝][谢謝].*[观觀]看|thanks.*watching|see you (?:next|soon)", re.I)
_PROMO = re.compile(
    r"[关關]注(?:我|我[们們]|公[众眾][号號])|[关關]注[\s·•:：。,，.-]|[订訂][阅閱]|[点點][赞讚]|[转轉][发發]"
    r"|[历歷]史回[测測]|不[构構]成投[资資]建[议議]"
    r"|subscribe|follow\s+(?:me|us|for more)|like and share|not financial advice", re.I)


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


def _code_line(text: str) -> bool:
    assignment = _BARE_ASSIGNMENT.match(text)
    # OCR can turn prose such as "and - 68 sessions" into an assignment.
    # Only a bare reserved target is invalid; member access, imports and valid
    # case-sensitive identifiers must retain their existing syntax evidence.
    if assignment and keyword.iskeyword(assignment.group(1)):
        return False
    return bool(_CODE.search(text) or _CALL.fullmatch(text) or _COMMAND.fullmatch(text))


def _layout(frame: Frame, *, minimum_confidence: float = .6) -> list[_Line]:
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
                    or confidence < minimum_confidence or not 0 <= x < 1 or not 0 <= y < 1
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


def _chart_region(ink: np.ndarray) -> _Line | None:
    """Require thin intersecting axes/grid and actual marks inside that area."""
    height, width = ink.shape
    if min(height, width) < 24:
        return None
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
                # Include a margin for ticks/labels outside the detected axes.
                left = max(0., min(x1, x) / width - .025)
                top = max(0., min(y1, y) / height - .025)
                right = min(1., max(x2, x) / width + .025)
                bottom = min(1., max(y2, y) / height + .025)
                return _Line("", left, top, right - left, bottom - top)
    return None


def _caption(line: _Line, frame: Frame, spoken: tuple) -> bool:
    if not spoken or line.y < .70 or line.height > .13:
        return False
    total, novel, overlap = transcript_alignment(
        Frame(frame.index, frame.timestamp, frame.path, lines=(line.text,)), spoken)
    if not (total and overlap == 1 and novel == 0):
        return False
    visible = _SPEECH_TOKENS.findall(_normal(line.text).casefold())
    local = [token for start, end, text in spoken
             if end > frame.timestamp - 2 and start < frame.timestamp + 2
             for token in _SPEECH_TOKENS.findall(_normal(text).casefold())]
    # A bag of words can reverse a warning or swap two values. Caption removal
    # needs the actual ordered phrase, not just the same tokens nearby.
    return bool(visible and any(local[index:index + len(visible)] == visible
                               for index in range(len(local) - len(visible) + 1)))


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


def _reading_material(lines: list[_Line], canvas: bool) -> bool:
    if not _aligned_reading(lines):
        return False
    # A few names, logos or browser labels on a photograph are not a document
    # paragraph. Preserve instruction lists and numerical panels, including
    # those filmed on a monitor rather than on a flat page background.
    substantial = any((_text_size(line.text) >= 40
                       and len(re.findall(r"[a-z_]+", line.text, re.I)) >= 8)
                      or len(re.findall(r"[\u3400-\u9fff]", line.text)) >= 18
                      for line in lines)
    return (canvas or substantial or sum(_text_size(line.text) for line in lines) >= 160
            or sum(bool(_BULLET.match(line.text)) for line in lines) >= 2
            or sum(bool(re.search(r"\d", line.text)) for line in lines) >= 3)


def _text_raster(ink: np.ndarray) -> bool:
    """Find several separated rows of glyph-like ink, without trusting OCR.

    This is a conservative document fallback, not a semantic classifier. A
    person's silhouette, large title or a few straight rules do not satisfy
    it. It also works for scripts not recognized by the configured OCR model.
    The caller must first establish a flat reading canvas.
    """
    height, width = ink.shape
    counts = ink.sum(axis=1)
    occupied = counts >= max(3, width * .012)
    edges = np.diff(np.r_[False, occupied, False].astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    rows = []
    for top, bottom in zip(starts, ends):
        if not 2 <= bottom - top <= max(3, height * .065):
            continue
        region = ink[top:bottom]
        columns = region.any(axis=0)
        positions = np.flatnonzero(columns)
        if not len(positions) or positions[-1] - positions[0] < width * .22:
            continue
        span = columns[positions[0]:positions[-1] + 1]
        # Text has multiple separated glyphs/words; axes, borders and a
        # silhouette contain solid contiguous spans instead.
        gaps = np.count_nonzero(np.diff(np.r_[True, span, True].astype(np.int8)) == -1)
        if gaps >= 4 and .06 <= region.mean() <= .45:
            rows.append((int(top), int(bottom)))
    return (len(rows) >= 4 and rows[0][0] < height * .45
            and rows[-1][1] - rows[0][0] >= height * .18)


def _diagram_region(ink: np.ndarray) -> _Line | None:
    """Retain connected outlined nodes even without chart axes or OCR text."""
    height, width = ink.shape
    if min(height, width) < 32:
        return None
    edges = []
    for y in range(height):
        for left, right in _runs(ink[y], max(16, int(width * .12))):
            if right - left <= width * .75:
                edges.append((y, left, right))
    # Busy imagery cannot establish this simple diagram form. Bound the pair
    # search independently of resolution and avoid treating dense ink as boxes.
    if len(edges) > 100:
        return None
    boxes = []
    for top, left, right in edges:
        for bottom, a, b in edges:
            if not height * .07 <= bottom - top <= height * .40:
                continue
            if abs(left - a) > 2 or abs(right - b) > 2:
                continue
            if (ink[top:bottom + 1, max(0, left - 1):left + 3].any(axis=1).mean() < .9
                    or ink[top:bottom + 1, max(0, right - 2):right + 2].any(axis=1).mean() < .9):
                continue
            if ink[top + 3:bottom - 2, left + 3:right - 2].mean() > .30:
                continue
            box = (left, top, right, bottom)
            if not any(max(abs(a - b) for a, b in zip(box, prior)) <= 3 for prior in boxes):
                boxes.append(box)
    for first in boxes:
        for later in boxes:
            left, top, right, bottom = first
            a, b, c, d = later
            y, yy = (top + bottom) // 2, (b + d) // 2
            x, xx = (left + right) // 2, (a + c) // 2
            horizontal = (a - right >= 6 and abs(y - yy) <= 3
                          and ink[max(0, y - 2):y + 3, right:a + 1].any(axis=0).mean() >= .8)
            vertical = (b - bottom >= 6 and abs(x - xx) <= 3
                        and ink[bottom:b + 1, max(0, x - 2):x + 3].any(axis=1).mean() >= .8)
            if horizontal or vertical:
                # Preserve the full canvas, including labels and connector
                # arrows outside detected boxes; subtitle masking is disabled.
                return _Line("", 0., 0., 1., 1.)
    return None


@dataclass(frozen=True)
class _Candidate:
    frame: Frame
    kind: str
    # Native-resolution digests are comparison evidence, never perceptual hashes.
    size: tuple[int, int]
    digest: bytes
    text: tuple[str, ...]
    captions: tuple[_Line, ...]
    protected: tuple[_Line, ...]
    flat_canvas: bool


def _rectangle(line: _Line, size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (max(0, int(line.x * width)), max(0, int(line.y * height)),
            min(width, math.ceil((line.x + line.width) * width)),
            min(height, math.ceil((line.y + line.height) * height)))


def _overlap(first: _Line, second: _Line, size: tuple[int, int]) -> bool:
    # Use exactly the same outward pixel rounding as the actual mask.
    a, b = _rectangle(first, size), _rectangle(second, size)
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _caption_lines(frame: Frame, lines: list[_Line], spoken: tuple) -> list[_Line]:
    candidates = [line for line in lines if _caption(line, frame, spoken)]
    # A row aligned with an established document column is reading material,
    # even when it is near the bottom and the speaker reads it aloud.
    upper = [line for line in lines if line.y < .65]
    code_context = any(_code_line(line.text) for line in upper)
    return [line for line in candidates if not any(
        abs(line.x - anchor.x) <= .025
        for anchor in upper
    ) and not (code_context and _code_line(line.text))]


def _native(frame: Frame) -> np.ndarray | None:
    try:
        with Image.open(frame.path) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    except (OSError, ValueError):
        return None


def _mask(pixels: np.ndarray, captions: tuple[_Line, ...]) -> np.ndarray:
    result = pixels.copy()
    height, width = result.shape[:2]
    for line in captions:
        # Do not blank a whole lower band or downsample small plot/code changes.
        x1, y1, x2, y2 = _rectangle(line, (width, height))
        result[y1:y2, x1:x2] = 0
    return result


def _signature(frame: Frame, captions: list[_Line]) -> tuple[str, ...]:
    ignored = {_normal(line.text) for line in captions}
    # Keep case, punctuation, operators and repeated tokens. Speech coverage
    # never excuses a changed number or symbol in the document itself.
    return tuple(_normal(text) for text in (frame.lines or (frame.text,))
                 if text.strip() and _normal(text) not in ignored)


def _safe_captions(lines: list[_Line], captions: list[_Line], *,
                   chart: _Line | None, size: tuple[int, int]) -> tuple[_Line, ...]:
    protected = [line for line in lines if line not in captions]
    if chart:
        protected.append(chart)
    if sum(line.width * line.height for line in captions) > .20:
        return ()
    return tuple(line for line in captions
                 if not any(_overlap(line, other, size) for other in protected))


def _complete_layout(frame: Frame, lines: list[_Line]) -> bool:
    try:
        nonempty = [block for block in frame.ocr_layout if str(block.get("text") or "").strip()]
        located = {_normal(line.text) for line in lines}
        return len(nonempty) == len(lines) and all(
            _normal(line) in located for line in frame.lines if line.strip())
    except (AttributeError, TypeError, ValueError):
        return False


def _outro_only(texts) -> bool:
    texts = [re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", _normal(text))
             for text in texts if text.strip()]
    if not any(_CLOSING.search(text) for text in texts):
        return False
    if any(re.search(r"\d", text) for text in texts):
        return False
    def ornament_noise(text):
        if not re.search(r"[a-z\u3400-\u9fff]", text, re.I):
            return True
        words = re.findall(r"[a-z]+", text)
        if set(word.casefold() for word in words) & {
            "to", "be", "or", "not", "and", "the", "for", "in", "on", "of", "no", "yes",
            "pe", "pb", "roe", "roa", "nav", "pnl", "eps", "var", "rsi", "atr", "ema", "sma",
        }:
            return False
        return (not re.search(r"[\u3400-\u9fff]", text) and len(words) >= 4
                and max(map(len, words)) <= 3 and len(set(words)) < len(words)
                and sum(char.isupper() for char in text) <= len(text) * .2)
    unknown = [text for text in texts if not (_CLOSING.search(text) or _PROMO.search(text)
                                             or ornament_noise(text))]
    # A single short nonnumeric logo can accompany the closing/promotional
    # lines. Any extra prose, numbers, formula or code makes this inconclusive.
    return len(unknown) <= 1 and all(
        _text_size(text) <= 8 and not re.search(r"[0-9=<>+*/%]", text)
        for text in unknown)


def _candidate(frame: Frame, spoken: tuple) -> _Candidate | None:
    lines = _layout(frame)
    pixels = _pixels(frame)
    if pixels is None:
        return None
    captions = _caption_lines(frame, lines, spoken)
    body = [line for line in lines if line not in captions]
    canvas, ink = _canvas(pixels)
    caption_text = {_normal(line.text) for line in captions}
    code_lines = [line.text for line in body if line.y < .7]
    if canvas:
        code_lines.extend(_normal(line) for line in frame.lines
                          if _normal(line) not in caption_text)
    code = any(_code_line(text) for text in code_lines)
    chart = _chart_region(ink) if canvas else None
    diagram = _diagram_region(ink) if canvas and chart is None else None
    if not code and chart is None and diagram is None and _outro_only(frame.lines):
        return None
    reading = _reading_material(body, canvas)
    # Low OCR confidence is not evidence that an otherwise structured page
    # is disposable. Use its geometry only; uncertain text may never provide
    # a caption mask or participate in lossy reading-view coverage.
    uncertain_reading = False
    if canvas and not (code or reading or chart or diagram):
        tentative = [line for line in _layout(frame, minimum_confidence=.25)
                     if line not in captions]
        missing_text = (bool(frame.ocr_warning) or not frame.lines
                        or len(lines) < max(3, len(frame.lines) * .5))
        uncertain_reading = (_reading_material(tentative, canvas)
                             or (missing_text and _text_raster(ink)))
    if not (code or reading or chart or diagram or uncertain_reading):
        return None
    native = _native(frame)
    if native is None:
        return None
    size = (native.shape[1], native.shape[0])
    safe = _safe_captions(lines, captions, chart=chart or diagram, size=size)
    # A warning disables subtitle-based merging, not positive content selection.
    if (uncertain_reading or frame.ocr_warning or frame.dedupe_warning
            or not _complete_layout(frame, lines)):
        safe = ()
    fingerprint = hashlib.sha256(_mask(native, safe).tobytes()).digest()
    return _Candidate(frame, "code" if code else "chart" if chart else "diagram" if diagram else
                      "reading_pixels" if uncertain_reading else "reading_layout",
                      size, fingerprint,
                      _signature(frame, list(safe)), safe,
                      tuple(body + ([chart or diagram] if chart or diagram else [])), canvas)


def _same_content(first: _Candidate, later: _Candidate) -> bool:
    if (first.size != later.size or first.text != later.text
            or first.frame.ocr_warning or later.frame.ocr_warning
            or first.frame.dedupe_warning or later.frame.dedupe_warning):
        return False
    if first.captions == later.captions and first.digest == later.digest:
        return True
    if not (first.captions and later.captions):
        return False
    masks = tuple(dict.fromkeys((*first.captions, *later.captions)))
    if (sum(line.width * line.height for line in masks) > .25
            or any(_overlap(mask, text, first.size) for mask in masks
                   for text in (*first.protected, *later.protected))):
        return False
    a, b = _native(first.frame), _native(later.frame)
    return (a is not None and b is not None and a.shape == b.shape
            and np.array_equal(_mask(a, masks), _mask(b, masks)))


def select_content_frames(frames: list[Frame], spoken_intervals=(), *,
                          decisions: dict[int, dict] | None = None) -> list[Frame]:
    """Keep positive code, reading-layout or plotted-data evidence.

    No first/last/context image is added. Empty OCR is assessed visually; an
    separated glyph-like rows on a reading canvas can survive uncertain OCR.
    An unreadable image or arbitrary OCR volume is not positive content evidence.
    Unavailable speech does not force a context image into the selection.
    """
    spoken = tuple(sorted(spoken_intervals, key=lambda item: (item[0], item[1])))
    groups: list[list[_Candidate]] = []
    representatives: list[_Candidate] = []
    seen: dict[tuple, int] = {}
    last_body: dict[tuple, int] = {}
    records = decisions if decisions is not None else {}
    for frame in sorted(frames, key=lambda item: (item.timestamp, item.index)):
        candidate = _candidate(frame, spoken)
        if candidate is None:
            records[frame.index] = {"reason": "promotional_outro" if _outro_only(frame.lines)
                                   else "no_independent_visual_content_selected"}
            continue
        key = (candidate.size, candidate.digest, candidate.text,
               tuple((line.x, line.y, line.width, line.height) for line in candidate.captions))
        body_key = (candidate.size, candidate.kind, candidate.text)
        possible = seen.get(key)
        if possible is None:
            possible = last_body.get(body_key)
        if possible is not None and _same_content(representatives[possible], candidate):
            groups[possible].append(candidate)
            # Keep the first positive representative as a fixed anchor. A
            # union of two subtitle masks is not transitive; swapping anchors
            # could otherwise hide gradual changes across a long sequence.
            seen[key] = possible
            last_body[body_key] = possible
        else:
            seen[key] = len(representatives)
            last_body[body_key] = len(representatives)
            representatives.append(candidate)
            groups.append([candidate])
    for representative, group in zip(representatives, groups):
        for candidate in group:
            records[candidate.frame.index] = (
                {"reason": "independent_" + representative.kind}
                if candidate is representative else
                {"reason": "duplicate_visual_content",
                 "representative_index": representative.frame.index})
    # Approximate reading-view coverage is deliberately separate from proven
    # duplicates. A moving camera is not a new paragraph; stable digital edits,
    # code and plots continue through the strict comparison above.
    views = [view for item in representatives if item.kind == "reading_layout"
             if (view := reading_views.describe(item.frame, item.protected, item.captions,
                                                protect_moderate=item.flat_canvas)) is not None]
    _, covered = reading_views.choose(views)
    for index, decision in list(records.items()):
        original = decision.get("representative_index", index)
        if original in covered:
            records[index] = dict(covered[original])
    return sorted((item.frame for item in representatives if item.frame.index not in covered),
                  key=lambda frame: (frame.timestamp, frame.index))
