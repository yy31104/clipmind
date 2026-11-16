"""Conservative local evidence for motion inside a recurring speaker pane.

Face detections locate a bounded pane; they do not identify a person. A pane
never exempts the rest of the screen from pixel comparison, including diagrams
with no readable text. Unproved cases remain informational boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import screen_faces
from .media import Frame

Box = tuple[float, float, float, float]
_WIDTH, _HEIGHT = 640, 360


@dataclass(frozen=True)
class MotionEvidence:
    kind: str = "uncertain"
    reason: str = "No supported speaker pane with an unchanged surrounding screen."
    regions: tuple[Box, ...] = ()
    supporting_frames: int = 0


def _pixels(frame: Frame) -> np.ndarray:
    with Image.open(frame.path) as image:
        return np.asarray(image.convert("RGB").resize(
            (_WIDTH, _HEIGHT), Image.Resampling.BILINEAR), dtype=np.int16)


def _pane_regions(pixels: np.ndarray, faces: tuple[Box, ...]) -> tuple[Box, ...]:
    """Find bounded foreground panes containing a face, relative to a flat gutter.

    A body-sized rectangle extrapolated from a face could cover part of a new
    diagram. Instead, the image itself must supply the pane's connected extent.
    Large camera views and interior diagram components are deliberately rejected.
    """
    if not faces:
        return ()
    try:
        import cv2
    except ImportError:
        return ()
    height, width = pixels.shape[:2]
    border = np.concatenate((pixels[:3].reshape(-1, 3), pixels[-3:].reshape(-1, 3),
                             pixels[:, :3].reshape(-1, 3), pixels[:, -3:].reshape(-1, 3)))
    quantized = border // 8
    codes = quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2]
    background = np.median(border[codes == np.bincount(codes).argmax()], axis=0)
    foreground = (np.max(np.abs(pixels - background), axis=2) > 24).astype(np.uint8)
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(foreground)
    regions = []
    for x, y, face_width, face_height in faces:
        patch = labels[max(0, int(y * height)):min(height, int((y + face_height) * height)),
                       max(0, int(x * width)):min(width, int((x + face_width) * width))]
        ids, counts = np.unique(patch[patch > 0], return_counts=True)
        if not len(ids):
            continue
        left, top, pane_width, pane_height, area = stats[ids[np.argmax(counts)]]
        if (pane_width * pane_height > width * height * 0.4
                or pane_width < face_width * width * 2
                or pane_height < face_height * height * 3
                or min(left, width - left - pane_width) > width * 0.04
                or area < pane_width * pane_height * 0.4):
            continue
        regions.append((left / width, top / height, pane_width / width, pane_height / height))
    return tuple(dict.fromkeys(regions))


def _overlap(first: Box, second: Box) -> float:
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    return intersection / (first[2] * first[3] + second[2] * second[3] - intersection)


def _supported_region(frames: list[Frame], panes: list[tuple[Box, ...]],
                      position: int) -> tuple[Box | None, int]:
    nearby = [(index, region) for index, candidates in enumerate(panes)
              if abs(frames[index].timestamp - frames[position].timestamp) <= 12
              for region in candidates]
    for _, region in nearby:
        cluster = [(index, other) for index, other in nearby if _overlap(region, other) > 0.9]
        support = len({index for index, _ in cluster})
        if support < 3:
            continue
        left = max(0.0, min(box[0] for _, box in cluster) - 3 / _WIDTH)
        top = max(0.0, min(box[1] for _, box in cluster) - 3 / _HEIGHT)
        right = min(1.0, max(box[0] + box[2] for _, box in cluster) + 3 / _WIDTH)
        bottom = min(1.0, max(box[1] + box[3] for _, box in cluster) + 3 / _HEIGHT)
        return (left, top, right - left, bottom - top), support
    return None, 0


def _motion_confined(before: np.ndarray, after: np.ndarray, region: Box) -> bool:
    changed = np.max(np.abs(after - before), axis=2) > 24
    x, y, width, height = region
    left, right = round(x * _WIDTH), round((x + width) * _WIDTH)
    top, bottom = round(y * _HEIGHT), round((y + height) * _HEIGHT)
    inside = np.count_nonzero(changed[top:bottom, left:right])
    changed[top:bottom, left:right] = False
    # A tiny total alone can hide a small code token. Local tiles must also be
    # unchanged; only isolated compression specks can pass this comparison.
    return (inside > 64 and np.count_nonzero(changed) <= 16
            and changed.reshape(30, 12, 40, 16).sum(axis=(1, 3)).max() <= 3)


def screen_motion_evidence(frames: list[Frame]) -> dict[int, MotionEvidence]:
    """Return boundary evidence without changing, dropping, or reordering frames.

    Three actual detections must support the same bounded pane within twelve
    seconds. A missed detection may borrow that local support, but a title or OCR
    bounding hull never serves as the screen-stability test.
    """
    result = {frame.index: MotionEvidence() for frame in frames}
    if len(frames) < 3:
        return result
    panes = []
    for frame in frames:
        try:
            panes.append(_pane_regions(_pixels(frame), screen_faces.boxes(frame.path)))
        except (OSError, ValueError):
            panes.append(())
    before = None
    for position, frame in enumerate(frames):
        try:
            after = _pixels(frame)
        except (OSError, ValueError):
            before = None
            continue
        region, support = _supported_region(frames, panes, position)
        if (position and before is not None and region is not None
                and not frame.ocr_warning and not frame.dedupe_warning
                and _motion_confined(before, after, region)):
            result[frame.index] = MotionEvidence(
                "speaker", "Motion confined to a recurring face-supported pane; surrounding screen unchanged.",
                (region,), support)
        before = after
    return result


def annotate_screen_motion(frames: list[Frame]) -> dict[int, str]:
    """Convenience mapping for callers that only need the boundary kind."""
    return {index: evidence.kind for index, evidence in screen_motion_evidence(frames).items()}
