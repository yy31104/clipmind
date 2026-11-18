"""On-device OCR through Apple's Vision framework.

Free, no model download, and genuinely good at Chinese - which makes it a
better fit here than shipping a PaddleOCR/ONNX stack just for this step.

Two things this module is careful about:
  * Frameworks are imported at module load, in the main thread. PyObjC resolves
    framework symbols lazily and that resolution is not reliable from the
    worker threads we OCR on.
  * Vision reads the file URL directly, so we never touch ImageIO/Quartz.
"""
from __future__ import annotations

import threading
import shutil
import math
import tempfile
import atexit
import subprocess
import sys
import os
import json
import queue
import signal
from collections import deque
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, settings

_IMPORT_ERROR: str | None = None
try:
    import objc
    import Vision
    from Foundation import NSURL
except Exception as exc:  # noqa: BLE001 - macOS-only dependency
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    objc = Vision = NSURL = None  # type: ignore[assignment]

_probe_lock = threading.Lock()
_probe_result: tuple[bool, str | None] | None = None
_vision_lock = threading.Lock()
_worker_lock = threading.Lock()
_worker_process = _worker_connection = _worker_idle_timer = None
_worker_reader = _worker_error_reader = _worker_replies = None
_worker_stderr = deque(maxlen=16)
_worker_stderr_tail = ""
_worker_module_path = None
_worker_requests = 0
_VISION_REQUEST_TIMEOUT = 10.0
_VISION_MAX_REQUESTS = 32
_VISION_IDLE_SECONDS = 30.0
RECOVER_MAX_COVERED = 0.5
RECOVER_MIN_INK = 0.05
DARK_PANEL_MIN_SHARE = 0.04
VISION_DARK_PANEL_LIMIT = 64
LOCAL_CONTRAST_PIXEL_LIMIT = 6_000_000


class OCRError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRBlock:
    text: str
    confidence: float | None = None
    # Normalized top-left coordinates: x, y, width, height.
    bbox: tuple[float, float, float, float] | None = None

    def public(self) -> dict:
        value: dict = {"text": self.text}
        if self.confidence is not None:
            value["confidence"] = round(self.confidence, 4)
        if self.bbox is not None:
            value["bbox"] = [round(number, 6) for number in self.bbox]
        return value


@dataclass(frozen=True)
class OCRResult:
    blocks: tuple[OCRBlock, ...]

    @property
    def lines(self) -> list[str]:
        return [block.text for block in self.blocks if block.text]


@dataclass(frozen=True)
class _PreparedOCR:
    path: Path
    page_mode: int = 11


def _foreground_threshold(histogram: list[int]) -> int:
    """Otsu separation of a dark background layer from brighter foreground."""
    total = sum(histogram)
    weighted = sum(value * count for value, count in enumerate(histogram))
    background_count = background_sum = 0
    best_score = -1.0
    threshold = 0
    for value, count in enumerate(histogram):
        background_count += count
        background_sum += value * count
        foreground_count = total - background_count
        if not background_count or not foreground_count:
            continue
        difference = (background_sum / background_count -
                      (weighted - background_sum) / foreground_count)
        score = background_count * foreground_count * difference * difference
        if score > best_score:
            best_score, threshold = score, value
    return threshold


