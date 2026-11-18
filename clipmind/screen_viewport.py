"""Evidence that a recurring display has the same pixels despite outside motion."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from itertools import combinations
from pathlib import Path

import numpy as np

from .media import Frame

Box = tuple[int, int, int, int]
_WIDTH, _HEIGHT = 640, 360
_CONTRAST = 25
_MIN_CONNECTED_PIXELS = 5


def _near(first: Box, second: Box, tolerance: int = 5) -> bool:
    return max(abs(left - right) for left, right in zip(first, second)) <= tolerance


def _gray(image, cv2):
    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (_WIDTH, _HEIGHT),
                      interpolation=cv2.INTER_AREA)


def _long_edges(gray, cv2) -> tuple[list[tuple], list[tuple]]:
    edges = cv2.Canny(gray, 24, 80)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 60, minLineLength=100, maxLineGap=12)
    vertical, horizontal = [], []
    if lines is None:
        return vertical, horizontal
    for x, y, xx, yy in lines[:, 0]:
        if abs(x - xx) <= 2 and abs(y - yy) >= 105:
            vertical.append(((x + xx) / 2, min(y, yy), max(y, yy)))
        if abs(y - yy) <= 2 and abs(x - xx) >= 160:
            horizontal.append(((y + yy) / 2, min(x, xx), max(x, xx)))
    return _distinct_edges(vertical), _distinct_edges(horizontal)


def _distinct_edges(lines: list[tuple]) -> list[tuple]:
    selected = []
    for line in sorted(lines, key=lambda item: item[2] - item[1], reverse=True):
        if all(abs(line[0] - other[0]) >= 4 for other in selected):
            selected.append(line)
        if len(selected) == 10:
            break
    return sorted(selected)


def _large_rectangle(gray, cv2) -> Box | None:
    vertical, horizontal = _long_edges(gray, cv2)
    best, largest = None, 0
    for (left, y1, y2), (right, yy1, yy2) in combinations(vertical, 2):
        width = right - left
        if width < _WIDTH * 0.38:
            continue
        for (top, x1, x2), (bottom, xx1, xx2) in combinations(horizontal, 2):
            height = bottom - top
            area = width * height
            if (height < _HEIGHT * 0.38 or not 1.15 <= width / height <= 2.2
                    or not largest < area < _WIDTH * _HEIGHT * 0.88):
                continue
            # Each observed line has to cover most of the proposed rim.
            if (any(min(right, end) - max(left, start) < 0.76 * width
                    for start, end in ((x1, x2), (xx1, xx2)))
                    or any(min(bottom, end) - max(top, start) < 0.76 * height
                           for start, end in ((y1, y2), (yy1, yy2)))):
                continue
            best = (round(left), round(top), round(right), round(bottom))
            largest = area
    return best


def _visible_rim(gray, box: Box, cv2) -> bool:
    left, top, right, bottom = box
    if min(left, top, _WIDTH - 1 - right, _HEIGHT - 1 - bottom) < 4:
        return False
    edge = cv2.Canny(gray, 24, 80)
    lines = [edge[top:bottom, x - 3:x + 4].max(axis=1) for x in (left, right)]
    lines += [edge[y - 3:y + 4, left:right].max(axis=0) for y in (top, bottom)]
    return sum(np.mean(line > 0) >= 0.52 for line in lines) >= 3


def _bright_projection(gray, cv2) -> Box | None:
    """An illuminated rectangular plane can occupy a smaller part of a room view."""
    small = cv2.resize(gray, (_WIDTH // 2, _HEIGHT // 2), interpolation=cv2.INTER_AREA)
    smoothed = cv2.medianBlur(small, 7)
    candidates = []
    for cutoff in np.percentile(smoothed, (72, 83, 89)):
        mask = np.uint8(smoothed > cutoff)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        for x, y, width, height, count in stats[1:]:
            if (not 0.08 <= count / mask.size <= 0.55 or not 1.1 <= width / height <= 2.0
                    or count < 0.78 * width * height
                    or min(x, y, small.shape[1] - x - width, small.shape[0] - y - height) < 5):
                continue
            inner = smoothed[y + 4:y + height - 4, x + 4:x + width - 4]
            surround = np.concatenate((
                smoothed[y - 4:y, x:x + width].ravel(),
                smoothed[y + height:y + height + 4, x:x + width].ravel(),
                smoothed[y:y + height, x - 4:x].ravel(),
                smoothed[y:y + height, x + width:x + width + 4].ravel(),
            ))
            if np.median(inner) > np.median(surround) + 24:
                candidates.append((count, (2 * x, 2 * y, 2 * (x + width), 2 * (y + height))))
    return max(candidates)[1] if candidates else None


def _room_edges(gray, box: Box, cv2):
    thumbnail = cv2.resize(gray, (_WIDTH // 2, _HEIGHT // 2), interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(thumbnail, 24, 80) != 0
    left, top, right, bottom = (value // 2 for value in box)
    edges[max(0, top - 8):bottom + 8, max(0, left - 8):right + 8] = False
    return edges


def _same_room(expected, observed, cv2) -> bool:
    """Confirm established room geometry even if lighting reveals extra detail."""
    neighborhood = np.ones((3, 3), dtype=np.uint8)
    nearby = cv2.dilate(np.uint8(expected), neighborhood) != 0
    expanded = cv2.dilate(np.uint8(observed), neighborhood) != 0
    matches = expected & expanded
    if np.count_nonzero(matches) < max(120, np.count_nonzero(expected) * 0.72):
        return False
    if np.count_nonzero(observed & nearby) < 120:
        return False
    support = 0
    for y in (0, _HEIGHT // 4):
        for x in (0, _WIDTH // 4):
            part = np.s_[y:y + _HEIGHT // 4, x:x + _WIDTH // 4]
            known = np.count_nonzero(expected[part])
            if known >= 30 and np.count_nonzero(matches[part]) >= known * 0.64:
                support += 1
    return support >= 3


def native_change(first, second, cv2) -> bool:
    """Protect compact strokes at the captured resolution and in all color channels."""
    changed = np.uint8(cv2.absdiff(first, second).max(axis=2) >= _CONTRAST)
    if np.count_nonzero(changed) < _MIN_CONNECTED_PIXELS:
        return False
    _, _, stats, _ = cv2.connectedComponentsWithStats(changed)
    return bool(np.any(stats[1:, cv2.CC_STAT_AREA] >= _MIN_CONNECTED_PIXELS))


def _pixels_at(image, box: Box) -> Box:
    height, width = image.shape[:2]
    left, top, right, bottom = box
    return (round(left * width / _WIDTH) + 2, round(top * height / _HEIGHT) + 2,
            round(right * width / _WIDTH) - 2, round(bottom * height / _HEIGHT) - 2)


def _new_side_detail(before, current, following, primary: Box, room_projection: bool, cv2) -> bool:
    """Find persistent new strokes on a previously blank neighboring surface."""
    if before.shape != current.shape or following.shape != current.shape:
        return False
    height, width = current.shape[:2]
    left, top, right, bottom = primary
    changed = np.uint8(cv2.absdiff(before, current).max(axis=2) >= _CONTRAST)
    changed[max(0, top - 12):bottom + 12, max(0, left - 12):right + 12] = 0
    groups = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, np.ones((5, 15), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(groups)
    for x, y, span, depth, _ in stats[1:]:
        if not (span >= 22 and depth >= 7 and span * depth <= 0.08 * width * height):
            continue
        section = np.s_[max(0, y - 4):min(height, y + depth + 4), max(0, x - 4):min(width, x + span + 4)]
        old, new, later = before[section], current[section], following[section]
        if np.mean(cv2.absdiff(new, later).max(axis=2) >= 15) > 0.009:
            continue
        added = cv2.absdiff(old, new).max(axis=2) >= _CONTRAST
        if np.count_nonzero(added) < 35 or not 0.02 <= np.mean(added) <= 0.44:
            continue
        background = np.median(old.reshape(-1, 3), axis=0)
        if np.mean(np.abs(old - background).max(axis=2) >= 18) > 0.03:
            continue
        # In a room view, a dark occluder can look like a blank surface
        # and then reveal a fixed wall or an object as it moves.
        if room_projection and max(background) < 2 * _CONTRAST:
            continue
        return True
    return False


def _side_text_replaced(previous: Frame, current: Frame, following: Frame,
                        first_image, current_image, primary: Box, cv2) -> bool:
    """Require recurring recognized text and changed pixels in a second pane."""
    height, width = current_image.shape[:2]
    left, top, right, bottom = primary

    def readable(frame: Frame):
        for line in frame.ocr_layout:
            text = "".join(line.get("text", "").split())
            confidence = line.get("confidence")
            bounds = line.get("bbox")
            known_confidence = isinstance(confidence, (int, float)) and confidence >= 0.75
            if (len(text) >= 5 and (confidence is None or known_confidence)
                    and isinstance(bounds, (tuple, list)) and len(bounds) == 4):
                x, y, w, h = bounds
                if w > 0 and h > 0:
                    yield text, (x, y, w, h)

    older = tuple(readable(previous))
    stable = tuple(readable(following))
    for text, box in readable(current):
        x, y, w, h = box
        a, b = max(0, int(x * width)), max(0, int(y * height))
        c, d = min(width, round((x + w) * width)), min(height, round((y + h) * height))
        if c <= a or d <= b or (left < c and a < right and top < d and b < bottom):
            continue

        def nearby(other):
            return abs(box[0] - other[0]) <= 0.02 and abs(box[1] - other[1]) <= 0.025

        before = {seen for seen, bounds in older if nearby(bounds)}
        if not before or text in before or not any(
            seen == text and nearby(bounds) for seen, bounds in stable
        ):
            continue
        if native_change(first_image[b:d, a:c], current_image[b:d, a:c], cv2):
            return True
    return False


class DisplayEvidence:
    """Find recurring physical display bounds, then compare their captured pixels."""

    def __init__(self, frames: list[Frame]):
        self.frames = frames
        self.regions: list[Box | None] = [None] * len(frames)
        self.projector = [False] * len(frames)
        self.outside_detail = False
        self._read = lru_cache(maxsize=6)(self._load)
        try:
            import cv2
        except ImportError:
            self.cv2 = None
            return
        self.cv2 = cv2
        if len(frames) >= 3:
            self._find_displays()

    def _load(self, path: Path):
        try:
            encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError:
            return None
        return self.cv2.imdecode(encoded, self.cv2.IMREAD_COLOR) if encoded.size else None

    def _find_displays(self) -> None:
        observed, projections = [], []
        stride = 3 if len(self.frames) >= 9 else 1
        for frame in self.frames[::stride]:
            image = self._read(frame.path)
            if image is None:
                continue
            gray = _gray(image, self.cv2)
            if (box := _large_rectangle(gray, self.cv2)) is not None:
                observed.append((frame.timestamp, box))
            if (box := _bright_projection(gray, self.cv2)) is not None:
                projections.append((frame.timestamp, box, frame.path))
        for index, frame in enumerate(self.frames):
            nearby = [box for time, box in observed if abs(frame.timestamp - time) <= 12]
            for box in nearby:
                if sum(_near(box, other) for other in nearby) >= 3:
                    self.regions[index] = box
                    break
            if self.regions[index] is not None:
                continue
            nearby = [box for time, box, _ in projections if abs(frame.timestamp - time) <= 55]
            for box in nearby:
                support = [other for other in nearby if _near(box, other, 9)]
                if len(support) >= 3:
                    left, top, right, bottom = zip(*support)
                    self.regions[index] = (min(left), min(top), max(right), max(bottom))
                    self.projector[index] = True
                    break
        counts = Counter(box for box in self.regions if box is not None)
        recurring = [box for box, count in counts.most_common(3) if count >= 6]
        for _, box in observed:
            if len(recurring) >= 3:
                break
            if (not any(_near(box, earlier) for earlier in recurring)
                    and sum(_near(box, other) for _, other in observed) >= 3):
                recurring.append(box)
        if recurring:
            for index, frame in enumerate(self.frames):
                if self.regions[index] is not None:
                    continue
                image = self._read(frame.path)
                if image is not None:
                    gray = _gray(image, self.cv2)
                    self.regions[index] = next(
                        (box for box in recurring if _visible_rim(gray, box, self.cv2)), None,
                    )
        self._extend_room_projection(projections)

    def _extend_room_projection(self, observations: list[tuple[float, Box, Path]]) -> None:
        if len(observations) < 3 or all(box is not None for box in self.regions):
            return
        boxes = dict.fromkeys(box for _, box, _ in observations)
        group = max((
            [row for row in observations if _near(box, row[1], 9)] for box in boxes
        ), key=len)
        if len(group) < 3:
            return
        left, top, right, bottom = zip(*(box for _, box, _ in group))
        region = (min(left), min(top), max(right), max(bottom))
        if (region[2] - region[0]) * (region[3] - region[1]) > 0.42 * _WIDTH * _HEIGHT:
            return
        samples = []
        for _, _, path in group[::max(1, len(group) // 7)]:
            image = self._read(path)
            if image is not None:
                samples.append(_room_edges(_gray(image, self.cv2), region, self.cv2))
        backgrounds = [
            edge for edge in samples if sum(_same_room(edge, other, self.cv2) for other in samples) >= 3
        ]
        if not backgrounds:
            return
        for index, frame in enumerate(self.frames):
            if self.regions[index] is not None:
                continue
            image = self._read(frame.path)
            if image is None:
                continue
            edges = _room_edges(_gray(image, self.cv2), region, self.cv2)
            if any(_same_room(expected, edges, self.cv2) for expected in backgrounds):
                self.regions[index], self.projector[index] = region, True

    def unchanged(self, position: int, anchor: int) -> bool:
        self.outside_detail = False
        box = self.regions[position]
        if self.cv2 is None or box is None:
            return False
        current = self._read(self.frames[position].path)
        if current is None:
            return False
        for earlier in dict.fromkeys((position - 1, anchor)):
            if earlier < 0 or (prior := self.regions[earlier]) is None or not _near(prior, box):
                continue
            previous = self._read(self.frames[earlier].path)
            if previous is None or previous.shape != current.shape:
                continue
            left, top, right, bottom = _pixels_at(previous, prior)
            previous_crop = previous[top:bottom, left:right]
            current_crop = current[top:bottom, left:right]
            if previous_crop.size == 0 or native_change(previous_crop, current_crop, self.cv2):
                continue
            if self.projector[earlier] and self.projector[position]:
                # A room cut with a similarly sized bright object cannot
                # confirm an unchanged physical projection.
                surround = self.cv2.absdiff(previous, current)
                surround[top:bottom, left:right] = 0
                reduced = self.cv2.resize(surround, (_WIDTH // 2, _HEIGHT // 2),
                                          interpolation=self.cv2.INTER_AREA)
                if np.mean(reduced.max(axis=2) >= _CONTRAST) >= 0.18:
                    continue
            stable = None
            stable_frame = self.frames[position]
            if position + 1 < len(self.frames):
                following = self.frames[position + 1]
                if following.timestamp - self.frames[position].timestamp <= 10:
                    stable = self._read(following.path)
                    stable_frame = following
            if stable is None and self.frames[position].stable_duration >= 1:
                stable, stable_frame = current, self.frames[position]
            surface = (left, top, right, bottom)
            if stable is not None and (
                _new_side_detail(previous, current, stable, surface,
                                 self.projector[earlier] or self.projector[position], self.cv2)
                or _side_text_replaced(self.frames[earlier], self.frames[position], stable_frame,
                                       previous, current, surface, self.cv2)
            ):
                self.outside_detail = True
                return False
            return True
        return False
