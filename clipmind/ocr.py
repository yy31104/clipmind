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
from pathlib import Path

from .config import settings

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


class OCRError(RuntimeError):
    pass


def read_text(path: Path, min_confidence: float = 0.4) -> list[str]:
    """Recognised text lines, ordered top-to-bottom.

    Raises OCRError so callers can distinguish "no text in this frame" from
    "OCR is broken"; the caller decides whether that is fatal (it is not).
    """
    if _IMPORT_ERROR is not None:
        raise OCRError(f"Vision unavailable ({_IMPORT_ERROR})")

    url = NSURL.fileURLWithPath_(str(path))
    with objc.autorelease_pool():
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        if handler is None:
            raise OCRError(f"could not open image: {path}")

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(list(settings.ocr_languages))
        request.setRecognitionLevel_(0)  # accurate
        request.setUsesLanguageCorrection_(True)

        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise OCRError(str(err) if err else "Vision request failed")

        observations = list(request.results() or [])
        # Vision uses a bottom-left origin, so descending y is visual top-down.
        observations.sort(key=lambda o: -o.boundingBox().origin.y)

        lines: list[str] = []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            if float(candidate.confidence()) < min_confidence:
                continue
            text = (candidate.string() or "").strip()
            if text:
                lines.append(text)
        return lines


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
