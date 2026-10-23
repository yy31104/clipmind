"""Choose legible representatives of jittering, text-heavy reading views.

This is a lossy reading aid, separate from proven pixel duplicates. It must
never decide canonical membership. Unselected OCR stays in the reading pack.
"""
from __future__ import annotations

import re
import math
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from collections import Counter
from statistics import median

from .media import Frame


_TOKENS = re.compile(r"[a-z_]+|[+-]?\d+(?:[,.]\d+)*%?|[\u3400-\u9fff]|[<>=!]+", re.I)
_NEGATIONS = {"no", "not", "never", "without", "except", "不", "没", "未", "無", "无"}


@dataclass
class ReadingView:
    frame: Frame
    phrases: frozenset[str]
    numbers: frozenset[str]
    rows: tuple[tuple[frozenset[str], float, float, float], ...]
    trusted_rows: tuple[tuple[str, ...], ...]
    score: float


def describe(frame: Frame, body, captions) -> ReadingView | None:
    """Use long, trusted body rows; sparse labels aren't paragraph evidence."""
    if frame.ocr_warning or frame.dedupe_warning:
        return None
    located = set()
    for block in frame.ocr_layout:
        try:
            if not isinstance(block, dict) or "text" not in block or "bbox" not in block:
                return None
            text = unicodedata.normalize("NFKC", str(block.get("text") or "")).strip()
            if not text:
                continue
            x, y, width, height = map(float, block["bbox"])
            if (not all(math.isfinite(value) for value in (x, y, width, height))
                    or not 0 <= x < 1 or not 0 <= y < 1 or width <= 0 or height <= 0
                    or x+width > 1.000001 or y+height > 1.000001):
                return None
            located.add(text)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
    if any(unicodedata.normalize("NFKC", text).strip() not in located
           for text in frame.lines if text.strip()):
        return None
    allowed = {(line.text, line.x, line.y, line.width, line.height) for line in body}
    excluded = {line.text for line in captions}
    phrases, numbers, rows = set(), set(), []
    score = 0.
    trusted_rows = []
    for block in frame.ocr_layout:
        # Layout has already been validated by the positive-content selector.
        text = unicodedata.normalize("NFKC", str(block.get("text") or "")).strip()
        try:
            confidence = float(block.get("confidence", 0))
            x, y, width, height = map(float, block["bbox"])
        except (KeyError, TypeError, ValueError):
            continue
        if ((text, x, y, width, height) not in allowed or text in excluded
                or not math.isfinite(confidence) or confidence < .6 or height > .09):
            continue
        tokens = _TOKENS.findall(text.casefold())
        if confidence >= .95:
            numbers.update(token for token in tokens if any(c.isdigit() for c in token))
            if len(tokens) >= 4:
                trusted_rows.append(tuple(_TOKENS.findall(text)))
        if len(text) < 8:
            continue
        # Tiny top chrome is not body prose. A cropped paragraph can start at
        # the very top, so long lines remain eligible there.
        if y < .08 and len(text) < 35:
            continue
        if len(tokens) < 2:
            continue
        normalized = " ".join(tokens)
        grams = frozenset(normalized[i:i+6] for i in range(len(normalized)-5))
        if not grams:
            continue
        rows.append((grams, x, y, height))
        if confidence < .85 or len(text) < 12 or len(tokens) < 3:
            continue
        phrases.update(grams)
        # Lower-confidence variants remain in omitted OCR, not extra pictures.
        # Confidence is a heuristic; it cannot establish the true printed value.
        score += len(normalized) * confidence
    if len(rows) < 3 or score < 40 or not phrases:
        return None
    return ReadingView(frame, frozenset(phrases), frozenset(numbers), tuple(rows),
                       tuple(trusted_rows), score)


