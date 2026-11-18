"""Numerical features for the offline information-boundary ranker."""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterator
import difflib
from functools import lru_cache
import re

import cv2
import numpy as np

from .media import Frame


WIDTH, HEIGHT = 320, 180
ROWS, COLS = 6, 8
GRID = (ROWS, HEIGHT // ROWS, COLS, WIDTH // COLS)
NEAR = np.ones((3, 3), np.uint8)
TERMS = re.compile(r"[a-z0-9_]{2,}|[\u3400-\u9fff]")
PERIMETER = np.ones((HEIGHT, WIDTH), bool)
PERIMETER[HEIGHT//6:-HEIGHT//6, WIDTH//8:-WIDTH//8] = False
KINDS = ("content", "build", "uncertain", "layout", "speaker", "animation")


def _tiles(image):
    return image.reshape(GRID).mean(axis=(1, 3))


def _fraction(count, total):
    return count / max(1.e-6, total)


def _spread(grid):
    ys, xs = np.where(grid > .06)
    return [
        grid.mean(), grid.max(), np.mean(grid > .04), np.mean(grid > .15),
        grid[[0, -1], :].mean(), grid[:, [0, -1]].mean(), grid[1:-1, 1:-1].mean(),
        (np.ptp(xs) + 1) / COLS if len(xs) else 0,
        (np.ptp(ys) + 1) / ROWS if len(ys) else 0,
    ]


def _spatial_overlap(a, b):
    return _fraction(np.minimum(a, b).sum(), np.maximum(a, b).sum())


def _term_overlap(a, b):
    return len(a & b) / max(1, len(a | b))


def _text_mask(layout):
    mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    for entry in layout:
        box = entry.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        x, y, w, h = box
        x1, x2 = int(np.clip(x * WIDTH, 0, WIDTH)), int(np.clip((x+w)*WIDTH, 0, WIDTH))
        y1, y2 = int(np.clip(y * HEIGHT, 0, HEIGHT)), int(np.clip((y+h)*HEIGHT, 0, HEIGHT))
        mask[y1:y2, x1:x2] = 1
    return cv2.dilate(mask, NEAR).astype(bool)


def _flow(left, current):
    """Measure the fraction of old texture that moves as one optical plane."""
    corners = cv2.goodFeaturesToTrack(left, maxCorners=85, qualityLevel=.012, minDistance=5)
    if corners is None:
        return [0.] * 9
    new, status, _ = cv2.calcOpticalFlowPyrLK(
        left, current, corners, None, winSize=(15, 15), maxLevel=2,
    )
    good = status[:, 0] == 1
    a, b = corners[good, 0], new[good, 0]
    if len(a) < 5:
        return [len(a)/len(corners)] + [0.] * 8
    delta = b - a
    median = np.median(delta, axis=0)
    residual = np.linalg.norm(delta-median, axis=1)
    motion = np.linalg.norm(delta, axis=1)
    affine, inliers = cv2.estimateAffinePartial2D(
        a, b, method=cv2.RANSAC, ransacReprojThreshold=1.2, maxIters=70,
    )
    warped_diff = cv2.absdiff(left, current)
    rotation = scale_error = 0.0
    if affine is not None:
        warp = cv2.warpAffine(left, affine, (WIDTH, HEIGHT), borderMode=cv2.BORDER_REPLICATE)
        warped_diff = cv2.absdiff(warp, current)
        rotation = abs(affine[0, 1])
        scale_error = abs(np.linalg.norm(affine[0, :2])-1)
    return [
        len(a)/len(corners), np.linalg.norm(median), np.median(motion),
        np.quantile(motion, .85), np.mean(residual < 1.2),
        np.mean(inliers) if inliers is not None else 0,
        rotation, scale_error, np.mean(warped_diff[6:-6, 6:-6] > 18),
    ]


def _visual_delta(before, after):
    diff = after-before
    shift = np.mean(diff, axis=(0, 1))
    centered = np.max(np.abs(diff-shift), axis=2)
    tiles = (centered > 22).reshape(9,15,12,20).mean(axis=(1,3))
    return [
        np.mean(np.abs(diff)>20), np.mean(centered>16), np.mean(centered>36),
        np.mean(tiles>.07), np.mean(tiles>.25), np.max(tiles), np.mean(np.abs(shift)),
    ]


def _text_delta(before, after):
    first = {line.casefold().strip() for line in before if line.strip()}
    current = {line.casefold().strip() for line in after if line.strip()}
    old_text = " ".join(sorted(first))[:1800]
    new_text = " ".join(sorted(current))[:1800]
    return [
        len(first), len(current), len(current-first), len(first-current),
        len(first & current)/max(1,len(first | current)),
        difflib.SequenceMatcher(None,old_text,new_text).ratio(), len(new_text)-len(old_text),
    ]


def _basic(frame, previous, following, duration, position, times, raster):
    t = frame.timestamp
    next_pos = min(position+1,len(times)-1)
    follow = min(bisect_left(times,t+1.0),len(times)-1)
    left, current, next_image, later = [raster(i)[4] for i in (position-1,position,next_pos,follow)]
    end = following.timestamp if position+1<len(times) else max(duration,t)
    heights = [m["bbox"][3] for m in frame.ocr_layout if len(m.get("bbox") or ())==4]
    confidences = [m["confidence"] for m in frame.ocr_layout if m.get("confidence") is not None]
    return [
        min(20,t-previous.timestamp), min(20,end-t),
        min(12,frame.observed_sample_count), min(12,previous.observed_sample_count),
        min(12,following.observed_sample_count), np.sum(np.abs(times-t)<=3),
        *_visual_delta(left,current), *_visual_delta(current,next_image),
        *_visual_delta(left,next_image), *_visual_delta(current,later),
        *_text_delta(previous.lines,frame.lines), *_text_delta(frame.lines,following.lines),
        np.median(heights) if heights else 0, np.median(confidences) if confidences else 0,
        (frame.scene_change_score or 0) if frame.scene_id else 0,
        *[frame.change_kind==kind for kind in KINDS],
    ]


def _active_positions(times, t, offsets):
    positions = np.searchsorted(times, t + np.asarray(offsets), side="right")-1
    return np.unique(np.clip(positions, 0, len(times)-1))


def _motion_features(position, times, frames, raster, motion, words):
    t = times[position]
    previous = position-1
    following = min(position+1,len(times)-1)
    color, gray, edge, thick_edge, _ = raster(position)
    old_color, old_gray, old_edge, old_thick, _ = raster(previous)
    next_gray = raster(following)[1]
    signed = color.astype(np.int16)-old_color
    changed = np.max(np.abs(signed-np.median(signed[::8, ::8],axis=(0,1))),axis=2)>20
    new_ink = edge & ~old_thick
    gone_ink = old_edge & ~thick_edge
    ink = new_ink | gone_ink
    mask = _text_mask(frames[position].ocr_layout) | _text_mask(frames[previous].ocr_layout)
    delta = cv2.absdiff(old_gray,gray)
    features = [
        *_spread(motion[position]), *_spread(_tiles(ink)), *_flow(old_gray,gray),
        old_edge.mean(), edge.mean(), mask.mean(), _fraction((changed & mask).sum(),changed.sum()),
        _fraction((ink & mask).sum(),ink.sum()), np.mean(ink[PERIMETER]), np.mean(ink[~PERIMETER]),
        np.mean(delta<8), np.mean(delta>20),
        np.median(gray), np.std(gray), np.mean(gray>=224), np.mean(gray<=32),
        np.mean(np.ptp(color,axis=2)>=35),
        _spatial_overlap(motion[position],motion[following]),
        _spatial_overlap(motion[position],motion[previous]),
        np.mean(cv2.absdiff(old_gray,next_gray)>20),
    ]
    for left_offsets, right_offsets in (
        ((-1.7,-.7,-.15),(.05,.65,1.55)),
        ((-5.,-3.,-1.5,-.15),(.05,.7,1.6,3.,5.)),
    ):
        before = _active_positions(times,t,left_offsets)
        after = _active_positions(times,t,right_offsets)
        dark_before = np.stack([raster(i)[1] for i in before])
        dark_after = np.stack([raster(i)[1] for i in after])
        old_edges = np.stack([raster(i)[3] for i in before])
        new_edges = np.stack([raster(i)[3] for i in after])
        low_left, high_left = dark_before.min(axis=0), dark_before.max(axis=0)
        low_right, high_right = dark_after.min(axis=0), dark_after.max(axis=0)
        between = np.maximum(low_right.astype(np.int16)-high_left,low_left.astype(np.int16)-high_right)
        persistent = (between > 17) & changed
        novel = ((new_ink & new_edges.all(axis=0) & ~old_edges.any(axis=0))
                 | (gone_ink & old_edges.all(axis=0) & ~new_edges.any(axis=0)))
        moving_left = (high_left-low_left)>18
        moving_right = (high_right-low_right)>18
        moving = moving_left | moving_right
        features.extend((
            len(before),len(after),moving_left.mean(),moving_right.mean(),moving.mean(),
            _spatial_overlap(motion[position],_tiles(moving)), _fraction(persistent.sum(),changed.sum()),
            _fraction(novel.sum(),ink.sum()), persistent[PERIMETER].mean(),persistent[~PERIMETER].mean(),
            *_spread(_tiles(novel)), *_spread(_tiles(persistent)),
        ))
        previous_words = [words[i] for i in before]
        later_words = [words[i] for i in after]
        current_words = words[position]
        novelty = current_words - set().union(*previous_words)
        carried = set.intersection(*later_words) if len(later_words)>1 else set()
        features.extend((
            len(novelty), len(novelty & carried),
            max(_term_overlap(current_words,sample) for sample in previous_words),
            max(_term_overlap(current_words,sample) for sample in later_words[1:]) if len(later_words)>1 else 0,
        ))
    lo, hi = np.searchsorted(times, [t-12,t+12])
    nearby = np.concatenate((motion[lo:position],motion[position+1:hi]), axis=0)
    typical = nearby.mean(axis=0) if len(nearby) else np.zeros_like(motion[position])
    fixed = motion[position] * (typical < .035)
    features.extend((max(0,hi-lo-1),typical.mean(),_spatial_overlap(typical,motion[position]),
                     _fraction(fixed.sum(),motion[position].sum())))
    return features


def features(frames: list[Frame], duration: float) -> Iterator[tuple[int, tuple[float, ...]]]:
    """Yield training-compatible features using a bounded raster cache."""
    times = np.asarray([frame.timestamp for frame in frames])
    words = [set(TERMS.findall(" ".join(frame.lines).casefold())) for frame in frames]

    @lru_cache(maxsize=40)
    def raster(index):
        path = frames[index].path
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError("A saved frame could not be read.")
        color = cv2.resize(image,(WIDTH,HEIGHT),interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(color,cv2.COLOR_BGR2GRAY)
        edge = np.abs(cv2.Laplacian(gray,cv2.CV_16S,ksize=1))>16
        thick = cv2.dilate(edge.astype(np.uint8),NEAR).astype(bool)
        tiny = cv2.resize(color,(240,135),interpolation=cv2.INTER_AREA).astype(np.float32)
        return color,gray,edge,thick,tiny

    motion = np.zeros((len(frames),ROWS,COLS),dtype=np.float64)
    for i in range(1,len(frames)):
        motion[i] = _tiles(cv2.absdiff(raster(i-1)[1],raster(i)[1])>20)

    for i, frame in enumerate(frames[1:],1):
        t = frame.timestamp
        if not 0<t<duration or frame.ocr_warning or frame.dedupe_warning:
            continue
        basic = _basic(frame,frames[i-1],frames[min(i+1,len(frames)-1)],duration,i,times,raster)
        extra = _motion_features(i,times,frames,raster,motion,words)
        extra.extend((
            round(frame.stable_duration,3),frame.ocr_char_count,frame.transcript_novelty,
            frame.transcript_overlap,(frame.source_timestamp or t)-t,
        ))
        yield i, tuple(round(float(value),7) for value in [*basic,*extra])
