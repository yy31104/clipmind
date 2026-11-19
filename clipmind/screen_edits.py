"""Find localized, persistent edits among near-duplicate screen samples."""
from bisect import bisect_left, bisect_right
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


def viewport_scrolled(
    earlier: np.ndarray, before: np.ndarray, after: np.ndarray, following: np.ndarray,
) -> bool:
    """Track a settled viewport through a vertical shift in 320×180 thumbnails."""
    try:
        import cv2
    except ImportError:
        return False

    def edges(gray):
        gray = gray.astype(np.float32)
        return gray - cv2.GaussianBlur(gray, (0, 0), 1.2)

    earlier, before, after, following = map(edges, (earlier, before, after, following))
    height, width = before.shape
    strip_height, strip_width = 28, 64
    search_radius = 22
    minimum_score = .72
    matches = []
    for top in range(search_radius, height - search_radius - strip_height + 1, strip_height // 2):
        for left in range(12, width - strip_width + 1, 40):
            strip = before[top:top + strip_height, left:left + strip_width]
            if np.std(strip) < 3:
                continue
            after_strip = after[top - search_radius:top + strip_height + search_radius, left:left + strip_width]
            scores = cv2.matchTemplate(after_strip, strip, cv2.TM_CCOEFF_NORMED).ravel()
            best = int(np.argmax(scores))
            offset = best - search_radius
            if abs(offset) < 3 or scores[best] < minimum_score or scores[best] - scores[search_radius] < .33:
                continue
            lead = earlier[top:top + strip_height, left:left + strip_width]
            if cv2.matchTemplate(lead, strip, cv2.TM_CCOEFF_NORMED)[0, 0] < minimum_score:
                continue
            shifted_top = top + offset
            first = max(0, shifted_top - search_radius)
            last = min(height, shifted_top + strip_height + search_radius)
            shifted = after[shifted_top:shifted_top + strip_height, left:left + strip_width]
            later = following[first:last, left:left + strip_width]
            continuation = cv2.matchTemplate(later, shifted, cv2.TM_CCOEFF_NORMED).ravel()
            next_best = int(np.argmax(continuation))
            next_offset = first + next_best - shifted_top
            if continuation[next_best] < minimum_score or (abs(next_offset) > 1 and next_offset * offset < 0):
                continue
            for other_left, other_top, other_offset in matches:
                if (strip_height <= top - other_top <= 2 * strip_height
                        and abs(left - other_left) <= strip_width and abs(offset - other_offset) <= 1):
                    return True
            matches.append((left, top, offset))
    return False


class SettledEdits:
    def __init__(self, samples: list[tuple[float, Path]]):
        paths = [path for _, path in samples]
        self.times = [timestamp for timestamp, _ in samples]

        @lru_cache(maxsize=12)
        def raster(position: int) -> np.ndarray:
            with Image.open(paths[position]) as image:
                gray = image.convert("L")
                gray.thumbnail((640, 640), Image.Resampling.BILINEAR)
                return np.asarray(gray, dtype=np.int16)

        self._raster = raster

    def at(self, position: int, *, replacing_representative: bool) -> bool:
        """Require changed strokes to be visible across two short stable runs."""
        t = self.times[position]
        first = bisect_right(self.times, t - 1.25) - 1
        last = bisect_left(self.times, t + 1.25)
        if (first < 0 or first >= position - 1 or last <= position + 1
                or last >= len(self.times) or t - self.times[first] > 2.05
                or self.times[last] - t > 2.05):
            return False
        try:
            before, current = self._raster(position - 1), self._raster(position)
            if before.shape != current.shape:
                return False
            delta = current - before
            strong = np.abs(delta) >= 24
            count = int(np.count_nonzero(strong))
            if not 8 <= count <= 0.026 * strong.size:
                return False
            older = {first, (first + position - 2) // 2, position - 2}
            newer = {position + 1, (position + 1 + last) // 2, last}
            lead = [self._raster(i) for i in older]
            tail = [np.abs(current - self._raster(i)) for i in newer]
            stable = np.abs(delta) >= 12
            for raster in lead:
                stable &= np.abs(current - raster) >= 12
            for difference in tail:
                stable &= difference <= 10

            def returns_to_earlier_view(region, visible):
                start = bisect_left(self.times, t - 7.0)
                step = max(1, (first - start) // 5)
                ink = current[region][visible]
                for j in range(start, first, step):
                    try:
                        past = self._raster(j)
                    except (OSError, ValueError):
                        continue
                    if past.shape == current.shape and np.mean(np.abs(ink - past[region][visible]) <= 12) >= .85:
                        return True
                return False

            return _settled_patch(
                before, lead, tail, delta, strong, stable, replacing_representative,
                returns_to_earlier_view,
            )
        except (OSError, ValueError, ImportError):
            return False


def _settled_patch(
    before, lead, tail, delta, strong, stable, replacing_representative, returns_to_earlier_view,
) -> bool:
    if np.count_nonzero(strong & stable) < 8:
        return False
    import cv2

    height, width = stable.shape
    joined = cv2.dilate(stable.astype(np.uint8), np.ones((5, 9), np.uint8))
    _, _, patches, _ = cv2.connectedComponentsWithStats(joined)
    low_motion = (
        np.mean(np.abs(delta)) < 0.25
        and min(np.mean(np.abs(before - frame)) for frame in lead) < 0.12
        and np.median([np.mean(frame) for frame in tail]) < 0.12
    )
    count = np.count_nonzero(strong)
    for x, y, w, h, _ in patches[1:]:
        if not (5 <= w <= width / 4 and 6 <= h <= height / 5):
            continue
        region = np.s_[y:y+h, x:x+w]
        mask = stable[region]
        measured = delta[region]
        positive = mask & (measured > 0)
        negative = mask & (measured < 0)
        replaced = min(np.count_nonzero(positive), np.count_nonzero(negative))
        pixels = np.count_nonzero(mask)
        if low_motion and _settled_ink(
            before, delta, tail, stable, x, y, w, h, count, returns_to_earlier_view,
        ):
            return True
        if (pixels < 18 or replaced < 6 or np.count_nonzero(strong[region] & mask) < 8
                or np.max(np.abs(measured[mask])) < 42):
            continue
        _, _, span_w, span_h = cv2.boundingRect(mask.astype(np.uint8))
        if min(span_w, span_h) < 4:
            continue
        padded = np.s_[max(0, y-8):y+h+8, max(0, x-8):x+w+8]
        # A shape that changes against a flat field can warrant its own
        # screenshot even when the surrounding application chrome moves.
        artwork = False
        if (replacing_representative and min(span_w, span_h) >= 12
                and min(np.count_nonzero(mask & (measured >= 96)),
                        np.count_nonzero(mask & (measured <= -96))) >= 100):
            background = before[padded]
            tone = np.median(background)
            artwork = np.mean((np.abs(background - tone) <= 16)
                              & (np.abs(background + delta[padded] - tone) <= 16)) >= 0.65
        if not artwork:
            if (not low_motion or count - np.count_nonzero(strong[padded]) > max(8, 0.17 * pixels)
                    or max(np.count_nonzero(frame[region] >= 24) for frame in tail)
                    > max(15, 0.43 * pixels)):
                continue
        # Two independently durable contrast directions certify a local
        # replacement. A filled blinking or relocated caret can also give
        # both directions, so exclude patches dominated by solid blocks.
        solid = 0
        for polarity in (positive, negative):
            _, _, components, _ = cv2.connectedComponentsWithStats(polarity.astype(np.uint8))
            solid += sum(area for _, _, rw, rh, area in components[1:]
                         if rw >= 3 and rh >= 5 and area >= 0.87 * rw * rh)
        if solid < 0.75 * pixels:
            return True
    return False


def _settled_ink(before, delta, tail, stable, x, y, width, height, count, returns_to_earlier_view):
    # A solid cursor can cover an inserted glyph before it blinks off.
    # Check the final ink shape and the surrounding text in those cases.
    rows, columns = before.shape
    if not (10 <= width <= 54 and 9 <= height <= 28
            and .11 * rows < y < .86 * rows and count <= 370):
        return False
    region = np.s_[y:y+height, x:x+width]
    if np.count_nonzero(stable[region]) < 31:
        return False
    area = np.s_[max(0, y-22):y+height+22, max(0, x-26):min(columns, x+width+26)]
    tone = np.median(before[area])
    old = before[region]
    new = old + delta[region]
    if np.mean(np.abs(before[area] - tone) < 24) < .72:
        return False
    import cv2

    printed = np.abs(new - tone) >= 38
    for difference in tail:
        printed &= difference[region] <= 15
    _, components, glyphs, _ = cv2.connectedComponentsWithStats(printed.astype(np.uint8))
    supported = np.bincount(components[stable[region]], minlength=len(glyphs))
    context = np.abs(before[area] - tone) >= 38
    if np.count_nonzero(context) - np.count_nonzero(np.abs(old - tone) >= 38) < 36:
        return False
    return any(
        3 <= gw <= 14 and 5 <= gh <= 17 and 9 <= n < .78 * gw * gh
        and supported[index] >= max(2, .13 * n)
        and not returns_to_earlier_view(region, (components == index) & stable[region])
        for index, (_, _, gw, gh, n) in enumerate(glyphs[1:], 1)
    )
