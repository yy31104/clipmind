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
import math
import re
import sys
import threading
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .douyin import numeric_source_id

logger = logging.getLogger(__name__)

WARMUP_URL = "https://www.douyin.com/"
DETAIL_MARKER = "aweme/detail"
ITEMINFO_PATH = "/web/api/v2/aweme/iteminfo/"
_DETAIL_PATH = "/aweme/v1/web/aweme/detail/"
_METADATA_HOSTS = frozenset({"www.douyin.com", "douyin.com", "www.iesdouyin.com", "iesdouyin.com"})
# Hidden windows are detected; an off-screen one is not. Same visibility to the
# user, and the only variant that survived the check.
OFFSCREEN_ARGS = ("--window-position=-3000,-3000", "--window-size=1024,768")
WARMUP_MS = 7000
POLL_MS = 500


class BrowserUnavailable(RuntimeError):
    """Playwright or a usable Chrome is missing, so this rung cannot run."""


class ResolveFailed(RuntimeError):
    """The browser ran, but Douyin named no address we can use."""


def _resolve_failure(message: str, reason: str, stage: str, *, stats=None, http_status=None) -> ResolveFailed:
    error = ResolveFailed(message)
    error.details = {"reason": reason, "stage": stage}
    if stats is not None:
        error.details.update({key: value for key, value in stats.items()
                              if type(value) is int and value >= 0})
    if type(http_status) is int and 100 <= http_status <= 599:
        error.details["last_http_status"] = http_status
    return error


@dataclass(frozen=True)
class ResolvedMedia:
    """What the page told us, before anything has been downloaded."""

    urls: tuple[str, ...]
    info: dict = field(default_factory=dict)
    size_bytes: int = 0
    # Each member is one original image, with its own CDN mirrors. Flattening
    # these would confuse mirror alternatives with additional carousel pages.
    image_urls: tuple[tuple[str, ...], ...] = ()
    # One aligned group per image page; () means that page is static.
    image_motion_urls: tuple[tuple[str, ...], ...] = ()


def _metadata_entries(response_url: str, payload: object) -> tuple[dict, ...]:
    """Read only the two metadata shapes published by Douyin's own pages.

    The mobile share page uses ``item_list`` rather than ``aweme_detail``.
    Merely seeing an ID in the page URL or loader state is insufficient: each
    returned entry must carry its own unambiguous identity. Responses from
    recommendations can be filed, but resolve() still selects the requested ID.
    """
    parsed = urlsplit(response_url)
    if (parsed.scheme != "https" or parsed.hostname not in _METADATA_HOSTS
            or parsed.username is not None or parsed.password is not None
            or not isinstance(payload, dict)):
        return ()
    path = parsed.path.rstrip("/") + "/"
    if path == _DETAIL_PATH:
        raw = [payload.get("aweme_detail")]
    elif path == ITEMINFO_PATH:
        raw = payload.get("item_list")
        if not isinstance(raw, list):
            return ()
    else:
        return ()

    entries = [entry for entry in raw if isinstance(entry, dict)]
    identities = [str(entry.get("aweme_id") or "") for entry in entries]
    identity_counts = Counter(identities)
    return tuple(entry for entry, identity in zip(entries, identities)
                 if identity.isascii() and identity.isdecimal()
                 and identity_counts[identity] == 1)


