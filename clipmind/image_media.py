"""Ordered still-image evidence, without manufacturing a video or speech."""
from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import threading
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from PIL import Image

from . import media
from .media import Frame, MediaError, dhash


_EXTENSIONS = {
    "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp",
    "TIFF": ".tiff", "GIF": ".gif", "AVIF": ".avif",
}

TIFF_MAX_PAGES = 1000
TIFF_MAX_PAGE_PIXELS = 40_000_000
TIFF_MAX_TOTAL_PIXELS = 160_000_000
TIFF_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
TIFF_PAGE_TRANSFORM = "tiff-to-png-lossless"


class TiffPageError(MediaError):
    code = "image_decode_failed"
    user_message = "TIFF 无法逐页完整解码，未生成不完整证据包。"
    action = "请选择可完整打开每一页的 TIFF，或提供原始图片页；原文件未修改。"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.user_message)


class TiffPageLimitExceeded(TiffPageError):
    code = "image_collection_limit_exceeded"
    user_message = "TIFF 超过页数、像素或输出大小限制，未生成不完整证据包。"
    action = "请提供较小的图片集合；不会静默截断 TIFF 的后续页面。"


class _TiffStopped(Exception):
    pass


class _BoundedPngWriter:
    """Enforce the byte budget while PNG is being written, not afterwards."""

    def __init__(self, handle, limit: int, stop: threading.Event) -> None:
        self.handle, self.limit, self.stop = handle, limit, stop
        self.written = 0

    def write(self, data):
        if self.stop.is_set():
            raise _TiffStopped()
        if len(data) > self.limit - self.written:
            raise TiffPageLimitExceeded()
        count = self.handle.write(data)
        self.written += count
        return count

    def flush(self):
        return self.handle.flush()


def _decode_tiff_collection(source: Path, directory: Path, stop: threading.Event, *,
                            max_pages: int, max_page_pixels: int,
                            max_total_pixels: int, max_output_bytes: int) -> tuple[Path, ...]:
    def check_stop():
        if stop.is_set():
            raise _TiffStopped()

    def page_pixels(image):
        pixels = image.width * image.height
        if image.width <= 0 or image.height <= 0 or pixels > max_page_pixels:
            raise TiffPageLimitExceeded()
        return pixels

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if image.format != "TIFF":
                    raise TiffPageError("The page decoder requires a TIFF image.")
                check_stop()
                if page_pixels(image) > max_total_pixels:
                    raise TiffPageLimitExceeded()
                image.load()
                if not image.tag_v2.next:
                    # A one-page TIFF retains its exact source bytes and format.
                    return (source,)
                # A declared next IFD must actually decode. EOF from a corrupt
                # link is not evidence that the document had fewer pages.
                image.seek(1)
                image.seek(0)
                paths = []
                total_pixels = total_bytes = 0
                while True:
                    check_stop()
                    if len(paths) >= max_pages:
                        raise TiffPageLimitExceeded()
                    total_pixels += page_pixels(image)
                    if total_pixels > max_total_pixels:
                        raise TiffPageLimitExceeded()
                    # These PNG modes retain every decoded pixel value. Do not
                    # silently clamp 32-bit/float data or convert CMYK to RGB.
                    if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}:
                        raise TiffPageError("This TIFF colour/bit-depth mode cannot be preserved losslessly as PNG.")
                    image.load()
                    check_stop()
                    destination = directory / f"page-{len(paths) + 1:05d}.png"
                    with destination.open("xb") as handle:
                        output = _BoundedPngWriter(handle, max_output_bytes - total_bytes, stop)
                        image.save(output, format="PNG")
                        total_bytes += output.written
                    paths.append(destination)
                    check_stop()
                    if not image.tag_v2.next:
                        return tuple(paths)
                    image.seek(len(paths))
    except (TiffPageError, _TiffStopped):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise TiffPageLimitExceeded() from exc
    except (OSError, ValueError, SyntaxError, EOFError, TypeError, OverflowError) as exc:
        raise TiffPageError() from exc


