"""Bounded motion clips belonging to ordered Douyin Live Photo pages.

Only each page's own ``video.play_addr.url_list`` is accepted. The original
still image remains a separate asset. Downloading bytes here does not prove
that they are decodable media: the pipeline must validate every clip with
FFmpeg before publishing a complete Evidence Pack.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import shutil
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .douyin_images import ImagePostBudgetExceeded, ImagePostError, MAX_IMAGES, MAX_TOTAL_BYTES, image_rows

_CDN_SUFFIXES = (
    "douyinvod.com", "douyin.com", "amemv.com", "snssdk.com", "bytecdn.cn",
    "byteimg.com", "bytedance.com", "zjcdn.com",
)


def trusted_motion_url(value: object) -> bool:
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


def motion_url_groups(detail: dict) -> tuple[tuple[str, ...], ...]:
    """Keep static slots and each moving page's own bounded CDN mirrors."""
    groups = []
    for row in image_rows(detail):
        video = row.get("video")
        declared = row.get("live_photo_type") in (1, "1") or row.get("clip_type") in (5, "5")
        if video in (None, "", "$undefined") or video == {}:
            if declared:
                raise ImagePostError("a Live Photo page carried no motion metadata")
            groups.append(())
            continue
        if not isinstance(video, dict):
            raise ImagePostError("a Live Photo page contains malformed motion metadata")
        play_addr = video.get("play_addr")
        urls = play_addr.get("url_list") if isinstance(play_addr, dict) else None
        if not isinstance(urls, list) or not urls or len(urls) > 16:
            raise ImagePostError("a Live Photo page carried no supported motion address")
        if not all(trusted_motion_url(url) for url in urls):
            raise ImagePostError("a Live Photo page returned an untrusted motion address")
        # These are metadata hints, never replacements for the decoded clip's
        # actual duration/size. Reject malformed hints instead of inventing them.
        for value in (video.get("duration"), play_addr.get("data_size")):
            if value is not None:
                try:
                    valid = not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0
                except (TypeError, ValueError, OverflowError):
                    valid = False
                if not valid:
                    raise ImagePostError("a Live Photo page contains invalid motion metadata")
        groups.append(tuple(dict.fromkeys(urls)))
    return tuple(groups)


class _MotionRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted_motion_url(newurl):
            raise ImagePostError("a Live Photo motion redirected outside the supported CDN")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Stopped(Exception):
    pass


def _download_collection(groups, directory: Path, stop: threading.Event, *,
                         max_total_bytes: int, timeout: float) -> tuple[Path | None, ...]:
    opener = urllib.request.build_opener(_MotionRedirects())
    deadline = time.monotonic() + timeout
    spent = 0
    paths: list[Path | None] = []
    for index, mirrors in enumerate(groups, 1):
        if stop.is_set():
            raise _Stopped()
        if not mirrors:
            paths.append(None)
            continue
        destination = directory / f"motion-page-{index:04d}.part"
        completed = None
        for url in mirrors:
            if stop.is_set():
                raise _Stopped()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ImagePostBudgetExceeded("the Live Photo motions exceeded the transfer deadline")
            try:
                request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
                with opener.open(request, timeout=min(remaining, 20.0)) as response:
                    if not trusted_motion_url(response.url):
                        raise ImagePostError("a Live Photo motion response left the supported CDN")
                    if response.status != 200 or response.headers.get("Content-Range") is not None:
                        raise ImagePostError("a Live Photo motion returned a partial response")
                    if response.headers.get("Content-Encoding", "identity").casefold() not in {"", "identity"}:
                        raise ImagePostError("compressed Live Photo motion transport is unsupported")
                    length = response.headers.get("Content-Length")
                    declared = None
                    if length is not None:
                        try:
                            declared = int(length)
                        except ValueError:
                            raise ImagePostError("a Live Photo motion returned an invalid content length") from None
                        if declared < 0:
                            raise ImagePostError("a Live Photo motion returned an invalid content length")
                        if declared > max_total_bytes - spent:
                            raise ImagePostBudgetExceeded("the Live Photo motions exceed the transfer-byte limit")
                    written = 0
                    with destination.open("wb") as handle:
                        while True:
                            if stop.is_set():
                                raise _Stopped()
                            if time.monotonic() >= deadline:
                                raise ImagePostBudgetExceeded("the Live Photo motions exceeded the transfer deadline")
                            # Keep deadline/cancellation checks between available
                            # blocks instead of waiting for an aggregate 64 KiB.
                            read = getattr(response, "read1", response.read)
                            block = read(min(64 * 1024, max_total_bytes - spent + 1))
                            if not block:
                                break
                            spent += len(block)
                            if spent > max_total_bytes:
                                raise ImagePostBudgetExceeded("the Live Photo motions exceed the transfer-byte limit")
                            written += len(block)
                            if declared is not None and written > declared:
                                raise ImagePostError("a Live Photo motion exceeded its declared response length")
                            handle.write(block)
                if not written:
                    raise ImagePostError("a Live Photo motion returned an empty body")
                if declared is not None and written != declared:
                    raise ImagePostError("a Live Photo motion transfer ended before its declared response length")
                completed = destination.with_suffix(".mp4")
                destination.replace(completed)
                break
            except (ImagePostBudgetExceeded, _Stopped):
                raise
            except Exception:
                destination.unlink(missing_ok=True)
                continue
        if completed is None:
            raise ImagePostError(f"Live Photo motion page {index} could not be downloaded")
        paths.append(completed)
    return tuple(paths)


async def download_motions(groups: tuple[tuple[str, ...], ...], root: Path, *,
                           max_total_bytes: int = MAX_TOTAL_BYTES,
                           timeout: float = 120.0) -> tuple[Path | None, ...]:
    """Download all moving slots, or remove this entire failed attempt."""
    if (not isinstance(groups, (tuple, list)) or len(groups) > MAX_IMAGES
            or max_total_bytes <= 0 or not math.isfinite(timeout) or timeout <= 0):
        raise ImagePostBudgetExceeded("the Live Photo motions exceed configured acquisition limits")
    if any(not isinstance(mirrors, (tuple, list)) or len(mirrors) > 16
           or not all(trusted_motion_url(url) for url in mirrors) for mirrors in groups):
        raise ImagePostError("a Live Photo page returned an untrusted motion address")
    if not any(groups):
        return (None,) * len(groups)
    if root.is_symlink():
        raise ImagePostError("the acquisition directory may not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="image-motion-", dir=root))
    stop = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        _download_collection, groups, directory, stop,
        max_total_bytes=max_total_bytes, timeout=timeout,
    ))
    try:
        return await asyncio.shield(worker)
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
