"""Find localized, persistent edits among near-duplicate screen samples."""
from bisect import bisect_left, bisect_right
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


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
                or last >= len(self.times) or t - self.times[first] > 1.95
                or self.times[last] - t > 1.95):
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
            return _settled_patch(
                before, lead, tail, delta, strong, stable, replacing_representative,
            )
        except (OSError, ValueError, ImportError):
            return False


def _settled_patch(before, lead, tail, delta, strong, stable, replacing_representative) -> bool:
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