def _is_metadata_response(response_url: str) -> bool:
    parsed = urlsplit(response_url)
    return (parsed.scheme == "https" and parsed.hostname in _METADATA_HOSTS
            and parsed.username is None and parsed.password is None
            and parsed.path.rstrip("/") + "/" in {_DETAIL_PATH, ITEMINFO_PATH})


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
    from .douyin_images import image_url_groups
    from .douyin_motion import motion_url_groups

    video = detail.get("video") or {}
    if not isinstance(video, dict):
        video = {}
    play_addr = video.get("play_addr") or {}
    if not isinstance(play_addr, dict):
        play_addr = {}
    urls = [str(u) for u in (play_addr.get("url_list") or []) if str(u).startswith("http")]
    explicit_image_post = (str(detail.get("aweme_type") or "") in {"68", "150"}
                           or isinstance(detail.get("image_post_info"), dict))
    images = image_url_groups(detail) if explicit_image_post or not urls else ()
    author = detail.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    info = {
        "id": found_id,
        "title": str(detail.get("desc") or "").strip(),
        "uploader": author.get("nickname") or None,
        "webpage_url": page_url,
        "_clipmind_strategy": "browser session",
    }
    if images:
        # An image post may carry a music track or a generated video preview.
        # Neither substitutes for the requested ordered original pages.
        info.update(duration=0.0, _clipmind_media_type="image_collection",
                    _clipmind_image_count=len(images))
        motions = motion_url_groups(detail)
        info["_clipmind_image_motion_count"] = sum(bool(group) for group in motions)
        music = detail.get("music")
        music = music if isinstance(music, dict) else {}
        info["_clipmind_image_audio"] = (
            "not_acquired" if music.get("play_url") or music.get("play_url_list")
            else "unavailable"
        )
        return ResolvedMedia(
            urls=(), info={k: v for k, v in info.items() if v not in (None, "")},
            image_urls=images,
            image_motion_urls=motions,
        )
    if not urls:
        raise ResolveFailed("the video detail carried no playable address")
    # Milliseconds upstream; MediaAsset.duration is seconds.
    duration_ms = video.get("duration") or 0
    info["duration"] = round(float(duration_ms) / 1000.0, 3) if duration_ms else 0.0
    return ResolvedMedia(
        urls=tuple(urls),
        info={k: v for k, v in info.items() if v not in (None, "")},
        size_bytes=int(play_addr.get("data_size") or 0),
    )


async def resolve(page_url: str, *, timeout: float = 90.0) -> ResolvedMedia:
    """Open the page just long enough for it to name its own media address."""
    # Local import: the image module shares this resolver's error contract.
    from .douyin_images import ImagePostBudgetExceeded

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise BrowserUnavailable("playwright is not installed") from exc

    api = sys.modules.get("playwright.async_api")
    navigation_errors = tuple({candidate for candidate in (
        asyncio.TimeoutError, getattr(api, "TimeoutError", None), getattr(api, "Error", None),
    ) if isinstance(candidate, type) and issubclass(candidate, Exception)})

    captured: dict[str, dict] = {}
    budget_errors: dict[str, ImagePostBudgetExceeded] = {}
    last_status: dict = {}
    stats = {"metadata_responses": 0, "embedded_attempts": 0}
    embedded: ResolvedMedia | None = None
    embedded_reads = 0
    next_embedded_read = 0.0
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
                if not _is_metadata_response(response.url):
                    return
                stats["metadata_responses"] += 1
                last_status["status"] = response.status
                try:
                    payload = await response.json()
                except Exception:  # noqa: BLE001 - a challenge page is not JSON
                    last_status["status"] = response.status
                    return
                for detail in _metadata_entries(response.url, payload):
                    found = str(detail["aweme_id"])
                    candidate = {"aweme_detail": detail}
                    try:
                        _media_info(candidate, page_url, found)
                    except ImagePostBudgetExceeded as exc:
                        # Keep the entry's own identity. A recommendation's
                        # large carousel must not stop the requested item.
                        budget_errors[found] = exc
                        continue
                    except (ResolveFailed, TypeError, ValueError, AttributeError):
                        # Some iteminfo replies identify the target before they
                        # carry a playable address. Keep waiting for a usable
                        # response; this partial entry must not end the poll or
                        # overwrite an earlier usable entry for the same ID.
                        continue
                    captured[found] = candidate

            page.on("response", on_response)
            # The warm-up is the point: the cookies the endpoint checks are
            # written by the site's own scripts, not by Set-Cookie.
            for destination, phase in ((WARMUP_URL, "warmup"), (page_url, "navigation")):
                try:
                    await page.goto(destination, wait_until="domcontentloaded", timeout=45000)
                    if phase == "warmup":
                        await page.wait_for_timeout(WARMUP_MS)
                except navigation_errors as exc:
                    timed_out = isinstance(exc, asyncio.TimeoutError) or type(exc).__name__ == "TimeoutError"
                    raise _resolve_failure(
                        "the browser navigation timed out" if timed_out else "the browser navigation failed",
                        "navigation_timeout" if timed_out else "navigation_failed", phase, stats=stats,
                    ) from exc
            while time.monotonic() < deadline:
                # Only a share link, which names no video, may take its identity
                # from where it landed. A request that already named one keeps
                # it: a redirect is not permission to return a different video.
                if expected is None:
                    expected = numeric_source_id(page.url)
                if expected and expected in captured:
                    break
                if expected and expected in budget_errors:
                    raise budget_errors[expected]
                # Image notes can carry all original pages in server-rendered
                # Pace state without ever requesting aweme/detail. Read only
                # that named, exact-ID contract; never scrape arbitrary <img>s.
                if expected and hasattr(page, "content") and embedded_reads < 12:
                    now = time.monotonic()
                    if now >= next_embedded_read:
                        embedded_reads += 1
                        stats["embedded_attempts"] = embedded_reads
                        next_embedded_read = now + 5.0
                        from .douyin_embedded import embedded_media_info
                        try:
                            html = await asyncio.wait_for(page.content(), timeout=min(5.0, max(0.01, deadline - now)))
                            embedded = embedded_media_info(html, page.url, expected)
                        except ImagePostBudgetExceeded:
                            # The embedded parser already verified both IDs.
                            # Resource refusal is final, not missing metadata.
                            if expected not in captured:
                                raise
                        except Exception:  # An in-progress page navigation is retried on the next poll.
                            pass
                        if embedded is not None:
                            break
                await page.wait_for_timeout(POLL_MS)
        finally:
            await browser.close()

    if embedded is not None:
        return embedded
    if not expected:
        raise _resolve_failure("the page never resolved to a video id", "target_id_unresolved", "identity", stats=stats)
    if expected not in captured and expected in budget_errors:
        raise budget_errors[expected]
    if expected not in captured:
        status = last_status.get("status")
        raise _resolve_failure(
            f"the page returned no detail for {expected} (last status {status})"
            if status else f"the page returned no detail for {expected}",
            "metadata_unavailable", "metadata", stats=stats, http_status=status,
        )
    return _media_info(captured[expected], page_url, expected)


