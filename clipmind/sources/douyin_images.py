"""Ordered image-post assets named by an exact-ID Douyin metadata entry.

This module never searches a page for arbitrary images: avatars, covers and
recommendations are not carousel pages. Callers must first validate the entry's
``aweme_id``. A failed page or exhausted budget rejects the whole collection;
returning only the downloaded prefix would silently omit evidence.
"""
from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import threading
import time
import urllib.request
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from .douyin_browser import ResolveFailed

MAX_IMAGES = 1000
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
_CDN_SUFFIXES = (
    "douyinpic.com", "byteimg.com", "ibyteimg.com", "bytecdn.cn",
    "douyin.com", "amemv.com", "snssdk.com", "pstatp.com", "toutiaoimg.com",
)


class ImagePostError(ResolveFailed):
    """The requested image post cannot be acquired completely and safely."""


class ImagePostBudgetExceeded(ImagePostError):
    """No partial collection may be labelled complete after reaching a limit."""


def trusted_image_url(value: object) -> bool:
    if not isinstance(value, str) or not value or any(ord(c) < 33 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
        return (parsed.scheme == "https" and parsed.username is None
                and parsed.password is None and parsed.port in (None, 443)
                and any(host == suffix or host.endswith("." + suffix)
                        for suffix in _CDN_SUFFIXES))
    except ValueError:
        return False


def image_rows(detail: dict) -> list[dict]:
    """Return only the explicit ordered page list, never covers or avatars."""
    rows = detail.get("images")
    post = detail.get("image_post_info")
    if rows is None and isinstance(post, dict):
        rows = post.get("images")
    if rows is None:
        if str(detail.get("aweme_type") or "") in {"68", "150"}:
            raise ImagePostError("the image post carried no original image pages")
        return []
    if not isinstance(rows, list) or not rows:
        if rows == [] and str(detail.get("aweme_type") or "") not in {"68", "150"}:
            return []
        raise ImagePostError("the image post carried no original image pages")
    if len(rows) > MAX_IMAGES:
        raise ImagePostBudgetExceeded("the image post exceeds the image-count limit")
    if not all(isinstance(row, dict) for row in rows):
        raise ImagePostError("the image post contains malformed image metadata")
    return rows


def image_url_groups(detail: dict) -> tuple[tuple[str, ...], ...]:
    """Return original page order, keeping CDN mirrors grouped per page.

    Desktop metadata uses ``images[].url_list``; the mobile image-post shape
    uses ``image_post_info.images[].display_image.url_list``. Do not walk
    arbitrary nested objects or borrow the video's cover/author/music images.
    Motion, when present, is acquired separately under the same page order.
    """
    result = []
    for row in image_rows(detail):
        urls = row.get("url_list")
        if urls is None:
            original = row.get("display_image")
            if isinstance(original, dict):
                urls = original.get("url_list")
        if not isinstance(urls, list) or not urls or len(urls) > 16:
            raise ImagePostError("an image page carried no supported original address")
        # A malformed address is not permission to drop a page or replace it
        # with a cover image. Keep only this page's explicitly named mirrors.
        if not all(trusted_image_url(url) for url in urls):
            raise ImagePostError("the image post returned an untrusted image address")
        result.append(tuple(dict.fromkeys(urls)))
    return tuple(result)


class _ImageRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted_image_url(newurl):
            raise ImagePostError("an image redirected outside the supported CDN")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Stopped(Exception):
    pass


def _image_extension(path: Path, max_pixels: int) -> str:
    from PIL import Image

    extensions = {
        "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif",
        "BMP": ".bmp", "TIFF": ".tiff", "AVIF": ".avif",
    }
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.width <= 0 or image.height <= 0 or image.width * image.height > max_pixels:
                raise ImagePostBudgetExceeded("an image exceeds the decoded-pixel limit")
            if getattr(image, "n_frames", 1) != 1:
                raise ImagePostError("an image page contains unsupported animated media")
            extension = extensions.get(image.format or "")
            if extension is None:
                raise ImagePostError("an image page uses an unsupported image format")
            image.load()
    return extension


def _download_collection(groups, directory: Path, stop: threading.Event, *,
                         max_total_bytes: int, max_image_pixels: int,
                         timeout: float) -> tuple[Path, ...]:
    opener = urllib.request.build_opener(_ImageRedirects())
    deadline = time.monotonic() + timeout
    spent = 0
    paths = []
    for index, mirrors in enumerate(groups, 1):
        destination = directory / f"page-{index:04d}.part"
        completed = None
        for url in mirrors:
            if stop.is_set():
                raise _Stopped()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ImagePostBudgetExceeded("the image collection exceeded the transfer deadline")
            try:
                request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
                with opener.open(request, timeout=min(remaining, 20.0)) as response:
                    if not trusted_image_url(response.url):
                        raise ImagePostError("an image response left the supported CDN")
                    if response.status != 200 or response.headers.get("Content-Range") is not None:
                        raise ImagePostError("an image returned a partial response")
                    encoding = response.headers.get("Content-Encoding", "identity").casefold()
                    if encoding not in {"", "identity"}:
                        raise ImagePostError("compressed image transport is unsupported")
                    length = response.headers.get("Content-Length")
                    declared = None
                    if length is not None:
                        try:
                            declared = int(length)
                        except ValueError:
                            raise ImagePostError("an image returned an invalid content length") from None
                        if declared < 0:
                            raise ImagePostError("an image returned an invalid content length")
                        if declared > max_total_bytes - spent:
                            raise ImagePostBudgetExceeded("the image collection exceeds the transfer-byte limit")
                    received = 0
                    with destination.open("wb") as handle:
                        while True:
                            if stop.is_set():
                                raise _Stopped()
                            if time.monotonic() >= deadline:
                                raise ImagePostBudgetExceeded("the image collection exceeded the transfer deadline")
                            # read(n) may aggregate a slow stream indefinitely;
                            # read1 returns after an available block so deadline
                            # and cancellation checks run between socket reads.
                            read = getattr(response, "read1", response.read)
                            block = read(min(64 * 1024, max_total_bytes - spent + 1))
                            if not block:
                                break
                            spent += len(block)
                            received += len(block)
                            if spent > max_total_bytes:
                                raise ImagePostBudgetExceeded("the image collection exceeds the transfer-byte limit")
                            if declared is not None and received > declared:
                                raise ImagePostError("an image exceeded its declared response length")
                            handle.write(block)
                    if declared is not None and received != declared:
                        raise ImagePostError("an image transfer ended before its declared response length")
                extension = _image_extension(destination, max_image_pixels)
                completed = destination.with_suffix(extension)
                destination.replace(completed)
                break
            except (ImagePostBudgetExceeded, _Stopped):
                raise
            except Exception:  # A failing mirror is not another carousel page.
                destination.unlink(missing_ok=True)
                continue
        if completed is None:
            # Never include raw URL, signed query or response content in errors.
            raise ImagePostError(f"image page {index} could not be downloaded or decoded")
        paths.append(completed)
    return tuple(paths)


async def download_images(groups: tuple[tuple[str, ...], ...], root: Path, *,
                          max_images: int = MAX_IMAGES,
                          max_total_bytes: int = MAX_TOTAL_BYTES,
                          max_image_pixels: int = MAX_IMAGE_PIXELS,
                          timeout: float = 120.0) -> tuple[Path, ...]:
    """Acquire all pages inside the caller's acquisition-owned directory.

    Every failed/cancelled attempt removes its own temporary directory after
    its worker has stopped. A successful directory is left for the normal
    acquisition ledger cleanup after OCR/evidence generation.
    """
    if (not groups or len(groups) > min(max_images, MAX_IMAGES)
            or max_total_bytes <= 0 or max_image_pixels <= 0 or timeout <= 0):
        raise ImagePostBudgetExceeded("the image collection exceeds its configured acquisition limits")
    if any(not isinstance(mirrors, (tuple, list)) or not mirrors or len(mirrors) > 16
           or not all(trusted_image_url(url) for url in mirrors)
           for mirrors in groups):
        raise ImagePostError("the image post returned an untrusted image address")
    if root.is_symlink():
        raise ImagePostError("the acquisition directory may not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="image-post-", dir=root))
    stop = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        _download_collection, groups, directory, stop,
        max_total_bytes=max_total_bytes, max_image_pixels=max_image_pixels,
        timeout=timeout,
    ))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        while not worker.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(worker)
        # Retrieve an exception raised by the worker while stopping.
        with contextlib.suppress(BaseException):
            worker.result()
        shutil.rmtree(directory)
        raise
    except BaseException:
        shutil.rmtree(directory)
        raise
