"""Compare projected slides in room video despite perspective and exposure drift."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from functools import lru_cache
from itertools import combinations
from pathlib import Path

import numpy as np

from .media import Frame
from .screen_viewport import _new_side_detail, _side_text_replaced

_WIDTH, _HEIGHT = 480, 270
_RIM_SAMPLES = np.linspace(0.12, 0.88, 20, dtype=np.float32)
_CONTEXT_SECONDS = 48


def _intersection(horizontal, vertical):
    # The lines have been measured near horizontal and near vertical.
    a, b = horizontal[:2]
    c, d = vertical[:2]
    x = (c * b + d) / (1 - c * a)
    return x, a * x + b


def _distinct(edges, span):
    result = []
    for edge in sorted(edges, key=lambda row: row[3] - row[2], reverse=True):
        center = edge[0] * span / 2 + edge[1]
        if all(abs(other[0] * span / 2 + other[1] - center) >= 3 for other in result):
            result.append(edge)
        if len(result) == 12:
            break
    return sorted(result, key=lambda edge: edge[0] * span / 2 + edge[1])


def _rim_support(gray, corners, cv2):
    """A dark screen frame has brighter samples both inside and outside."""
    support = []
    for start, end in zip(corners, np.roll(corners, -1, axis=0)):
        along = end - start
        normal = np.array([-along[1], along[0]], np.float32) / np.linalg.norm(along)
        points = start + _RIM_SAMPLES[:, None] * along
        offsets = np.arange(-10, 11, dtype=np.float32)
        coordinates = points[None, :, :] + offsets[:, None, None] * normal
        sample = cv2.remap(gray, coordinates[..., 0], coordinates[..., 1], cv2.INTER_LINEAR)
        rim = np.min(sample[8:13], axis=0).astype(np.float32)
        surrounding = np.minimum(np.median(sample[:5], axis=0), np.median(sample[-5:], axis=0))
        support.append(float(np.mean(surrounding - rim > 10)))
    return sorted(support)


def _measure_screen(gray, cv2):
    edges = cv2.Canny(gray, 12, 37)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, 40, minLineLength=56, maxLineGap=17)
    if lines is None:
        return None
    horizontal, vertical = [], []
    for x, y, xx, yy in lines[:, 0]:
        dx, dy = xx - x, yy - y
        if abs(dx) >= 92 and abs(dy / dx) < 0.22:
            slope = dy / dx
            horizontal.append((slope, y - slope * x, min(x, xx), max(x, xx)))
        if abs(dy) >= 66 and abs(dx / dy) < 0.22:
            slope = dx / dy
            vertical.append((slope, x - slope * y, min(y, yy), max(y, yy)))
    horizontal = _distinct(horizontal, _WIDTH)
    vertical = _distinct(vertical, _HEIGHT)
    best = 0.0
    chosen = None
    for top, bottom in combinations(horizontal, 2):
        for left, right in combinations(vertical, 2):
            corners = np.float32([_intersection(top, left), _intersection(top, right),
                                  _intersection(bottom, right), _intersection(bottom, left)])
            if np.any(corners < 10) or np.any(corners > [_WIDTH - 11, _HEIGHT - 11]):
                continue
            width = (corners[1, 0] + corners[2, 0] - corners[0, 0] - corners[3, 0]) / 2
            height = (corners[2, 1] + corners[3, 1] - corners[0, 1] - corners[1, 1]) / 2
            if width < 104 or height < 65 or not 1.12 <= width / height <= 2.28:
                continue
            spans = (corners[[0, 1], 0], corners[[3, 2], 0], corners[[0, 3], 1], corners[[1, 2], 1])
            if any(min(end, b) - max(start, a) < 0.51 * (b - a)
                   for (a, b), (_, _, start, end) in zip(spans, (top, bottom, left, right))):
                continue
            support = _rim_support(gray, corners, cv2)
            score = sum(support) * 3 + 0.9 * width * height / (_WIDTH * _HEIGHT)
            if support[1] >= 0.40 and support[2] >= 0.55 and score > best:
                chosen, best = corners, score
    return chosen


def _rectify(image, corners, size, cv2):
    width, height = size
    rectangle = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    matrix = cv2.getPerspectiveTransform(corners, rectangle)
    return cv2.warpPerspective(image, matrix, size)


def _largest_component(mask, cv2):
    if np.count_nonzero(mask) < 5:
        return 0
    _, _, info, _ = cv2.connectedComponentsWithStats(np.uint8(mask))
    return int(np.max(info[1:, cv2.CC_STAT_AREA]))


def _same_projection(before, after, cv2):
    """Register a small camera shift, then test color and local detail."""
    def grayscale(image):
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    identity = np.eye(2, 3, dtype=np.float32)
    interior = np.zeros(before.shape[:2], dtype=np.uint8)
    interior[12:-12, 12:-12] = 255
    try:
        _, shift = cv2.findTransformECC(
            cv2.GaussianBlur(grayscale(before), (5, 5), 0),
            cv2.GaussianBlur(grayscale(after), (5, 5), 0),
            identity.copy(), cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 22, 0.00015), interior, 1,
        )
    except cv2.error:
        return False
    if (np.max(np.abs(shift[:, :2] - identity[:, :2])) > 0.025
            or np.max(np.abs(shift[:, 2])) > 0.025 * max(before.shape[:2])):
        return False
    aligned = cv2.warpAffine(after, shift, (after.shape[1], after.shape[0]),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    before, after = before.astype(np.float32), aligned.astype(np.float32)
    first = cv2.GaussianBlur(before, (0, 0), 2)
    second = cv2.GaussianBlur(after, (0, 0), 2)
    inner = np.s_[12:-12, 12:-12]
    old_span = np.ptp(np.percentile(first[inner], (15, 85), axis=(0, 1)), axis=0)
    new_span = np.ptp(np.percentile(second[inner], (15, 85), axis=(0, 1)), axis=0)
    scales = np.where(old_span >= 2, np.clip(old_span / np.maximum(2, new_span), 0.65, 1.55), 1)
    offset = np.median((first - scales * second)[inner], axis=(0, 1))
    # Check native color at the edge as well as in the alignment interior.
    # Exclude only pixels interpolated from outside the captured canvas.
    valid = cv2.warpAffine(
        np.ones(before.shape[:2], dtype=np.float32), shift, (after.shape[1], after.shape[0]),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
    ) >= 0.999
    color = np.abs(before - (scales * after + offset)).max(axis=2)
    if _largest_component((color > 24) & valid, cv2) >= 5:
        return False
    low = np.abs(grayscale(np.float32(first - (scales * second + offset))))[inner]
    high = np.abs(grayscale(np.float32((before - first) - scales * (after - second))))[inner]
    strokes = cv2.morphologyEx(np.uint8(high > 9), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return np.mean(low > 13) <= 0.025 and np.mean(high > 10) <= 0.025 and _largest_component(strokes, cv2) <= 48


def unchanged_projection_indices(frames: list[Frame]) -> set[int]:
    """Return frame indices with a recurring, visible, unchanged projected screen."""
    if len(frames) < 6:
        return set()
    try:
        import cv2
    except ImportError:
        return set()

    @lru_cache(maxsize=5)
    def read(path: Path):
        try:
            encoded = path.read_bytes()
        except OSError:
            return None, None
        pixels = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR) if encoded else None
        gray = (cv2.resize(cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY), (_WIDTH, _HEIGHT),
                           interpolation=cv2.INTER_AREA) if pixels is not None else None)
        return pixels, gray

    observed = []
    for frame in frames[::2]:
        _, gray = read(frame.path)
        if gray is not None and (corners := _measure_screen(gray, cv2)) is not None:
            observed.append((frame.timestamp, corners))
    times = [timestamp for timestamp, _ in observed]
    result = set()
    for position, (previous, current) in enumerate(zip(frames, frames[1:]), start=1):
        if current.change_kind not in {"content", "build", "uncertain"} or current.ocr_warning or current.dedupe_warning:
            continue
        left = bisect_left(times, current.timestamp - _CONTEXT_SECONDS)
        right = bisect_right(times, current.timestamp + _CONTEXT_SECONDS)
        if right - left < 3:
            continue
        earlier, earlier_gray = read(previous.path)
        later, later_gray = read(current.path)
        if earlier is None or later is None or earlier.shape != later.shape:
            continue
        nearby = sorted(observed[left:right], key=lambda row: abs(row[0] - current.timestamp))
        for _, corners in nearby[:10]:
            cluster = [other for _, other in nearby if np.max(np.abs(other - corners)) <= 9]
            if len(cluster) < 3:
                continue
            median = np.float32(np.median(cluster, axis=0))
            a, b = (_rim_support(image, median, cv2) for image in (earlier_gray, later_gray))
            # Rectangular desktop windows can have the same dark rim. Require
            # visible perspective in the recurring border before warping it.
            skew = max(abs(median[0, 1] - median[1, 1]), abs(median[2, 1] - median[3, 1]),
                       abs(median[0, 0] - median[3, 0]), abs(median[1, 0] - median[2, 0]))
            if skew < 3 or min(a[1], b[1]) < 0.25 or min(a[2], b[2]) < 0.44:
                continue
            actual = median * np.float32([later.shape[1] / _WIDTH, later.shape[0] / _HEIGHT])
            size = (int(np.linalg.norm(actual[1] - actual[0])), int(np.linalg.norm(actual[3] - actual[0])))
            if not _same_projection(_rectify(earlier, actual, size, cv2),
                                    _rectify(later, actual, size, cv2), cv2):
                break
            stable = None
            stable_frame = current
            if position + 1 < len(frames) and frames[position + 1].timestamp - current.timestamp <= 10:
                stable_frame = frames[position + 1]
                stable, _ = read(stable_frame.path)
            if stable is None and current.stable_duration >= 1:
                stable, stable_frame = later, current
            x, y, width, height = cv2.boundingRect(actual)
            surface = (x, y, x + width, y + height)
            if stable is not None and (
                _new_side_detail(earlier, later, stable, surface, True, cv2)
                or _side_text_replaced(previous, current, stable_frame, earlier, later, surface, cv2)
            ):
                break
            result.add(current.index)
            break
    return result