def _dark_code_image(rgb, cv2):
    """Lift dim strokes relative to the surrounding color without binarizing."""
    import numpy as np

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    background = cv2.morphologyEx(rgb, cv2.MORPH_OPEN, kernel)
    background = cv2.GaussianBlur(background, (0, 0), sigmaX=4.0)
    strokes = cv2.subtract(rgb, background).max(axis=2)
    upper = float(np.quantile(strokes, 0.97))
    gain = min(3.6, max(1.0, 235 / max(1.0, upper)))
    contrasted = 255 - np.clip(strokes.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    local_gray = cv2.blur(gray, (25, 25))
    light_surface = local_gray >= 160
    dark_strokes = cv2.subtract(local_gray, gray)
    light_contrast = 255 - np.minimum(255, dark_strokes.astype(np.uint16) * 2)
    contrasted[light_surface] = np.minimum(gray[light_surface], light_contrast[light_surface])
    strokes[light_surface] = 0
    return contrasted, strokes


def _has_dense_code_rows(strokes, cv2) -> bool:
    import numpy as np

    height, width = strokes.shape
    ink = (strokes >= 30).astype(np.uint8)
    connected = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                                 np.ones((1, max(9, round(width / 95))), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(connected)
    rows = [(x, y, w, h) for x, y, w, h, area in stats[1:]
            if w >= 0.055 * width and 0.005 * height <= h < 0.047 * height and area >= 0.14 * w * h]

    def row_count(group):
        return len({round((y + h / 2) / max(3, 0.011 * height)) for _, y, _, h in group})

    if row_count(rows) < 5 or max(y + h for _, y, _, h in rows) - min(y for _, y, _, _ in rows) <= 0.1 * height:
        return False
    # A nearly full dark frame can still contain two independent editors.
    # Preserve sparse segmentation when both sides of a wide gutter hold text.
    margin = round(0.03 * width)
    for split in range(width // 5, 4 * width // 5, max(1, margin)):
        left = [row for row in rows if row[0] + row[2] <= split - margin]
        right = [row for row in rows if row[0] >= split + margin]
        if len(left) + len(right) != len(rows) or min(row_count(left), row_count(right)) < 5:
            continue
        overlap = (min(max(y + h for _, y, _, h in left), max(y + h for _, y, _, h in right))
                   - max(min(y for _, y, _, _ in left), min(y for _, y, _, _ in right)))
        if overlap >= 0.12 * height:
            return False
    return True


@contextmanager
def _recognition_image(path: Path, *, portable: bool = False, scale: float | None = None):
    """A bounded, temporary OCR derivative; evidence images stay untouched."""
    from PIL import Image, ImageChops, ImageOps

    with Image.open(path) as original:
        image = original.convert("RGB")
    width, height = image.size
    # Value, rather than luminance, preserves bright red/green code syntax.
    red, green, blue = image.split()
    value = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = value.histogram()
    total = width * height
    dark_share = sum(histogram[:96]) / total
    bright_share = sum(histogram[192:]) / total
    dark = dark_share >= 0.7 and bright_share < 0.12
    page_mode = 11
    enhanced = False
    try:
        import cv2
    except ImportError:
        cv2 = None
    use_local_contrast = cv2 is not None and total <= LOCAL_CONTRAST_PIXEL_LIMIT
    if dark and use_local_contrast:
        import numpy as np

        pixels, strokes = _dark_code_image(np.asarray(image), cv2)
        image = Image.fromarray(pixels)
        enhanced = True
        # For a nearly full-frame dark block, sparse segmentation drops code
        # rows, especially includes and comments. Keep it for mixed layouts.
        if portable and dark_share >= 0.85 and bright_share < 0.1 and _has_dense_code_rows(strokes, cv2):
            page_mode = 6
    elif dark and portable:
        # Keep antialiased strokes available while enlarging small terminal glyphs.
        image = ImageOps.autocontrast(ImageOps.invert(value), cutoff=(0.02, 0.08))
    elif dark:
        # A broad bright panel can contain its own dark code panel. Keep
        # that mixed-polarity content intact instead of erasing dim syntax.
        threshold = _foreground_threshold(histogram)
        image = value.point([255 if level <= threshold else 0
                             for level in range(256)])
    elif portable and cv2 is not None:
        image, panels = _enhance_dark_panels(image, value, cv2, use_local_contrast=use_local_contrast)
        dark = dark or panels
        enhanced = panels and use_local_contrast
    # A darker mask isolates a code pane from surrounding UI. Vision reads
    # that pane at higher resolution while keeping the full image in color.
    upscale = (
        portable or dark
        or next(_dark_panel_slices(value, VISION_DARK_PANEL_LIMIT), None) is not None
    )
    factor = (min(2.0, 2560 / width, math.sqrt(6_000_000 / total))
              if upscale else 1)
    if portable and enhanced:
        factor = min(3.2, 4096 / width, math.sqrt(16_000_000 / total))
    if scale is not None:
        factor = min(scale, 4096 / width, math.sqrt(16_000_000 / total))
    if factor > 1:
        image = image.resize((round(width * factor), round(height * factor)),
                             Image.Resampling.LANCZOS)
    if not dark and factor <= 1:
        yield _PreparedOCR(path, page_mode)
        return
    with tempfile.TemporaryDirectory(prefix="clipmind-ocr-") as folder:
        prepared = Path(folder) / "recognition.png"
        image.save(prepared)
        yield _PreparedOCR(prepared, page_mode)


def _recover_missing_lines(primary: list[OCRBlock], secondary: list[OCRBlock],
                           prepared: Path) -> list[OCRBlock]:
    """Add second-pass blocks only where the first pass found nothing but ink exists."""
    import numpy as np
    from PIL import Image

    with Image.open(prepared) as image:
        ink = np.asarray(image.convert("L")) < 128
    height, width = ink.shape
    covered = np.zeros_like(ink)

    def window(box):
        x, y, w, h = box
        return (slice(max(0, int(y * height)), min(height, int((y + h) * height) + 1)),
                slice(max(0, int(x * width)), min(width, int((x + w) * width) + 1)))

    for block in primary:
        if block.bbox is not None:
            covered[window(block.bbox)] = True
    recovered = list(primary)
    for block in secondary:
        if block.bbox is None:
            continue
        area = window(block.bbox)
        if covered[area].size and covered[area].mean() <= RECOVER_MAX_COVERED and ink[area].mean() >= RECOVER_MIN_INK:
            recovered.append(block)
            covered[area] = True
    return recovered


_COMMENT_MARKERS = ("//", "#", "/*", "--", ";", "<!--")


def _trailing_comments(left: list[OCRBlock], right: list[OCRBlock]) -> bool:
    """A right-hand column of comments that annotate the code lines beside them."""
    def same_row(first: OCRBlock, second: OCRBlock) -> bool:
        overlap = (min(first.bbox[1] + first.bbox[3], second.bbox[1] + second.bbox[3])
                   - max(first.bbox[1], second.bbox[1]))
        return overlap >= min(first.bbox[3], second.bbox[3]) / 2

    comments = [block for block in right if block.text.lstrip().startswith(_COMMENT_MARKERS)]
    if len(right) < 2 or len(comments) < 0.6 * len(right):
        return False
    return sum(any(same_row(block, other) for other in left) for block in right) >= 0.6 * len(right)


def _dark_panel_slices(value, limit: int):
    """Find large, densely filled dark rectangles on a mixed screen."""
    import numpy as np

    try:
        import cv2
    except ImportError:
        return
    levels = np.asarray(value)
    height, width = levels.shape
    dark = (levels < limit).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(dark)
    for left, top, panel_width, panel_height, area in stats[1:count]:
        if (panel_width * panel_height < DARK_PANEL_MIN_SHARE * width * height
                or area < 0.6 * panel_width * panel_height
                or panel_width * panel_height > 0.9 * width * height):
            continue
        yield np.s_[top:top + panel_height, left:left + panel_width]


def _enhance_dark_panels(image, value, cv2, *, use_local_contrast: bool):
    """Bring out dim syntax colors in mixed screens for portable OCR."""
    import numpy as np
    from PIL import Image

    levels = np.asarray(value)
    rgb = np.asarray(image) if use_local_contrast else None
    result = None
    for bounds in _dark_panel_slices(levels, 96):
        if result is None:
            result = np.asarray(image.convert("L")).copy()
        if rgb is not None:
            result[bounds], _ = _dark_code_image(rgb[bounds], cv2)
        else:
            region = levels[bounds]
            threshold = _foreground_threshold(np.bincount(region.ravel(), minlength=256).tolist())
            result[bounds] = np.where(region <= threshold, 255, 0)
    return (Image.fromarray(result), True) if result is not None else (image, False)


def _reading_order(blocks: list[OCRBlock]) -> tuple[OCRBlock, ...]:
    """Read separated columns before the next column, with headers above them."""
    positioned = [block for block in blocks if block.bbox is not None]
    unpositioned = [block for block in blocks if block.bbox is None]

    def ordered(items: list[OCRBlock]) -> list[OCRBlock]:
        if len(items) < 2:
            return items
        for axis, minimum_gap in ((0, 0.025), (1, 0.02)):
            intervals = sorted((block.bbox[axis], block.bbox[axis] +
                                block.bbox[axis + 2]) for block in items)
            end = intervals[0][1]
            gaps = []
            for start, stop in intervals[1:]:
                if start - end >= minimum_gap:
                    gaps.append((start - end, (start + end) / 2))
                end = max(end, stop)
            if gaps:
                _, split = max(gaps)
                before = [block for block in items if block.bbox[axis] < split]
                after = [block for block in items if block.bbox[axis] >= split]
                if axis == 0 and _trailing_comments(before, after):
                    continue
                return ordered(before) + ordered(after)
        rows: list[list[OCRBlock]] = []
        for block in sorted(items, key=lambda block: block.bbox[1]):
            if rows:
                first = rows[-1][0]
                overlap = min(first.bbox[1] + first.bbox[3], block.bbox[1] + block.bbox[3])
                overlap -= max(first.bbox[1], block.bbox[1])
                if overlap >= min(first.bbox[3], block.bbox[3]) / 2:
                    rows[-1].append(block)
                    continue
            rows.append([block])
        return [block for row in rows for block in sorted(row, key=lambda block: block.bbox[0])]

    return tuple(ordered(positioned) + unpositioned)


def recognize(
    path: Path,
    min_confidence: float = 0.4,
    *,
    languages: tuple[str, ...] | None = None,
) -> OCRResult:
    """Recognized text and normalized layout, ordered top-to-bottom.

    Raises OCRError so callers can distinguish "no text in this frame" from
    "OCR is broken"; the caller decides whether that is fatal (it is not).
    """
    if _IMPORT_ERROR is not None:
        raise OCRError(f"Vision unavailable ({_IMPORT_ERROR})")

    url = NSURL.fileURLWithPath_(str(path))
    with _vision_lock, objc.autorelease_pool():
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        if handler is None:
            raise OCRError(f"could not open image: {path}")

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(list(languages or settings.ocr_languages))
        request.setRecognitionLevel_(0)  # accurate
        request.setUsesLanguageCorrection_(True)

        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise OCRError(str(err) if err else "Vision request failed")

        observations = list(request.results() or [])
        # Vision uses a bottom-left origin, so descending y is visual top-down.
        observations.sort(key=lambda o: -o.boundingBox().origin.y)

        blocks: list[OCRBlock] = []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            if float(candidate.confidence()) < min_confidence:
                continue
            text = (candidate.string() or "").strip()
            if text:
                box = obs.boundingBox()
                blocks.append(
                    OCRBlock(
                        text=text,
                        confidence=float(candidate.confidence()),
                        bbox=(
                            float(box.origin.x),
                            1.0 - float(box.origin.y) - float(box.size.height),
                            float(box.size.width),
                            float(box.size.height),
                        ),
                    )
                )
        return OCRResult(tuple(blocks))


def read_text(
    path: Path,
    min_confidence: float = 0.4,
    *,
    languages: tuple[str, ...] | None = None,
) -> list[str]:
    """Compatibility view containing only recognized lines."""
    return recognize(
        path,
        min_confidence=min_confidence,
        languages=languages,
    ).lines


def _vision_worker_command() -> list[str]:
    return [sys.executable, "-u", "-P", "-m", "clipmind.ocr_worker"]


def _read_vision_replies(stream, replies) -> None:
    try:
        for line in stream:
            try:
                message = ("reply", json.loads(line))
            except ValueError:
                message = ("error", "Vision worker returned invalid output")
            try:
                replies.put_nowait(message)
            except queue.Full:
                return
            if message[0] != "reply":
                return
    finally:
        try:
            replies.put_nowait(("error", "Vision worker exited before returning a result"))
        except queue.Full:
            pass


def _read_vision_errors(stream, messages) -> None:
    for chunk in iter(lambda: stream.read(4096), ""):
        messages.append(chunk)


def _stop_vision_worker() -> None:
    """Reap only this module's interpreter and its bounded pipe readers."""
    global _worker_process, _worker_connection, _worker_idle_timer, _worker_requests
    global _worker_reader, _worker_error_reader, _worker_replies, _worker_stderr_tail
    if _worker_idle_timer is not None:
        _worker_idle_timer.cancel()
        _worker_idle_timer = None
    process, _worker_process = _worker_process, None
    if process is not None:
        def stop_group(*, force=False):
            try:
                if os.name == "posix":
                    # Descendants can retain these pipes after the interpreter
                    # exits, so poll() is not a reason to skip its owned group.
                    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                elif process.poll() is None:
                    process.kill() if force else process.terminate()
            except ProcessLookupError:
                pass
            except PermissionError:
                if process.poll() is None:
                    process.kill() if force else process.terminate()

        stop_group()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            stop_group(force=True)
            process.wait(timeout=1)
        # An interpreter can exit immediately while a native helper ignores
        # SIGTERM. Close all inherited pipe writers before joining readers.
        stop_group(force=True)
    if _worker_connection is not None:
        try:
            _worker_connection.close()
        except (BrokenPipeError, OSError):
            pass
        _worker_connection = None
    for reader in (_worker_reader, _worker_error_reader):
        if reader is not None:
            reader.join(timeout=1)
    if process is not None:
        process.stdout.close()
        process.stderr.close()
    _worker_stderr_tail = "".join(_worker_stderr)
    _worker_reader = _worker_error_reader = _worker_replies = None
    _worker_requests = 0


def _close_vision_worker() -> None:
    with _worker_lock:
        _stop_vision_worker()


def _expire_vision_worker(process, requests: int) -> None:
    with _worker_lock:
        if _worker_process is process and _worker_requests == requests:
            _stop_vision_worker()


def _recognize_in_worker(image: Path, *, languages: tuple[str, ...]) -> OCRResult:
    global _worker_process, _worker_connection, _worker_requests, _worker_idle_timer
    global _worker_reader, _worker_error_reader, _worker_replies, _worker_module_path
    with _worker_lock:
        started = time.monotonic()
        if _worker_idle_timer is not None:
            _worker_idle_timer.cancel()
            _worker_idle_timer = None
        if (_worker_process is None or _worker_process.poll() is not None or
                _worker_requests >= _VISION_MAX_REQUESTS):
            _stop_vision_worker()
            _worker_stderr.clear()
            # Use this package, including an ablation worktree, rather than
            # whichever checkout an editable environment happens to point at.
            environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            try:
                _worker_process = subprocess.Popen(
                    _vision_worker_command(), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace", env=environment,
                    cwd=Path(__file__).resolve().parents[1],
                    start_new_session=os.name == "posix",
                )
            except OSError as exc:
                raise OCRError(f"Vision worker could not start: {exc}") from exc
            _worker_connection = _worker_process.stdin
            _worker_replies = queue.Queue(maxsize=1)
            _worker_reader = threading.Thread(target=_read_vision_replies,
                args=(_worker_process.stdout, _worker_replies), daemon=True)
            _worker_error_reader = threading.Thread(target=_read_vision_errors,
                args=(_worker_process.stderr, _worker_stderr), daemon=True)
            _worker_reader.start()
            _worker_error_reader.start()
        try:
            _worker_connection.write(json.dumps({"image": str(image.resolve()),
                                                 "languages": languages}) + "\n")
            _worker_connection.flush()
            kind, value = _worker_replies.get(timeout=max(0, _VISION_REQUEST_TIMEOUT -
                                                            (time.monotonic() - started)))
            if kind != "reply":
                raise OCRError(value)
            if value.get("status") != "ok":
                raise OCRError(value.get("error", "Vision worker returned no result"))
            result = OCRResult(tuple(OCRBlock(block["text"], block.get("confidence"),
                tuple(block["bbox"]) if block.get("bbox") is not None else None)
                for block in value["blocks"]))
            _worker_module_path = value.get("ocr_module")
        except (queue.Empty, BrokenPipeError, OSError, OCRError, KeyError, TypeError) as exc:
            _stop_vision_worker()
            if isinstance(exc, queue.Empty):
                error = OCRError("Vision recognition exceeded its 10-second deadline")
            elif isinstance(exc, OCRError):
                error = exc
            else:
                error = OCRError("Vision worker exited before returning a valid result")
            if _worker_stderr_tail:
                error.add_note(_worker_stderr_tail)
            raise error from exc
        _worker_requests += 1
        process = _worker_process
        _worker_idle_timer = threading.Timer(_VISION_IDLE_SECONDS, _expire_vision_worker,
                                             args=(process, _worker_requests))
        _worker_idle_timer.daemon = True
        _worker_idle_timer.start()
        return result


atexit.register(_close_vision_worker)


@dataclass(frozen=True)
class VisionTextRecognizer:
    config: Settings
    name: str = "apple-vision"

    def available(self) -> bool:
        return available()

    def read_text(self, image: Path) -> list[str]:
        return self.recognize(image).lines

    def recognize(self, image: Path) -> OCRResult:
        with _recognition_image(image) as preparation:
            prepared = preparation.path
            languages = self.config.ocr_languages
            try:
                result = _recognize_in_worker(prepared, languages=languages)
            except OCRError:
                if prepared == image:
                    raise
                result = _recognize_in_worker(image, languages=languages)
                return OCRResult(_reading_order(list(result.blocks)))
            blocks = list(result.blocks)
            if prepared != image:
                # A second pass can recover lines, but cannot invalidate the
                # recognition that already succeeded.
                try:
                    original = _recognize_in_worker(image, languages=languages)
                except OCRError:
                    pass
                else:
                    blocks = _recover_missing_lines(blocks, list(original.blocks), prepared)
        return OCRResult(_reading_order(blocks))


def tesseract_available() -> bool:
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa: F401
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class TesseractTextRecognizer:
    """Portable OCR fallback for Linux, Windows and non-Vision macOS hosts."""

    config: Settings
    name: str = "tesseract"

    def available(self) -> bool:
        return tesseract_available()

    def read_text(self, image: Path) -> list[str]:
        return self.recognize(image).lines

    def recognize(self, image: Path) -> OCRResult:
        if not self.available():
            raise OCRError(
                "Tesseract unavailable; install tesseract and the portable OCR extra"
            )
        return self._recognize_once(image, None)

    def _recognize_once(self, image: Path, scale: float | None) -> OCRResult:
        import pytesseract
        from PIL import Image

        with _recognition_image(image, portable=True, scale=scale) as prepared, Image.open(prepared.path) as source:
            width, height = source.size
            data = pytesseract.image_to_data(
                source,
                lang=self.config.tesseract_languages,
                output_type=pytesseract.Output.DICT,
                config=f"--psm {prepared.page_mode}",
                timeout=30,
            )
        grouped: dict[tuple[int, int, int], list[int]] = {}
        for index, text in enumerate(data.get("text", [])):
            if not str(text).strip():
                continue
            key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )
            grouped.setdefault(key, []).append(index)

        blocks: list[OCRBlock] = []
        lines = []
        for indices in grouped.values():
            # A page-segmentation engine may join distant columns into one
            # line. Preserve each column's spatial box and reading order.
            current = []
            for index in sorted(indices, key=lambda i: int(data["left"][i])):
                if current:
                    previous = current[-1]
                    gap = (int(data["left"][index]) - int(data["left"][previous]) -
                           int(data["width"][previous]))
                    if gap > 3 * max(int(data["height"][previous]), int(data["height"][index])):
                        lines.append(current)
                        current = []
                current.append(index)
            lines.append(current)
        for indices in lines:
            words = [str(data["text"][index]).strip() for index in indices]
            left = min(int(data["left"][index]) for index in indices)
            top = min(int(data["top"][index]) for index in indices)
            right = max(
                int(data["left"][index]) + int(data["width"][index])
                for index in indices
            )
            bottom = max(
                int(data["top"][index]) + int(data["height"][index])
                for index in indices
            )
            confidences = [
                float(data["conf"][index]) / 100.0
                for index in indices
                if float(data["conf"][index]) >= 0
            ]
            blocks.append(
                OCRBlock(
                    text=" ".join(words),
                    confidence=(sum(confidences) / len(confidences)) if confidences else None,
                    bbox=(left / width, top / height, (right - left) / width, (bottom - top) / height),
                )
            )
        return OCRResult(_reading_order(blocks))


def available() -> bool:
    """True only if OCR actually runs - the import succeeding is not enough."""
    global _probe_result
    with _probe_lock:
        if _probe_result is None:
            if _IMPORT_ERROR is not None:
                _probe_result = (False, _IMPORT_ERROR)
            else:
                _probe_result = _smoke_test()
    return _probe_result[0]


def unavailable_reason() -> str | None:
    available()
    return _probe_result[1] if _probe_result else None


def _smoke_test() -> tuple[bool, str | None]:
    import tempfile

    from PIL import Image

    image: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            image = Path(handle.name)
        Image.new("RGB", (64, 32), "white").save(image)
        _recognize_in_worker(image, languages=settings.ocr_languages)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if image is not None:
            image.unlink(missing_ok=True)