class _Stopped(Exception):
    """Internal: the transfer was asked to stop and did."""


def _fetch_to(url: str, destination: Path, chunk: int, stop: threading.Event, *,
              deadline: float, max_bytes: int | None, on_progress=None,
              started: float | None = None, spent: list[int] | None = None) -> int:
    """One bounded mirror transfer; only numeric progress leaves this thread."""
    written = 0
    started = time.monotonic() if started is None else started
    spent = [0] if spent is None else spent
    last_report = -math.inf

    def check() -> float:
        if stop.is_set():
            raise _Stopped()
        now = time.monotonic()
        if now >= deadline:
            raise ResolveFailed("the media transfer exceeded its total time limit")
        return now

    def progress(total: int | None, *, force: bool = False) -> None:
        nonlocal last_report
        now = time.monotonic()
        if on_progress is not None and (force or now - last_report >= 1.0):
            last_report = now
            on_progress(written, total, max(0.0, now - started))

    remaining = deadline - check()
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    # The deadline covers all mirrors. A stalled socket is additionally bounded
    # to 30 seconds; a final blocked read can outlast the wall deadline by at
    # most that read's timeout before the worker exits and cleanup can run.
    with urllib.request.urlopen(request, timeout=min(remaining, 30.0)) as response:
        check()
        encoding = response.headers.get("Content-Encoding", "identity").strip().casefold()
        if encoding not in {"", "identity"}:
            raise ResolveFailed("compressed media transport is unsupported")
        raw_length = response.headers.get("Content-Length")
        total = None
        if raw_length is not None:
            try:
                total = int(raw_length)
            except (TypeError, ValueError):
                raise ResolveFailed("the media response has an invalid content length") from None
            if total < 0:
                raise ResolveFailed("the media response has an invalid content length")
            if max_bytes is not None and total > max_bytes - spent[0]:
                raise ResolveFailed("the media transfer exceeded its byte limit")
        status = getattr(response, "status", None)
        content_range = response.headers.get("Content-Range")
        if status == 206 or content_range is not None:
            # Douyin CDNs have returned 206 even without our sending Range.
            # Only a provably whole representation may be accepted: its range
            # starts at zero, covers the stated full length, and matches CL.
            match = (re.fullmatch(r"bytes 0-([0-9]+)/([0-9]+)", content_range.strip())
                     if isinstance(content_range, str) else None)
            if (status != 206 or match is None or total is None or total <= 0
                    or int(match[1]) != total - 1 or int(match[2]) != total):
                raise ResolveFailed("the media response contains an incomplete or unverified range")
        progress(total, force=True)
        read = getattr(response, "read1", None)
        if not callable(read):
            read = response.read
        with destination.open("wb") as handle:
            while True:
                check()
                amount = min(chunk, 64 * 1024)
                if max_bytes is not None:
                    amount = min(amount, max_bytes - spent[0] + 1)
                block = read(amount)
                check()
                if not block:
                    break
                spent[0] += len(block)
                if max_bytes is not None and spent[0] > max_bytes:
                    raise ResolveFailed("the media transfer exceeded its byte limit")
                written += len(block)
                if total is not None and written > total:
                    raise ResolveFailed("the media response exceeded its declared length")
                handle.write(block)
                progress(total)
        if total is not None and written != total:
            raise ResolveFailed("the media response ended before its declared length")
        progress(total, force=True)
    return written


