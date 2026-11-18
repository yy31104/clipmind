"""Conservative local evidence for motion in recurring speaker views.

Face detections locate a bounded pane; they do not identify a person. A pane
never exempts the rest of the screen from pixel comparison, including diagrams
with no readable text. Unproved cases remain informational boundaries.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import screen_faces
from .media import Frame

Box = tuple[float, float, float, float]
FaceReader = Callable[[Path], tuple[Box, ...]]
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


def screen_motion_evidence(
    frames: list[Frame], *, face_boxes: FaceReader | None = None,
) -> dict[int, MotionEvidence]:
    """Return boundary evidence without changing, dropping, or reordering frames.

    Three actual detections must support the same bounded pane within twelve
    seconds. A missed detection may borrow that local support, but a title or OCR
    bounding hull never serves as the screen-stability test.
    """
    result = {frame.index: MotionEvidence() for frame in frames}
    if len(frames) < 3:
        return result
    detect_faces = face_boxes or screen_faces.boxes
    panes = []
    for frame in frames:
        try:
            panes.append(_pane_regions(_pixels(frame), detect_faces(frame.path)))
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


_ROOM_WIDTH, _ROOM_HEIGHT = 320, 180
_ROOM_MARGIN = (_ROOM_WIDTH // 5, 130)
_ROOM_WITNESS_SECONDS = 8.0
_ROOM_NATIVE_PIXEL_LIMIT = 6_000_000
FULL_FRAME_SPEAKER_REASON = "Motion confined to the presenter in a recurring full-frame room view."


@dataclass(frozen=True)
class _RoomView:
    pixels: np.ndarray
    foreground: np.ndarray


def _room_margins(pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    width, height = _ROOM_MARGIN
    return pixels[:height, :width], pixels[:height, -width:]


def _room_view(pixels: np.ndarray, cv2) -> _RoomView | None:
    """Observe a central foreground component against two quiet wall margins."""
    small = cv2.resize(pixels, (_ROOM_WIDTH, _ROOM_HEIGHT), interpolation=cv2.INTER_AREA).astype(np.int16)
    margins = _room_margins(small)
    colors = [np.median(side.reshape(-1, 3), axis=0) for side in margins]
    if any(np.mean(np.max(np.abs(side - color), axis=2) <= 14) < 0.94
           for side, color in zip(margins, colors, strict=True)):
        return None
    wall = np.linspace(*colors, _ROOM_WIDTH)[None, :, :]
    mask = np.uint8(np.max(np.abs(small - wall), axis=2) >= 23)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), dtype=np.uint8))
    count, labels, components, centers = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return None
    component = 1 + int(np.argmax(components[1:, cv2.CC_STAT_AREA]))
    left, top, width, height, area = components[component]
    if (left <= 10 or left + width >= _ROOM_WIDTH - 10 or top > 65 or height < 85
            or not 0.05 <= area / mask.size <= 0.60
            or not _ROOM_WIDTH / 4 <= centers[component, 0] <= 3 * _ROOM_WIDTH / 4):
        return None
    points = cv2.findNonZero(np.uint8(labels == component))
    foreground = np.zeros_like(mask)
    cv2.fillConvexPoly(foreground, cv2.convexHull(points), 1)
    foreground = cv2.dilate(foreground, np.ones((9, 9), dtype=np.uint8))
    return _RoomView(small, foreground.astype(bool))


def _same_room_margins(first: _RoomView, second: _RoomView) -> bool:
    delta = np.max(np.abs(first.pixels - second.pixels), axis=2)
    return all(np.mean(side <= 12) >= 0.99 for side in _room_margins(delta))


def _has_room_face(view: _RoomView, faces: tuple[Box, ...]) -> bool:
    return any(width >= 24 / _ROOM_WIDTH and height >= 24 / _ROOM_HEIGHT
               and 0 <= x + width / 2 < 1 and 0 <= y + height / 2 < 1
               and view.foreground[int((y + height / 2) * _ROOM_HEIGHT), int((x + width / 2) * _ROOM_WIDTH)]
               for x, y, width, height in faces)


def _connected_change(mask: np.ndarray, minimum: int, cv2) -> bool:
    if np.count_nonzero(mask) < minimum:
        return False
    _, _, components, _ = cv2.connectedComponentsWithStats(np.uint8(mask))
    return bool(np.any(components[1:, cv2.CC_STAT_AREA] >= minimum))


def _room_native_motion(first: np.ndarray, second: np.ndarray, foreground: np.ndarray, cv2) -> bool:
    """Require confined native changes, including small strokes and new colors."""
    if first.shape != second.shape:
        return False
    height, width = first.shape[:2]
    inside = cv2.resize(np.uint8(foreground), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
    changed = np.max(cv2.absdiff(first, second), axis=2) >= 25
    if _connected_change(changed & ~inside, 5, cv2):
        return False
    # A newly introduced object can join the silhouette. At least retain
    # connected patches with colors outside the previous image's neighborhood.
    occupied = np.zeros((16, 16, 16), dtype=bool)
    occupied[tuple(np.moveaxis(first // 16, 2, 0))] = True
    neighborhoods = np.lib.stride_tricks.sliding_window_view(np.pad(occupied, 1), (3, 3, 3))
    known = np.any(neighborhoods, axis=(-3, -2, -1))
    novel = ~known[tuple(np.moveaxis(second // 16, 2, 0))]
    return not _connected_change(changed & novel, 12, cv2)


def full_frame_speaker_positions(
    frames: list[Frame], candidates: list[int], *, face_boxes: FaceReader | None = None,
) -> set[int]:
    """Certify a recurring close-up without letting it establish an information anchor."""
    if not candidates or len(frames) < 3:
        return set()
    try:
        import cv2
    except ImportError:
        return set()
    detect_faces = face_boxes or lru_cache(maxsize=8)(screen_faces.boxes)

    @lru_cache(maxsize=3)
    def native(position: int) -> np.ndarray | None:
        try:
            with Image.open(frames[position].path) as image:
                if image.width * image.height <= _ROOM_NATIVE_PIXEL_LIMIT:
                    return np.array(image.convert("RGB"))
        except (OSError, ValueError):
            pass
        return None

    @lru_cache(maxsize=6)
    def room(position: int) -> _RoomView | None:
        pixels = native(position)
        return _room_view(pixels, cv2) if pixels is not None else None

    supported = set()
    for position in candidates:
        if position == 0 or frames[position].timestamp - frames[position - 1].timestamp > _ROOM_WITNESS_SECONDS:
            continue
        previous, current = room(position - 1), room(position)
        if previous is None or current is None or not _same_room_margins(previous, current):
            continue
        foreground = previous.foreground | current.foreground
        if np.count_nonzero(previous.foreground & current.foreground) < 0.6 * np.count_nonzero(foreground):
            continue
        changed = np.max(np.abs(previous.pixels - current.pixels), axis=2) >= 24
        if np.count_nonzero(changed) < 12 or np.count_nonzero(changed & ~foreground) > 18:
            continue
        witnesses = []
        for index in (position - 2, position + 1):
            if (0 <= index < len(frames)
                    and abs(frames[index].timestamp - frames[position].timestamp) <= _ROOM_WITNESS_SECONDS
                    and (view := room(index)) is not None and _same_room_margins(view, current)):
                witnesses.append((index, view))
        if not witnesses:
            continue
        same_room = [(position - 1, previous), (position, current), *witnesses]
        if sum(_has_room_face(view, detect_faces(frames[index].path)) for index, view in same_room) < 2:
            continue
        first, second = native(position - 1), native(position)
        if first is not None and second is not None and _room_native_motion(first, second, foreground, cv2):
            supported.add(position)
    return supported
