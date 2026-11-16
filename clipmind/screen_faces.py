"""Local face boxes used only to distinguish speaker motion from screen changes."""
from functools import lru_cache
from pathlib import Path


def boxes(path: Path) -> tuple[tuple[float, float, float, float], ...]:
    try:
        stat = path.stat()
        return _boxes_at(path.resolve(), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ()


@lru_cache(maxsize=64)
def _boxes_at(path: Path, modified_ns: int, byte_count: int) -> tuple[tuple[float, float, float, float], ...]:
    try:
        import cv2
        from PIL import Image
        import numpy as np

        with Image.open(path) as source:
            image = source.convert('L')
            image.thumbnail((960, 960))
            width, height = image.size
            pixels = np.asarray(image)
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        found = detector.detectMultiScale(pixels, scaleFactor=1.1, minNeighbors=5, minSize=(18, 18))
        return tuple((x/width, y/height, w/width, h/height) for x, y, w, h in found)
    except Exception:  # Optional detection never turns an unclear frame into a negative.
        return ()
