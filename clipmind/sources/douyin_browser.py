"""Acquire a Douyin media address through a real browser, because Douyin needs one.

Douyin's detail endpoint is gated by a signature its own JavaScript security SDK
builds inside the page. A plain HTTP client is refused with
``Blocked by ArgusSecurityPlugin Uifid Not Found`` no matter which cookies it
carries, which is why yt-dlp cannot reach it and why its extractor leaves that
signature as a TODO.

The media is not gated. Once the page has named the address, any client can
fetch it -- measured as HTTP 206 with no cookies, no Referer and no user agent.
So the browser is used for the address only, and closed as soon as it has one.

No account is involved: a signed-out session works once it has been warmed up by
actually running the site's JavaScript. Headless is refused -- a warm-up that
yields 32 cookies signed out yields 4 headless, and the request then fails -- so
the window is moved off screen rather than hidden. That difference is measured,
not assumed.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .douyin import numeric_source_id

logger = logging.getLogger(__name__)

WARMUP_URL = "https://www.douyin.com/"
DETAIL_MARKER = "aweme/detail"
# Hidden windows are detected; an off-screen one is not. Same visibility to the
# user, and the only variant that survived the check.
OFFSCREEN_ARGS = ("--window-position=-3000,-3000", "--window-size=1024,768")
WARMUP_MS = 7000
POLL_MS = 500


class BrowserUnavailable(RuntimeError):
    """Playwright or a usable Chrome is missing, so this rung cannot run."""


class ResolveFailed(RuntimeError):
    """The browser ran, but Douyin named no address we can use."""


@dataclass(frozen=True)
class ResolvedMedia:
    """What the page told us, before anything has been downloaded."""

    urls: tuple[str, ...]
    info: dict = field(default_factory=dict)
    size_bytes: int = 0


def _media_info(payload: dict, page_url: str, expected_id: str) -> ResolvedMedia:
    detail = (payload or {}).get("aweme_detail")
    if not isinstance(detail, dict):
        raise ResolveFailed("the page returned no video detail")
    found_id = str(detail.get("aweme_id") or "")
    if found_id != expected_id:
        # Handing this back would build an Evidence Pack for a video nobody
        # asked for. Returning nothing is the lesser failure by a wide margin.
        raise ResolveFailed(
            f"the page described video {found_id or 'unknown'}, not {expected_id}"
        )
    video = detail.get("video") or {}
    play_addr = video.get("play_addr") or {}
    urls = [str(u) for u in (play_addr.get("url_list") or []) if str(u).startswith("http")]
    if not urls:
        raise ResolveFailed("the video detail carried no playable address")
    # Milliseconds upstream; MediaAsset.duration is seconds.
    duration_ms = video.get("duration") or 0
    author = detail.get("author") or {}
    info = {
        "id": str(detail.get("aweme_id") or ""),
        "title": (detail.get("desc") or "").strip(),
        "duration": round(float(duration_ms) / 1000.0, 3) if duration_ms else 0.0,
        "uploader": author.get("nickname") or None,
        "webpage_url": page_url,
        "_clipmind_strategy": "browser session",
    }
    return ResolvedMedia(
        urls=tuple(urls),
        info={k: v for k, v in info.items() if v not in (None, "")},
        size_bytes=int(play_addr.get("data_size") or 0),
    )


async def resolve(page_url: str, *, timeout: float = 90.0) -> ResolvedMedia:
    """Open the page just long enough for it to name its own media address."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise BrowserUnavailable("playwright is not installed") from exc

    captured: dict[str, dict] = {}
    last_status: dict = {}
    # When the request already names a video, that is the answer. Deriving the
    # target from wherever the browser lands would let a redirect substitute a
    # different video for the one that was asked for.
    expected: str | None = numeric_source_id(page_url)
    deadline = time.monotonic() + max(float(timeout), 10.0)

    async with async_playwright() as engine:
        try:
            browser = await engine.chromium.launch(
                channel="chrome", headless=False, args=list(OFFSCREEN_ARGS)
            )
        except Exception as exc:  # noqa: BLE001 - any launch failure is "no browser"
            raise BrowserUnavailable(str(exc).splitlines()[0][:160]) from exc

        try:
            context = await browser.new_context(locale="zh-CN")
            page = await context.new_page()

            async def on_response(response) -> None:
                # File every detail response under the video it describes. The
                # warm-up feed requests its own recommendations, so "the first
                # response that arrives" is not the one that was asked for.
                if DETAIL_MARKER not in response.url:
                    return
                try:
                    payload = await response.json()
                except Exception:  # noqa: BLE001 - a challenge page is not JSON
                    last_status["status"] = response.status
                    return
                detail = (payload or {}).get("aweme_detail")
                if isinstance(detail, dict):
                    found = str(detail.get("aweme_id") or "")
                    if found:
                        captured[found] = payload

            page.on("response", on_response)
            # The warm-up is the point: the cookies the endpoint checks are
            # written by the site's own scripts, not by Set-Cookie.
            await page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(WARMUP_MS)
            await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            while time.monotonic() < deadline:
                # Only a share link, which names no video, may take its identity
                # from where it landed. A request that already named one keeps
                # it: a redirect is not permission to return a different video.
                if expected is None:
                    expected = numeric_source_id(page.url)
                if expected and expected in captured:
                    break
                await page.wait_for_timeout(POLL_MS)
        finally:
            await browser.close()

    if not expected:
        raise ResolveFailed("the page never resolved to a video id")
    if expected not in captured:
        status = last_status.get("status")
        raise ResolveFailed(
            f"the page returned no detail for {expected} (last status {status})"
            if status else f"the page returned no detail for {expected}"
        )
    return _media_info(captured[expected], page_url, expected)


class _Stopped(Exception):
    """Internal: the transfer was asked to stop and did."""


def _fetch_to(url: str, destination: Path, chunk: int, stop: threading.Event) -> int:
    written = 0
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=60) as response:
        with destination.open("wb") as handle:
            while True:
                if stop.is_set():
                    raise _Stopped()
                block = response.read(chunk)
                if not block:
                    break
                handle.write(block)
                written += len(block)
    return written


async def download(urls, destination: Path, *, chunk: int = 1 << 20) -> Path:
    """Fetch the named address with a plain client; the CDN asks for nothing.

    Cancellation must stop the transfer, not merely stop waiting for it. A
    thread cannot be killed, so it is asked to stop and then waited for: a
    transfer still writing after cancellation would race the very cleanup that
    cancellation exists to trigger.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for url in urls:
        stop = threading.Event()
        worker = asyncio.create_task(
            asyncio.to_thread(_fetch_to, url, destination, chunk, stop)
        )
        try:
            written = await asyncio.shield(worker)
        except asyncio.CancelledError:
            stop.set()
            # Being cancelled again must not cut this wait short. Returning
            # while the thread is still writing reopens the race with the
            # cleanup that cancellation exists to trigger, so the wait is
            # shielded and repeated until the transfer has actually exited.
            while not worker.done():
                with contextlib.suppress(BaseException):
                    await asyncio.shield(worker)
            raise
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            last = exc
            logger.warning("Douyin media address failed: %s", str(exc)[:120])
            continue
        if written > 0:
            return destination
        last = ResolveFailed("the address returned an empty body")
    raise ResolveFailed(f"no named address could be downloaded ({last})")
