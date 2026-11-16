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


@contextmanager
def _recognition_image(path: Path, *, portable: bool = False):
    """A bounded, temporary OCR derivative; evidence images stay untouched."""
    from PIL import Image, ImageChops

    with Image.open(path) as original:
        image = original.convert("RGB")
    width, height = image.size
    # Value, rather than luminance, preserves bright red/green code syntax.
    red, green, blue = image.split()
    value = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = value.histogram()
    total = width * height
    dark = (sum(histogram[:96]) / total >= 0.7 and
            sum(histogram[192:]) / total < 0.12)
    if dark:
        # A broad bright panel can contain its own dark code panel. Keep
        # that mixed-polarity content intact instead of erasing dim syntax.
        threshold = _foreground_threshold(histogram)
        image = value.point([255 if level <= threshold else 0
                             for level in range(256)])
    factor = (min(2.0, 2560 / width, math.sqrt(6_000_000 / total))
              if portable or dark else 1)
    if factor > 1:
        image = image.resize((round(width * factor), round(height * factor)),
                             Image.Resampling.LANCZOS)
    if not dark and factor <= 1:
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="clipmind-ocr-") as folder:
        prepared = Path(folder) / "recognition.png"
        image.save(prepared)
        yield prepared


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
    if _worker_connection is not None:
        try:
            _worker_connection.close()
        except (BrokenPipeError, OSError):
            pass
        _worker_connection = None
    if process is not None:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
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
        with _recognition_image(image) as prepared:
            result = _recognize_in_worker(prepared, languages=self.config.ocr_languages)
        return OCRResult(_reading_order(list(result.blocks)))


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
        import pytesseract
        from PIL import Image

        with _recognition_image(image, portable=True) as prepared, Image.open(prepared) as source:
            width, height = source.size
            data = pytesseract.image_to_data(
                source,
                lang=self.config.tesseract_languages,
                output_type=pytesseract.Output.DICT,
                config="--psm 11",
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

    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            Image.new("RGB", (64, 32), "white").save(handle.name)
            read_text(Path(handle.name))
            Path(handle.name).unlink(missing_ok=True)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