async def download(urls, destination: Path, *, chunk: int = 64 * 1024,
                   on_progress=None, timeout: float = 900.0,
                   max_bytes: int | None = None) -> Path:
    """Fetch the named address with a plain client; the CDN asks for nothing.

    Cancellation must stop the transfer, not merely stop waiting for it. A
    thread cannot be killed, so it is asked to stop and then waited for: a
    transfer still writing after cancellation would race the very cleanup that
    cancellation exists to trigger.
    """
    if (type(chunk) is not int or chunk <= 0
            or type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0
            or (max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0))):
        raise ResolveFailed("invalid media transfer limits")
    destination.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    deadline = started + timeout
    spent = [0]
    active = True
    completed = False
    generation = 0
    delivered = None
    last: Exception | None = None

    def deliver(token: int, written: int, total: int | None, elapsed: float) -> None:
        nonlocal delivered
        event = (token, written, total, elapsed)
        if active and token == generation and on_progress is not None and event != delivered:
            delivered = event
            try:
                on_progress(written, total, elapsed)
            except Exception:  # A progress display cannot corrupt the transfer.
                logger.warning("Douyin download progress could not be reported")

    try:
        for url in urls:
            if time.monotonic() >= deadline:
                last = ResolveFailed("the media transfer exceeded its total time limit")
                break
            if max_bytes is not None and spent[0] >= max_bytes:
                last = ResolveFailed("the media transfer exceeded its byte limit")
                break
            generation += 1
            token = generation
            latest = [None]

            def enqueue(written: int, total: int | None, elapsed: float, *,
                        token=token, latest=latest) -> None:
                # Each mirror owns its own event token. A delayed callback from
                # a failed mirror cannot overwrite the next mirror's progress.
                latest[0] = (written, total, elapsed)
                loop.call_soon_threadsafe(deliver, token, written, total, elapsed)

            deliver(token, 0, None, max(0.0, time.monotonic() - started))
            stop = threading.Event()
            worker = asyncio.create_task(asyncio.to_thread(
                _fetch_to, url, destination, chunk, stop, deadline=deadline,
                max_bytes=max_bytes, on_progress=enqueue if on_progress is not None else None,
                started=started, spent=spent,
            ))
            try:
                written = await asyncio.shield(worker)
            except asyncio.CancelledError:
                stop.set()
                # Returning while a thread still writes would race cleanup.
                # A repeated cancellation must not cut the shielded wait short.
                while not worker.done():
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(worker)
                with contextlib.suppress(BaseException):
                    worker.result()
                raise
            except Exception as exc:  # noqa: BLE001 - try the next mirror
                last = exc
                # Transport errors can include signed CDN URLs. Keep raw
                # exception text out of the normal log and public diagnosis.
                logger.warning("Douyin media address failed (%s)", type(exc).__name__)
                destination.unlink(missing_ok=True)
                continue
            if written > 0:
                # Deliver the final count synchronously before the caller can
                # advance to sampling. Queued copies are deduplicated/disabled.
                final = latest[0] or (written, None, max(0.0, time.monotonic() - started))
                deliver(token, *final)
                completed = True
                return destination
            last = ResolveFailed("the address returned an empty body")
            destination.unlink(missing_ok=True)
        if isinstance(last, ResolveFailed):
            raise last
        raise ResolveFailed("no named address could be downloaded") from None
    finally:
        active = False
        if not completed:
            destination.unlink(missing_ok=True)
