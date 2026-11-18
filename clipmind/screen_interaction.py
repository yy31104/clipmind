"""Corroborate settled screen edits and discount intermediate interaction states."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

import cv2
import numpy as np

from .media import Frame
from .screen_rank_model import CUTOFF

_WIDTH = 640
_SHAPE_SCORE = .18
_SHELL = re.compile(r"^([^\s$>%#]*[@:~][^\s$>%#]*[$>%#]|[$>%#])(?:\s+(.*?))?\s*$")
_FIELD = re.compile(r"^([^\W\d_][^:@!?.;(){}/<>\[\]\\|=]{2,26}):\s*(.*?)\s*$")
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"' r"|'(?:[^'\\]|\\.)*'")
_CALL = re.compile(r"\b([a-z_]\w*)\s*\(")


@dataclass(frozen=True)
class _Change:
    bands: tuple[tuple[int, int, int], ...]
    menu_only: bool
    recurring_chrome: bool
    persistent_shape: bool
    continues_move: bool = False


def _components(mask):
    _, _, stats, _ = cv2.connectedComponentsWithStats(np.uint8(mask))
    return stats[1:]


def _menu_region(changed):
    height, width = changed.shape
    work = changed.copy()
    work[:round(height * .04)] = False
    work[round(height * .94):] = False
    joined = cv2.morphologyEx(np.uint8(work), cv2.MORPH_CLOSE, np.ones((7, 17), np.uint8))
    joined = cv2.dilate(joined, np.ones((3, 9), np.uint8))
    regions = []
    for x, y, w, h, _ in _components(joined):
        if y <= height * .17 and .14 * height <= h <= .94 * height and .08 * width <= w <= .6 * width:
            region = np.s_[y:y + h, x:x + w]
            regions.append((np.count_nonzero(changed[region]), region))
    if regions:
        count, region = max(regions, key=lambda item: item[0])
        if count >= .64 * np.count_nonzero(changed):
            return region
    return None


def _menu_return(i, times, gray, changed):
    # Look for a temporary panel covering the same pixels in either theme.
    # An independent edit at the same time keeps its ordinary ranking evidence.
    region = _menu_region(changed)
    if region is None:
        return None
    old_image, current_image = gray(i - 1), gray(i)
    before, current = old_image[region], current_image[region]
    shade = np.mean(current.astype(np.int16) - before)
    tone = float(np.median(before))
    if not ((tone > 80 and shade < -5) or (tone <= 80 and shade > 6)):
        return None
    # A popover has a flat background. Reject loading tiles and other
    # rectangular changes that reveal a textured part of the scene.
    if np.mean(np.abs(current.astype(np.int16) - np.median(current)) <= 20) < .76:
        return None
    active = changed[region]
    count = np.count_nonzero(active)
    stop = min(len(times), i + 14, bisect_left(times, times[i] + 12))

    closing = next((k for k in range(i + 1, stop)
                    if np.count_nonzero(active & (cv2.absdiff(before, gray(k)[region]) <= 20)) >= .84 * count),
                   None)
    if closing is None:
        return None

    # The uncovered area may already hold a real edit as a menu opens.
    after = gray(closing)
    height, width = changed.shape
    y, x = region
    panel = np.s_[max(0, y.start - 5):y.stop + 5, max(0, x.start - 5):x.stop + 5]
    visible = np.s_[round(.115 * height):round(.88 * height), round(.06 * width):round(.94 * width)]
    sustained = changed & (cv2.absdiff(current_image, after) <= 19)
    sustained[panel] = False
    opening_only = not _has_connected_detail(sustained[visible])

    # A pure dismissal restores the covered pixels and leaves the rest of
    # the last displayed screen as it was. Check both areas for new detail.
    residual = cv2.absdiff(gray(closing - 1), after) >= 25
    residual[panel] = False
    inside = cv2.absdiff(before, after[region]) >= 25
    pure = not _has_connected_detail(residual) and not _has_connected_detail(inside)
    return closing, panel, opening_only, pure


def _inside_open_menu(changed, panels):
    if not panels:
        return False
    visible = changed.copy()
    for _, panel in panels:
        visible[panel] = False
    return not _has_connected_detail(visible)


def _has_connected_detail(changed):
    return any(row[cv2.CC_STAT_AREA] >= 6 for row in _components(changed))


def _persistent_line_edit(changed, before, current, following):
    height, width = changed.shape
    count = np.count_nonzero(changed)
    if count < 6 or count > changed.size * .0014:
        return False
    retained = cv2.absdiff(current, following) <= 19
    if np.count_nonzero(changed & retained) < .8 * count:
        return False
    grouped = cv2.morphologyEx(np.uint8(changed), cv2.MORPH_CLOSE, np.ones((3, 13), np.uint8))
    for x, y, w, h, _ in _components(grouped):
        if not (3 <= h <= 20 and 2 <= w <= 110 and .11 * height < y < .9 * height and 11 < x < width - 11):
            continue
        edited = np.count_nonzero(changed[y:y + h, x:x + w])
        if edited < max(6, .75 * count):
            continue
        flank_rows = slice(max(0, y - 4), y + h + 4)
        flanks = (
            np.s_[flank_rows, max(0, x - 22):x - 1],
            np.s_[flank_rows, x + w + 1:x + w + 23],
        )
        ink = []
        for flank in flanks:
            pixels = current[flank]
            if not pixels.size:
                ink.append(0)
                continue
            rich = np.abs(pixels.astype(np.float32) - np.median(pixels)) >= 38
            ink.append(np.count_nonzero(rich & retained[flank]))
        if min(ink) >= 5:
            return True
        if edited >= 30 and max(ink) >= 10:
            area = np.s_[y:y + h, x:x + w]
            previous = before[area].astype(np.float32)
            old_ink = np.abs(previous - np.median(previous)) >= 38
            if np.count_nonzero(old_ink & changed[area]) >= .15 * edited:
                return True
    return False


def _continues_ink_move(before, first, settled, filled):
    # Two consecutive depictions of the same compact object movement
    # share a region, with part of the earlier change still visible.
    if (filled < 16 or min(np.mean(image >= 232) for image in (before, first, settled)) < .8
            or not .001 <= np.mean(cv2.absdiff(before, first) >= 25) <= .012):
        return False

    def ink_change(a, b):
        return ((a < 105) & (b > 170)) | ((b < 105) & (a > 170))

    initial = ink_change(before, first)
    count = np.count_nonzero(initial)
    if not count:
        return False
    continued = cv2.dilate(np.uint8(ink_change(first, settled)), np.ones((11, 11), np.uint8))
    if np.count_nonzero(initial & (continued != 0)) < .8 * count:
        return False
    retained = initial & ink_change(before, settled) & (cv2.absdiff(first, settled) < 30)
    return .25 * count <= np.count_nonzero(retained) <= .75 * count


def _recurs(i, times, masks):
    t = times[i]
    old = masks[bisect_left(times, t - 100, lo=1) - 1:bisect_left(times, t - 6, lo=1) - 1]
    future = masks[bisect_right(times, t + 6, lo=1) - 1:bisect_right(times, t + 100, lo=1) - 1]
    matches = 0
    here = masks[i - 1]
    for nearby in (old, future):
        smaller = np.minimum(nearby, here).sum(axis=(1, 2))
        larger = np.maximum(nearby, here).sum(axis=(1, 2))
        matches += np.count_nonzero(smaller > .58 * larger)
    return matches >= 3


def _raster_changes(frames):
    size = None

    @lru_cache(maxsize=18)
    def gray(i):
        image = cv2.imread(str(frames[i].path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("A saved frame could not be read for interaction ranking.")
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)

    first = cv2.imread(str(frames[0].path), cv2.IMREAD_GRAYSCALE)
    if first is None:
        raise ValueError("The initial frame could not be read for interaction ranking.")
    source_height, source_width = first.shape
    scale = min(1, _WIDTH / max(source_width, source_height))
    width, height = (max(1, round(side * scale)) for side in (source_width, source_height))
    size = (width, height)
    if min(size) < 32:
        return [None] * len(frames), height

    times = [frame.timestamp for frame in frames]
    masks = np.empty((len(frames) - 1, 40, 64), np.uint8)
    for i in range(1, len(frames)):
        changed = np.uint8(cv2.absdiff(gray(i - 1), gray(i)) >= 28)
        masks[i - 1] = cv2.resize(changed * 255, (64, 40), interpolation=cv2.INTER_AREA)

    kernel = np.ones((3, 3), np.uint8)

    @lru_cache(maxsize=18)
    def dark(i):
        return cv2.erode(np.uint8(gray(i) < 115), kernel).astype(bool)

    interior = np.s_[round(height * .115):round(height * .88), round(width * .06):round(width * .94)]
    evidence = [None]
    menu_dismissals = set()
    open_menus = []
    for i, t in enumerate(times[1:], 1):
        before, current = gray(i - 1), gray(i)
        delta = cv2.absdiff(before, current)
        changed = delta >= 25
        share = float(changed.mean())
        core = changed[interior]
        core_share = np.count_nonzero(core) / max(1, np.count_nonzero(changed))
        bright = min((before[interior] >= 232).mean(), (current[interior] >= 232).mean())
        chrome = shape = False
        if bright > .72 and .002 <= share <= .15:
            filled = ((dark(i) & (before > 170)) | (dark(i - 1) & (current > 170)))[interior]
            largest = max((row[cv2.CC_STAT_AREA] for row in _components(filled)), default=0)
            chrome = .01 <= share and largest <= 12 and core_share < .27 and _recurs(i, times, masks)
            if bright > .84 and largest >= 36 and core.mean() < .025 and core_share > .115:
                later = gray(bisect_right(times, t + 1) - 1)
                shape = np.count_nonzero(filled & (cv2.absdiff(current, later)[interior] < 25)) >= 46
        bands = ()
        if share <= .032:
            row_counts = (delta >= 52).sum(axis=1)
            edges = np.flatnonzero(np.diff(np.r_[False, row_counts >= 5, False].astype(np.int8)))
            bands = tuple((int(a), int(b), int(row_counts[a:b].sum()))
                          for a, b in zip(edges[::2], edges[1::2])
                          if .12 * height <= a < .95 * height and row_counts[a:b].sum() >= 40)
        open_menus = [(closing, panel) for closing, panel in open_menus if closing > i]
        menu_hover = _inside_open_menu(changed, open_menus)
        menu_return = _menu_return(i, times, gray, changed) if .013 <= share <= .38 else None
        opening_only = False
        if menu_return is not None:
            closing, panel, opening_only, closing_only = menu_return
            open_menus.append((closing, panel))
            if closing_only:
                menu_dismissals.add(closing)
        if not shape and i + 1 < len(times) and times[i + 1] >= t + .8 and share <= .0014:
            shape = _persistent_line_edit(changed, before, current, gray(i + 1))
        menu_only = opening_only or menu_hover or i in menu_dismissals
        continues = False
        if (i >= 2 and shape and not menu_only and not evidence[-1].persistent_shape
                and not evidence[-1].menu_only and t - times[i - 1] <= 2):
            original = gray(i - 2)
            early_ink = ((dark(i - 1) & (original > 170)) | (dark(i - 2) & (before > 170)))[interior]
            continues = _continues_ink_move(original[interior], before[interior], current[interior],
                                           np.count_nonzero(early_ink))
        evidence.append(_Change(bands, menu_only, chrome, shape, continues))
    return evidence, height


def _text_items(frame):
    items = []
    for entry in frame.ocr_layout:
        bounds = entry.get("bbox") or ()
        if len(bounds) != 4:
            continue
        x, y, _, height = bounds
        text = " ".join(unicodedata.normalize("NFKC", entry.get("text") or "").casefold().split())
        if height > 0 and text and .1 < y < .955:
            items.append((x, y + height / 2, text))
    return items


def _line_at(items, center, radius=.015):
    return " ".join(text for _, text in sorted((x, text) for x, y, text in items if abs(y - center) <= radius))


def _characters(text):
    return "".join(c for c in text if c.isalnum())


def _grows(before, after):
    if len(before) < 4 or len(after) < len(before) + 2:
        return False
    return after.startswith(before) or SequenceMatcher(None, before, after[:len(before)], autojunk=False).ratio() >= .86


def _balanced_code(text):
    code = _QUOTED.sub("", text)
    if "'" in code or '"' in code:
        return False
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for char in code:
        if char in "([{":
            stack.append(char)
        elif char in pairs and (not stack or stack.pop() != pairs[char]):
            return False
    return not stack


def _statement(text):
    # A semicolon can terminate a statement or occur inside a partial header.
    # OCR can also mistake the caret after a real terminator for a stroke.
    return (text.rstrip("!|¦•").endswith(";") and len(_characters(text)) >= 4
            and _balanced_code(text))


def _settled_statement(i, frames, lines, center, dominance, previous, current):
    if (not .12 < center < .85 or dominance < .74
            or frames[i + 1].timestamp - frames[i].timestamp < 1.2
            or not _statement(current)):
        return False
    text = _characters(current)
    if _statement(previous) and text == _characters(previous):
        return False
    if not any(.023 < abs(y - center) < .12 and (_statement(line) or ("(" in line and line.endswith("{")))
               for _, y, line in lines[i]):
        return False
    for supported in lines[i + 1:i + 3]:
        following = _line_at(supported, center)
        if (_statement(following)
                and SequenceMatcher(None, text, _characters(following), autojunk=False).ratio() >= .75):
            return True
    return False


def _composing_call(i, frames, lines, center, current):
    match = _CALL.search(current)
    if not match or _balanced_code(current):
        return False
    tail = current[match.end():].strip()
    if len(_characters(tail)) < 3:
        return False
    tokens = _characters(current[match.start():])
    for n in range(i + 1, min(i + 5, len(frames))):
        if frames[n].timestamp - frames[i].timestamp > 24:
            break
        text = _line_at(lines[n], center)
        next_call = _CALL.search(text)
        if not next_call or next_call[1] != match[1]:
            continue
        later = _characters(text[next_call.start():])
        if len(later) >= len(tokens) + 4 and SequenceMatcher(
            None, tokens, later[:len(tokens)], autojunk=False,
        ).ratio() >= .78:
            return True
    return False


def _text_adjustments(frames, changes, height):
    lines = list(map(_text_items, frames))
    prompts = Counter()
    for items in lines:
        prompts.update({
            match[1].strip() for x, y, _ in items if x <= .06 and y > .87
            if (match := _FIELD.match(_line_at(items, y, .013)))
        })

    pending = {}
    results = set()
    for i in range(1, len(frames) - 1):
        change = changes[i]
        if change is None or not change.bands:
            continue
        start, stop, count = max(change.bands, key=lambda band: band[2])
        if stop - start >= .045 * height:
            continue
        center = (start + stop) / (2 * height)
        dominance = count / sum(band[2] for band in change.bands)
        previous, current, following = (_line_at(items, center) for items in lines[i - 1:i + 2])
        if not current:
            continue
        old_shell, shell = _SHELL.match(previous), _SHELL.match(current)
        if (dominance >= .72 and shell and len(_characters(shell[2] or "")) >= 2 and old_shell
                and (old_shell[1] == shell[1] or _grows(_characters(old_shell[1]), _characters(shell[1])))
                and not any(_SHELL.match(text) for _, y, text in lines[i] if y > center + .022)):
            pending[i] = False
            continue

        field, old_field, new_field = map(_FIELD.match, (current, previous, following))
        if (center > .87 and dominance >= .58 and field
                and prompts[field[1].strip()] >= 2 and len(_characters(field[1])) >= 5
                and (not field[2] or not new_field or new_field[1] != field[1]
                     or _grows(_characters(field[2]), _characters(new_field[2])))):
            pending[i] = False
            continue

        wait = frames[i + 1].timestamp - frames[i].timestamp
        before, after, later = map(_characters, (previous, current, following))
        next_change = changes[i + 1]
        next_local = next_change is not None and any(
            abs((a + b) / (2 * height) - center) < .018 for a, b, _ in next_change.bands
        )
        closed = current.endswith((".", ";", "}", "!", "?"))
        if (wait <= 6.5 and next_local and not shell and dominance >= .58 and _grows(after, later)
                and before != after and len(after) >= 8 and not closed
                and (not current.endswith("()") or len(after) >= 12)):
            pending[i] = True
            continue

        if (.12 < center < .86 and dominance >= .67 and next_local and not shell
                and _composing_call(i, frames, lines, center, current)):
            pending[i] = False
            continue

        if (center > .87 and dominance >= .48 and old_field and not field
                and prompts[old_field[1].strip()] >= 2 and len(_characters(old_field[2])) >= 2
                and len(after) >= 8 and not _grows(before, after)):
            results.add(i)
        elif pending.get(i - 1) and wait >= 5 and len(after) >= 10 and closed:
            results.add(i)
        elif _settled_statement(i, frames, lines, center, dominance, previous, current):
            results.add(i)
    return pending, results


def adjust_scores(
    frames: list[Frame], scores: dict[int, float],
) -> tuple[dict[int, float], dict[int, int]]:
    """Use only saved images, their layout observations, and neighboring timestamps."""
    if not scores:
        return scores, {}
    changes, height = _raster_changes(frames)
    pending, results = _text_adjustments(frames, changes, height)
    adjusted = scores.copy()
    for i, score in scores.items():
        change = changes[i]
        if i in pending:
            adjusted[i] = score * .25
        elif i in results:
            adjusted[i] = max(score, .26)
        elif change and change.menu_only:
            adjusted[i] = score * .12
        elif change and change.recurring_chrome:
            adjusted[i] = score * .34
        elif change and change.persistent_shape:
            adjusted[i] = max(score, _SHAPE_SCORE)
    fallback_onsets = {}
    for i, score in scores.items():
        change = changes[i]
        if (change and change.continues_move and adjusted[i] == _SHAPE_SCORE
                and score < CUTOFF and i - 1 in adjusted and adjusted[i - 1] < CUTOFF
                and i - 1 not in pending):
            # Prefer the earlier onset, but keep the settled frame eligible
            # if another boundary suppresses it.
            adjusted[i - 1] = _SHAPE_SCORE
            fallback_onsets[i] = i - 1
    return adjusted, fallback_onsets
