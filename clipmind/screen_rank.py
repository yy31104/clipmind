"""Pick offline informational boundaries while keeping the canonical timeline."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import logging
import math
import struct

from .media import Frame
from .screen_rank_model import (
    CUTOFF,
    FOREST_CENTER,
    FOREST_SCALE,
    FOREST_TREES,
    LOGISTIC_BIAS,
    LOGISTIC_COEFFICIENTS,
)

ALGORITHM = "offline-screen-rank-v1"
_LOGGER = logging.getLogger(__name__)
_WINDOW_SECONDS = 1.0
_FLOAT = struct.Struct("<f")


def _float32(value: float) -> float:
    return _FLOAT.unpack(_FLOAT.pack(value))[0]


def _score(values: tuple[float, ...]) -> float:
    if len(values) != len(LOGISTIC_COEFFICIENTS) or not all(map(math.isfinite,values)):
        raise ValueError("Invalid screen-ranker feature vector.")
    transformed = [math.copysign(math.log1p(abs(value)),value) for value in values]
    log_odds = LOGISTIC_BIAS + math.fsum(
        coefficient * value for coefficient, value in zip(LOGISTIC_COEFFICIENTS,transformed)
    )
    exp_magnitude = math.exp(-abs(log_odds))
    logistic = (1. if log_odds >= 0. else exp_magnitude) / (1.+exp_magnitude)
    forest = [
        _float32((value-center)/scale)
        for value, center, scale in zip(transformed,FOREST_CENTER,FOREST_SCALE)
    ]
    yes = 0
    for tree in FOREST_TREES:
        node = 0
        while True:
            feature, threshold, left, right = tree[node]
            if feature == -1:
                yes += int(threshold)
                break
            node = left if forest[feature] <= threshold else right
    return (logistic + yes/len(FOREST_TREES))/2


def _select(times: list[float], scores: dict[int, float], warnings: set[int],
            cutoff: float = CUTOFF) -> set[int]:
    selected = set(warnings)
    blocked = set()

    def suppress(position: int) -> None:
        t = times[position]
        bound = math.nextafter(_WINDOW_SECONDS, math.inf)
        left = bisect_left(times, math.nextafter(t-bound, -math.inf))
        right = bisect_right(times, math.nextafter(t+bound, math.inf))
        blocked.update(i for i in range(left,right) if abs(times[i]-t)<=_WINDOW_SECONDS)

    for position in warnings:
        suppress(position)
    for position in sorted(scores,key=lambda i:(-scores[i],i)):
        if scores[position] < cutoff:
            break
        if position in blocked:
            continue
        selected.add(position)
        suppress(position)
    return selected


def annotate_information_boundaries(frames: list[Frame], duration: float) -> bool:
    """Make optional decisions atomically, with legacy classification as fallback."""
    times = [frame.timestamp for frame in frames]
    if (not math.isfinite(duration) or duration <= 0 or not all(map(math.isfinite,times))
            or any(a>=b for a,b in zip(times,times[1:]))):
        return False
    eligible = {i for i,t in enumerate(times) if i and 0<t<duration}
    if not eligible:
        return True
    warnings = {i for i in eligible if frames[i].ocr_warning or frames[i].dedupe_warning}
    try:
        import cv2
        from .screen_rank_features import features
    except ImportError as exc:
        _LOGGER.warning("Using existing boundary classification because the ranker is unavailable: %s",exc)
        return False
    try:
        scores = {i:_score(values) for i,values in features(frames,duration)}
        if set(scores) != eligible - warnings:
            raise ValueError("Screen-ranker feature coverage differs from the timeline.")
        selected = _select(times,scores,warnings)
    except (OSError,ValueError,TypeError,ArithmeticError,cv2.error) as exc:
        _LOGGER.warning("Using existing boundary classification because ranking failed: %s",exc)
        return False
    for position in eligible:
        frames[position].information_boundary = position in selected
    return True