def _changed_trusted_row(view: ReadingView, references: list[ReadingView]) -> bool:
    """Protect local edits, not merely a bag of values found elsewhere on page."""
    rows = {row for other in references for row in other.trusted_rows}
    joined = [" ".join(row) for row in rows]
    bags = {row: Counter(row) for row in rows}
    for row in view.trusted_rows:
        if any(" ".join(row) in text for text in joined):
            continue
        for earlier in rows:
            if min(len(row), len(earlier))/max(len(row), len(earlier)) < .8:
                continue
            # Upper bound: even perfect ordering cannot match tokens absent
            # from the other row. Avoid expensive sequence alignment otherwise.
            common = sum((Counter(row) & bags[earlier]).values())
            if 2*common/(len(row)+len(earlier)) < .85:
                continue
            comparison = SequenceMatcher(None, row, earlier, autojunk=False)
            if comparison.ratio() < .85:
                continue
            changed = sum(max(j-i, b-a) for tag, i, j, a, b in comparison.get_opcodes()
                          if tag != "equal")
            if changed <= 2:
                edits = [(row[i:j], earlier[a:b])
                         for tag, i, j, a, b in comparison.get_opcodes() if tag != "equal"]
                for later, before in edits:
                    values = [token for token in (*later, *before)
                              if any(c.isdigit() for c in token) or token in _NEGATIONS
                              or re.fullmatch(r"[<>=!]+|[A-Z]", token)]
                    case_edit = (len(later) == len(before) and any(
                        a != b and a.casefold() == b.casefold()
                        and (a.isupper() or b.isupper() or "_" in a)
                        for a, b in zip(later, before)))
                    if values or case_edit:
                        return True
    return False


def _moving_view(first: ReadingView, other: ReadingView) -> bool:
    """Require multiple matching text anchors that actually moved or scaled.

    Stable digital documents still use the strict pixel/text comparison. In
    particular a one-character edit at the same layout is not a camera view.
    """
    movement, scale = [], []
    for grams, x, y, height in first.rows:
        matches = [(len(grams & candidate) / max(1, len(grams)), a, b, h)
                   for candidate, a, b, h in other.rows]
        overlap, a, b, h = max(matches, key=lambda item: (item[0], -abs(item[1]-x)-abs(item[2]-y)),
                               default=(0., 0., 0., height))
        if overlap >= .7:
            movement.append(max(abs(x-a), abs(y-b)))
            scale.append(abs(height-h) / max(height, h))
    return len(movement) >= 3 and (median(movement) >= .012 or median(scale) >= .12)


def choose(views: list[ReadingView]) -> tuple[set[int], dict[int, dict]]:
    """Cover readable phrases with the clearest views, without a count budget.

    Camera variation is approximate: all source images and OCR must remain
    available elsewhere. Code/chart candidates never enter this stage.
    """
    kept: list[ReadingView] = []
    dropped: dict[int, dict] = {}
    for view in sorted(views, key=lambda item: (-item.score, item.frame.timestamp, item.frame.index)):
        nearby = [other for other in kept
                  if len(view.phrases & other.phrases) >= min(40, len(view.phrases)*.35)
                  and _moving_view(view, other)]
        known = set().union(*(other.phrases for other in nearby))
        numbers = set().union(*(other.numbers for other in nearby))
        coverage = len(view.phrases & known) / max(1, len(view.phrases))
        # One newly exposed substantial row can matter even when the rest of a
        # very long page was already seen. Don't hide it behind aggregate ratios.
        new_row = any(len(grams) >= 40 and grams <= view.phrases
                      and len(grams & known)/len(grams) < .6
                      for grams, *_ in view.rows)
        if (not nearby or coverage < .85 or not view.numbers <= numbers or new_row
                or _changed_trusted_row(view, nearby)):
            kept.append(view)
            continue
        remaining = set(view.phrases & known)
        missing_numbers = set(view.numbers)
        refs = []
        while remaining or missing_numbers:
            best = max(nearby, key=lambda item: len(item.phrases & remaining)
                       + 6*len(item.numbers & missing_numbers))
            if not (best.phrases & remaining or best.numbers & missing_numbers):
                break
            refs.append(best.frame.index)
            remaining.difference_update(best.phrases)
            missing_numbers.difference_update(best.numbers)
        dropped[view.frame.index] = {
            "reason": "repeated_reading_view",
            "representative_index": refs[0],
            "representative_indexes": refs,
            "reading_phrase_coverage": round(coverage, 4),
        }
    return {view.frame.index for view in kept}, dropped