async def expand_tiff_pages(source: Path, root: Path, *,
                            max_pages: int = TIFF_MAX_PAGES,
                            max_page_pixels: int = TIFF_MAX_PAGE_PIXELS,
                            max_total_pixels: int = TIFF_MAX_TOTAL_PIXELS,
                            max_output_bytes: int = TIFF_MAX_OUTPUT_BYTES) -> tuple[Path, ...]:
    """Keep one-page TIFF bytes; fully decode multipage TIFF into lossless PNGs.

    Source identity remains the original container's hash. Every converted page
    is an ordinal image, never a fabricated video second. The caller owns root;
    a failure/cancellation removes our entire attempt after the writer exits.
    """
    if any(type(value) is not int or value <= 0 for value in (
        max_pages, max_page_pixels, max_total_pixels, max_output_bytes,
    )):
        raise TiffPageLimitExceeded()
    if root.is_symlink():
        raise TiffPageError("The image-page workspace must not be a symbolic link.")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="image-pages-", dir=root))
    stop = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        _decode_tiff_collection, source, directory, stop,
        max_pages=min(max_pages, TIFF_MAX_PAGES),
        max_page_pixels=min(max_page_pixels, TIFF_MAX_PAGE_PIXELS),
        max_total_pixels=min(max_total_pixels, TIFF_MAX_TOTAL_PIXELS),
        max_output_bytes=min(max_output_bytes, TIFF_MAX_OUTPUT_BYTES),
    ))
    try:
        paths = await asyncio.shield(worker)
        if paths == (source,):
            shutil.rmtree(directory)
        return paths
    except asyncio.CancelledError:
        stop.set()
        while not worker.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(worker)
        with contextlib.suppress(BaseException):
            worker.result()
        shutil.rmtree(directory)
        raise
    except BaseException:
        shutil.rmtree(directory)
        raise


class ImageMotionError(MediaError):
    code = "image_motion_decode_failed"
    action = "请检查本机 FFmpeg，或提供可正常播放的原图和动态素材；未将缺页或静态封面冒充完整动态内容。"

    def __init__(self, page: int) -> None:
        self.user_message = f"第 {page + 1} 页动态素材无法完整解码，未生成不完整证据包。"
        super().__init__(self.user_message)


def candidates(paths: Iterable[Path]) -> list[Frame]:
    """Keep every explicit source image in source order, even near duplicates.

    Collections are not sampled video: a one-character edit on an otherwise
    identical page must not disappear in the pre-OCR dHash filter.
    """
    frames = []
    for index, raw in enumerate(paths):
        path = Path(raw)
        try:
            with Image.open(path) as image:
                if image.format not in _EXTENSIONS:
                    raise MediaError("This image format is not supported locally.")
                if getattr(image, "n_frames", 1) != 1:
                    raise MediaError("Animated images must use the video sampling path.")
                image.verify()
            # verify() only validates headers in several Pillow plugins (JPEG
            # included). Decode pixels before accepting a page as evidence;
            # hashing failures remain recoverable only for readable originals.
            with Image.open(path) as image:
                image.load()
        except MediaError:
            raise
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise MediaError(
                f"Source image {index + 1} could not be decoded; no partial pack was published."
            ) from exc
        frame = Frame(index, 0.0, path, source_image_index=index)
        try:
            frame.phash = dhash(path)
        except Exception as exc:  # noqa: BLE001 - a hash cannot discard a page
            frame.dedupe_warning = f"{type(exc).__name__}: perceptual hash failed; image retained"
        frames.append(frame)
    if not frames:
        raise MediaError("The source image collection is empty.")
    return frames


async def sample_collection(
    paths: Iterable[Path],
    motion_paths: Iterable[Path | None],
    dest_dir: Path,
    *,
    fps: float,
    width: int,
) -> list[Frame]:
    """Interleave every original page with its own decoded motion samples.

    The source image index may repeat. Frame.index remains globally unique;
    only source_motion_timestamp has clip-local seconds, while timestamp=0
    continues to mean that no whole-post playback clock was acquired.
    """
    originals = candidates(paths)
    motions = tuple(motion_paths)
    if not motions:
        motions = (None,) * len(originals)
    if len(motions) != len(originals):
        raise MediaError("Image motion pages must match the original image order.")
    frames: list[Frame] = []
    for page, (original, motion) in enumerate(zip(originals, motions)):
        frames.append(replace(original, index=len(frames)))
        if motion is None:
            continue
        try:
            sampled = await media.sample_frames(
                Path(motion), dest_dir / f"page-{page + 1:05d}", fps=fps, width=width,
                strict=True,
            )
        except MediaError as exc:
            raise ImageMotionError(page) from exc
        if not sampled:
            raise ImageMotionError(page)
        for sample in sampled:
            frames.append(replace(
                sample, index=len(frames), timestamp=0.0,
                source_image_index=page, source_motion_timestamp=sample.timestamp,
            ))
    return frames


def retain_all(frames: list[Frame], dest_dir: Path) -> list[Frame]:
    """Copy original bytes into canonical storage; acquisition owns the inputs."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    retained = []
    for frame in frames:
        with Image.open(frame.path) as image:
            extension = _EXTENSIONS[image.format]
        page = frame.source_image_index if frame.source_image_index is not None else frame.index
        name = f"image-{page + 1:05d}"
        if frame.source_motion_timestamp is not None:
            name += f"-motion-{frame.index:05d}"
        dest = dest_dir / f"{name}{extension}"
        shutil.copy2(frame.path, dest)
        retained.append(replace(frame, path=dest))
    return retained
