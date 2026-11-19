"""Use pane geometry and persistent ink to rank editor interactions."""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import re

import cv2
import numpy as np

from .media import Frame
from .screen_interaction import _balanced_code, _components

_CODE_LAYOUT = re.compile(r"[A-Za-z_][\w.]*\s*\(|\w\s*[=:{]\s*\w")
_CALL = re.compile(
    r"^(?:(?:var|val|let|const)\s+\w+\s*=\s*|[\w. ]+\s*=\s*)?"
    r"[A-Za-z_]\w+(?:\s*[.,]\s*[A-Za-z_]\w+)*\s*\("
)
_INTERACTION_FACTOR = .25
_SETTLED_EXPRESSION_SCORE = .18


@dataclass(frozen=True)
class _Editor:
    right: float
    step: float
    rows: tuple[float, ...]


@dataclass(frozen=True)
class _Row:
    left: float
    center: float
    right: float
    height: float
    text: str


@dataclass(frozen=True)
class _Panel:
    region: tuple[slice, slice]
    opening: bool
    partner: int
    restored: float


def _raster_columns(image):
    height, width = image.shape
    if width < 120 or height < 90:
        return []
    background = cv2.morphologyEx(image, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contrast = image.astype(np.int16) - background
    ink = (contrast >= 9) & (image < 125)
    joined = cv2.morphologyEx(np.uint8(ink), cv2.MORPH_CLOSE, np.ones((2, 3), np.uint8))
    glyphs = []
    for x, y, w, h, area in _components(joined):
        edge = int(x + w)
        if (.065 * width < edge < .72 * width and .095 * height < y < .80 * height
                and .003 * width < w < .03 * width and max(4, .007 * height) <= h < .034 * height
                and .20 * w * h <= area and w <= 1.55 * h
                and np.median(background[y:y + h, x:edge]) <= 72):
            glyphs.append((edge, y + h / 2, int(h)))
    candidates = []
    visited = set()
    bright = contrast >= 24
    for edge, _ in Counter(x for x, _, _ in glyphs).most_common(40):
        if edge in visited:
            continue
        group = sorted((y, h) for x, y, h in glyphs if abs(x - edge) <= 2)
        if len(group) < 5:
            continue
        visited.update(range(edge - 2, edge + 3))
        unique = [group[0]]
        for y, h in group[1:]:
            if y - unique[-1][0] >= 5:
                unique.append((y, h))
        if len(unique) < 5:
            continue
        glyph_height = float(np.median([h for _, h in unique]))
        distances = np.diff([y for y, _ in unique])
        pitches = distances[(1.45 * glyph_height <= distances) & (distances <= 3.2 * glyph_height)]
        if len(pitches) < 4:
            continue
        for pitch in np.unique(pitches):
            if np.count_nonzero(abs(pitches - pitch) <= 1.1) < 4:
                continue
            regular = max((
                [(y, h) for y, h in unique if abs((y - center) - round((y - center) / pitch) * pitch) <= 1.7]
                for center, _ in unique
            ), key=len)
            if len(regular) < max(5, .66 * len(unique)):
                continue
            gap_start = edge + max(3, round(glyph_height * .52))
            gap_end = edge + max(gap_start - edge + 2, round(glyph_height * 1.65))
            gap_clear = np.mean([
                ink[max(0, round(y - h / 2) - 1):round(y + h / 2) + 1, gap_start:gap_end].mean() < .13
                for y, h in regular
            ])
            if gap_clear < .70:
                continue
            code_end = gap_end + round(width * .25)
            alongside = np.mean([
                np.count_nonzero(bright[max(0, round(y - h * .55)):round(y + h * .55) + 1, gap_end:code_end]) >= 18
                for y, h in regular
            ])
            if alongside >= .60:
                editor = _Editor(edge / width, float(pitch / height), tuple(y / height for y, _ in regular))
                candidates.append((len(regular) * gap_clear * alongside, editor))
    return sorted(candidates, key=lambda item: item[0], reverse=True)


def _raster_editor(frame: Frame, gray, index: int) -> _Editor | None:
    # OCR can drop dim line numbers yet recognize the code alongside them.
    items = [
        entry["bbox"] for entry in frame.ocr_layout
        if len(entry.get("bbox") or ()) == 4 and _CODE_LAYOUT.search(entry.get("text") or "")
    ]
    if len(items) < 2:
        return None
    for quality, editor in _raster_columns(gray(index)):
        if quality < 6:
            break
        edge, step, rows = editor.right, editor.step, editor.rows
        supporting = {
            round((y + h / 2) / step) for x, y, w, h in items
            if edge - .05 < x < edge + .38 and x + w > edge + .085 and .35 * step < h < 1.8 * step
            and any(abs(y + h / 2 - mark) <= .40 * step for mark in rows)
        }
        if len(supporting) >= 2:
            return editor
    return None


def _editor_layouts(frames, times, gray):
    raster = [_raster_editor(frame, gray, i) for i, frame in enumerate(frames)]
    layouts = [None] * len(frames)
    for i, editor in enumerate(raster):
        if editor is None:
            continue
        earlier, later = max(0, i - 3), min(len(frames), i + 4)
        if any(
            j != i and abs(times[i] - times[j]) <= 8
            and abs(other.right - editor.right) <= .015 and abs(other.step - editor.step) <= .10 * editor.step
            for j in range(earlier, later) if (other := raster[j]) is not None
        ):
            layouts[i] = editor
    return layouts


def _panel_regions(before, current, editor):
    height, width = before.shape
    delta = current.astype(np.int16) - before
    for sign, panel, underlying in ((1, current, before), (-1, before, current)):
        change = delta * sign
        high = (change >= 9) & (change < 120)
        if not np.any(high):
            continue
        groups = cv2.morphologyEx(np.uint8(high), cv2.MORPH_CLOSE, np.ones((5, 9), np.uint8))
        groups = cv2.morphologyEx(groups, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
        for x, y, w, h, area in _components(groups):
            if not (editor.right * width - 13 <= x and y > .075 * height and x + w < .96 * width
                    and .09 * width <= w <= .58 * width and 1.85 * editor.step * height <= h <= .54 * height
                    and w * h <= .21 * width * height and area >= .62 * w * h):
                continue
            if not -.03 <= x / width - editor.right < .55:
                continue
            region = np.s_[y:y + h, x:x + w]
            tone = np.median(panel[region])
            if tone > 120 or np.median(underlying[region]) > 110:
                continue
            mask = high[region] & (np.abs(panel[region].astype(np.int16) - tone) <= 6)
            if (mask.mean() >= .43 and np.mean(mask.mean(axis=0) > .38) >= .60
                    and np.mean(mask.mean(axis=1) > .38) >= .60):
                yield region, sign > 0


def _largest_group(mask, shape, *, minimum_width=1, minimum_height=1):
    joined = cv2.morphologyEx(np.uint8(mask), cv2.MORPH_CLOSE, np.ones(shape, np.uint8))
    return max((
        np.count_nonzero(mask[y:y + h, x:x + w]) for x, y, w, h, _ in _components(joined)
        if w >= minimum_width and h >= minimum_height
    ), default=0)


def _without_panel(mask, region, padding):
    y, x = region
    mask[max(0, y.start - padding):y.stop + padding, max(0, x.start - padding):x.stop + padding] = False
    return mask


def _panels(times, editors, smooth):
    panels = {}
    for i in range(1, len(times) - 1):
        editor = editors[i - 1] or editors[i]
        if editor is None:
            continue
        before, now = smooth(i - 1), smooth(i)
        for region, opening in _panel_regions(before, now, editor):
            covered = cv2.absdiff(now, before)[region] >= 12
            if np.count_nonzero(covered) < 130:
                continue
            if opening:
                underlay = before
                witnesses = range(i + 1, min(i + 14, bisect_right(times, times[i] + 12)))
            else:
                underlay = now
                limit = max(0, i - 14, bisect_right(times, times[i] - 12) - 1)
                witnesses = range(i - 2, limit - 1, -1)
            best, partner = 0., None
            for j in witnesses:
                restoration = np.mean((cv2.absdiff(smooth(j), underlay)[region][covered]) <= 10)
                if restoration > best:
                    best, partner = restoration, j
                if restoration >= .84:
                    break
            if best < .78 or partner is None:
                continue
            previous = panels.get(i)
            if previous is None or (opening and not previous.opening) or previous.restored < best:
                panels[i] = _Panel(region, opening, partner, round(best, 3))
    return panels


def _persistent_editor_ink(editor, gray, smooth, earlier, current, later, exclude=None):
    old, now, next_image = gray(earlier), gray(current), gray(later)
    height, width = now.shape
    contrast = (cv2.absdiff(now, smooth(current)) >= 24) | (cv2.absdiff(old, smooth(earlier)) >= 24)
    ink = (cv2.absdiff(old, now) >= 25) & contrast & (cv2.absdiff(now, next_image) <= 17)
    if exclude is not None:
        _without_panel(ink, exclude, 3)
    ink[:round(height * .12)] = False
    ink[round(height * .92):] = False
    ink[:, :round(width * (editor.right + .012))] = False
    ink[:, round(width * (editor.right + .47)):] = False
    return _largest_group(ink, (5, 9), minimum_width=7, minimum_height=3)


def _dismissible_panels(times, editors, panels, gray, smooth):
    dismissed = set()
    for i, panel in panels.items():
        if panel.restored < .85:
            continue
        editor = editors[i] or editors[i - 1]
        partner = panel.partner
        if panel.opening:
            outside = _persistent_editor_ink(editor, gray, smooth, i - 1, i, i + 1, panel.region)
            # A quick closing can uncover new ink drawn under the panel.
            if times[partner] - times[i] <= 1.25:
                restored = _persistent_editor_ink(
                    editor, gray, smooth, i - 1, partner, min(partner + 1, len(times) - 1),
                )
                outside = max(outside, restored)
        else:
            outside = _persistent_editor_ink(editor, gray, smooth, partner, i, i + 1)
        if outside < 100:
            dismissed.add(i)
    return dismissed


def _rows(frame: Frame, editor: _Editor) -> list[_Row]:
    right, step, marks = editor.right, editor.step, editor.rows
    origin = max(marks, key=lambda mark: sum(
        abs((mark - other) / step - round((mark - other) / step)) < .17 for other in marks
    ))
    groups = {}
    for entry in frame.ocr_layout:
        box = entry.get("bbox") or ()
        if len(box) != 4:
            continue
        x, y, w, h = box
        center = y + h / 2
        cell = (center - origin) / step
        if (right + .009 < x < right + .46 and .44 * step < h < 1.50 * step
                and abs(cell - round(cell)) < .28 and any(abs(center - y) < 7.5 * step for y in marks)):
            groups.setdefault(round(cell), []).append(_Row(x, center, x + w, h, (entry.get("text") or "").strip()))
    rows = []
    for _, items in sorted(groups.items()):
        first, *parts = sorted(items, key=lambda row: row.left)
        end = first.right
        text = [first.text]
        for part in parts:
            if not end - .015 <= part.left <= min(end + .025, right + .40):
                break
            end = part.right
            text.append(part.text)
        rows.append(_Row(first.left, first.center, end, first.height, " ".join(text)))
    return rows


def _nearby(rows, row, distance):
    return [
        other for other in rows
        if abs(other.center - row.center) <= distance and abs(other.left - row.left) <= .073
    ]


def _tokens(text: str) -> str:
    return "".join(char.casefold() for char in text if char.isalnum())


def _similar(before: str, after: str) -> float:
    return SequenceMatcher(None, before, after, autojunk=False).ratio()


def _same_text(before: str, after: str) -> bool:
    first, second = _tokens(before), _tokens(after)
    return first == second or (min(len(first), len(second)) >= 7 and _similar(first, second) >= .86)


def _complete_call(line: str) -> bool:
    line = re.sub(r"[\s|;!¦•]+$", "", line)
    # OCR can read inlay hints and caret fragments as trailing characters.
    line = re.sub(r"(\))\s+(?:[fH1(%@]?[}9]|[H@]|1[)])$", r"\1", line)
    return (
        not line.startswith(("if ", "for ", "while ", "return ", "import ", "fun "))
        and _CALL.match(line) is not None and line.endswith((")", "}", "]"))
        and len(_tokens(line)) >= 7 and _balanced_code(line) and not re.search(r",\s*\)$", line)
    )


def _row_ink(before, current, following, row, later_row=None, earlier_row=None):
    height, width = current.shape
    y0 = max(0, round((row.center - .56 * row.height) * height))
    y1 = min(height, round((row.center + .56 * row.height) * height))
    x0 = max(0, round((row.left - .004) * width))
    x1 = min(width, round((row.right + .006) * width))
    region = np.s_[y0:y1, x0:x1]

    def aligned(image, other):
        if other is None:
            return image[region]
        dy = round((other.center - row.center) * height)
        dx = round((other.left - row.left) * width)
        if abs(dx) <= 3:
            dx = 0
        if abs(dy) <= 3:
            dy = 0
        if not (0 <= x0 + dx < x1 + dx <= width and 0 <= y0 + dy < y1 + dy <= height):
            return image[region]
        return image[y0 + dy:y1 + dy, x0 + dx:x1 + dx]

    old, now, later = aligned(before, earlier_row), current[region], aligned(following, later_row)
    if not now.size or abs(float(np.median(old)) - float(np.median(now))) >= 13:
        return 0, 0, 0
    changed = (cv2.absdiff(old, now) >= 25) & (
        (abs(old.astype(np.int16) - np.median(old)) >= 32)
        | (abs(now.astype(np.int16) - np.median(now)) >= 32)
    )
    return (
        np.count_nonzero(changed),
        np.count_nonzero(changed & (cv2.absdiff(now, later) <= 17)),
        np.count_nonzero(changed & (now > old)),
    )


def _nearby_editors(editors, times):
    supported = editors.copy()
    for i, editor in enumerate(editors):
        if editor is not None:
            continue
        nearby = [
            j for j in range(max(0, i - 5), min(len(editors), i + 6))
            if editors[j] is not None and abs(times[i] - times[j]) <= 6
        ]
        if nearby:
            supported[i] = editors[min(nearby, key=lambda j: abs(times[i] - times[j]))]
    return supported


def _expressions(frames, times, editors, panels, gray):
    supported = _nearby_editors(editors, times)
    rows = [_rows(frame, editor) if editor else [] for frame, editor in zip(frames, supported)]
    complete, partial = set(), set()
    for i in range(1, len(frames) - 1):
        if not rows[i]:
            continue
        step = supported[i].step
        next_frames = range(i + 1, min(len(frames), i + 7, bisect_right(times, times[i] + 8)))
        panel = panels.get(i)
        for row in rows[i]:
            if panel and panel.opening:
                y, x = panel.region
                height, width = gray(i).shape
                if y.start / height <= row.center <= y.stop / height and x.start / width <= row.left <= x.stop / width:
                    continue
            if not _complete_call(row.text):
                if not _CALL.match(row.text) or len(_tokens(row.text)) < 6:
                    continue
                if any(_same_text(row.text, old.text) for old in _nearby(rows[i - 1], row, .38 * step)):
                    continue
                tokens = _tokens(row.text)
                for j in next_frames:
                    candidates = _nearby(rows[j], row, .38 * step)
                    if any(len(_tokens(later.text)) > len(tokens) + 2 and _complete_call(later.text)
                           and _similar(tokens, _tokens(later.text)[:len(tokens)]) >= .86 for later in candidates):
                        total, retained, _ = _row_ink(gray(i - 1), gray(i), gray(j), row)
                        if total >= 14 and retained >= .52 * total:
                            partial.add(i)
                        break
                continue

            if any(
                _complete_call(old.text) and _same_text(row.text, old.text)
                for j in range(i - 1, max(-1, i - 4), -1) if times[i] - times[j] <= 8
                for old in _nearby(rows[j], row, 6.3 * step)
            ):
                continue
            previous = panel.partner if panel and not panel.opening else i - 1
            text = _tokens(row.text)
            prefixes = []
            for j in {i - 1, previous}:
                if times[i] - times[j] > 12:
                    continue
                for old in _nearby(rows[j], row, 5.3 * step):
                    prefix = _tokens(old.text)
                    if (not _complete_call(old.text) and 7 <= len(prefix) <= len(text) + 1
                            and len(text) >= 7 and _similar(prefix, text[:len(prefix)]) >= .80):
                        prefixes.append((j, old))
            earlier = None
            if prefixes:
                previous, earlier = max(prefixes, key=lambda pair: len(_tokens(pair[1].text)))
            else:
                # A newly inserted row needs stable neighbors in the same pane.
                stable_neighbors = sum(
                    any(_same_text(neighbor.text, old.text) for old in _nearby(rows[previous], neighbor, .38 * step))
                    for neighbor in rows[i] if neighbor != row and len(_tokens(neighbor.text)) >= 8
                )
                if stable_neighbors < 2:
                    continue
            for j in next_frames:
                if times[j] - times[i] < 1:
                    continue
                later = next((other for other in _nearby(rows[j], row, step * (5.5 if prefixes else .38))
                              if _same_text(row.text, other.text) and _complete_call(other.text)), None)
                if later is None:
                    continue
                total, retained, added = _row_ink(gray(previous), gray(i), gray(j), row, later, earlier)
                if retained < .68 * total:
                    continue
                if ((prefixes and total >= 12 and (added >= .24 * total or retained >= .86 * total))
                        or (not prefixes and total >= 58 and added >= .48 * total)):
                    complete.add(i)
                    break
    return partial, complete


def adjust_editor_scores(
    frames: list[Frame], gray: Callable[[int], np.ndarray], scores: dict[int, float],
) -> None:
    """Discount transient editor UI and keep independently witnessed edits."""
    if not scores or len(frames) < 3:
        return
    times = [frame.timestamp for frame in frames]
    editors = _editor_layouts(frames, times, gray)
    if not any(editors):
        return

    @lru_cache(maxsize=36)
    def smooth(i):
        return cv2.medianBlur(gray(i), 5)

    panels = _panels(times, editors, smooth)
    dismissed = _dismissible_panels(times, editors, panels, gray, smooth)
    partial, complete = _expressions(frames, times, editors, panels, gray)
    for i in (dismissed | partial) & scores.keys():
        scores[i] *= _INTERACTION_FACTOR
    for i in complete & scores.keys():
        scores[i] = max(scores[i], _SETTLED_EXPRESSION_SCORE)
