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
    with objc.autorelease_pool():
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
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


@dataclass(frozen=True)
class VisionTextRecognizer:
    config: Settings
    name: str = "apple-vision"

    def available(self) -> bool:
        return available()

    def read_text(self, image: Path) -> list[str]:
        return read_text(image, languages=self.config.ocr_languages)

    def recognize(self, image: Path) -> OCRResult:
        return recognize(image, languages=self.config.ocr_languages)


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

        with Image.open(image) as source:
            width, height = source.size
            data = pytesseract.image_to_data(
                source,
                lang=self.config.tesseract_languages,
                output_type=pytesseract.Output.DICT,
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
        for indices in grouped.values():
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
        return OCRResult(tuple(blocks))


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
