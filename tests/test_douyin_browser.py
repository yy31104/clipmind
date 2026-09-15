"""The Douyin browser rung: what it maps, what it refuses, and where it sits.

No real browser and no network. ``resolve()`` imports playwright inside the
call, so replacing that module exercises the wiring a browser would drive --
filing detail responses by the video they describe and choosing among them.
That wiring is where a redirect or a feed response can substitute one video for
another, so it is tested here rather than left to a live run.
"""

import asyncio
import sys
import tempfile
import threading
import time
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from clipmind.config import Settings
from clipmind.fetch import AcquisitionEngine, CookieRung, DouyinBrowserRung
from clipmind.sources import MediaAsset, douyin_browser
from clipmind.sources.base import SourceAdapter

WANTED = "7682348302453082787"
OTHER = "1111111111111111111"
PAGE = f"https://www.douyin.com/video/{WANTED}"
SHARE_LINK = "https://v.douyin.com/bRras9xtBzo/"


def payload(aweme_id=WANTED, desc="量化之王", **video_overrides):
    video = {
        "duration": 604647,
        "play_addr": {"data_size": 93521000, "url_list": ["https://cdn.example/a"]},
    }
    video.update(video_overrides)
    return {
        "aweme_detail": {
            "aweme_id": aweme_id,
            "desc": desc,
            "author": {"nickname": "Vincent hahaha"},
            "video": video,
        }
    }


class _Response:
    """One detail response, as the page would deliver it."""

    def __init__(self, body):
        self.url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        self.status = 200
        self._body = body

    async def json(self):
        return self._body


class _Page:
    """Warms up, lands somewhere, then delivers its detail responses in order."""

    def __init__(self, landing, bodies):
        self.url = "about:blank"
        self._handlers = []
        self._landing = landing
        self._bodies = bodies
        self._visits = 0

    def on(self, event, handler):
        self._handlers.append(handler)

    async def goto(self, url, **kwargs):
        self._visits += 1
        if self._visits == 1:
            self.url = douyin_browser.WARMUP_URL
            return
        self.url = self._landing
        for body in self._bodies:
            for handler in self._handlers:
                await handler(_Response(body))

    async def wait_for_timeout(self, milliseconds):
        await asyncio.sleep(0)


class _Context:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


class _Browser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    async def new_context(self, **kwargs):
        return _Context(self._page)

    async def close(self):
        self.closed = True


class _Chromium:
    def __init__(self, page):
        self._page = page

    async def launch(self, **kwargs):
        return _Browser(self._page)


class _Engine:
    def __init__(self, page):
        self.chromium = _Chromium(page)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Clock:
    """Time that only moves when resolve() looks at it, so waiting is instant."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 1.0
        return self.now


def browser_substitute(landing, bodies):
    module = types.ModuleType("playwright.async_api")
    module.async_playwright = lambda page=_Page(landing, bodies): _Engine(page)
    return {"playwright": types.ModuleType("playwright"), "playwright.async_api": module}


class ResolveWiringTests(unittest.IsolatedAsyncioTestCase):
    """Where a redirect or a feed response could substitute another video."""

    async def resolve(self, requested, landing, bodies):
        with patch.dict(sys.modules, browser_substitute(landing, bodies)), \
             patch.object(douyin_browser, "time", _Clock()):
            return await douyin_browser.resolve(requested, timeout=10)

    async def test_a_redirect_cannot_replace_a_requested_video(self):
        # The request named a video. Landing somewhere else and being handed
        # that video's detail is not permission to return it.
        with self.assertRaises(douyin_browser.ResolveFailed) as raised:
            await self.resolve(
                PAGE,
                f"https://www.douyin.com/video/{OTHER}?from=redirect",
                [payload(aweme_id=OTHER, desc="重定向后的另一个视频")],
            )

        self.assertIn(WANTED, str(raised.exception))

    async def test_a_response_for_another_video_arriving_first_is_not_taken(self):
        # The warm-up feed asks for its own recommendations, so arriving first
        # is not evidence of being the right answer.
        resolved = await self.resolve(
            PAGE,
            PAGE,
            [payload(aweme_id=OTHER, desc="推荐视频"), payload()],
        )

        self.assertEqual(resolved.info["id"], WANTED)
        self.assertEqual(resolved.info["title"], "量化之王")

    async def test_a_share_link_takes_its_identity_from_where_it_lands(self):
        # A share link names no video, so the landing page is the only source
        # of identity it can have -- and the response must still match it.
        resolved = await self.resolve(SHARE_LINK, PAGE, [payload()])

        self.assertEqual(resolved.info["id"], WANTED)

    async def test_a_share_link_bounced_to_the_feed_is_refused(self):
        # Douyin answers an unavailable video by bouncing to the feed with a
        # recommendation attached. Nothing there was requested.
        with self.assertRaises(douyin_browser.ResolveFailed):
            await self.resolve(
                SHARE_LINK,
                f"https://www.douyin.com/jingxuan?previous_page=web_video_404_link&modal_id={OTHER}",
                [payload(aweme_id=OTHER, desc="推荐视频")],
            )

    async def test_a_page_that_returns_nothing_is_refused(self):
        with self.assertRaises(douyin_browser.ResolveFailed):
            await self.resolve(PAGE, PAGE, [])


class IdentityTests(unittest.TestCase):
    """The page is asked for one video; anything else must be refused."""

    def test_a_payload_describing_another_video_is_refused(self):
        with self.assertRaises(douyin_browser.ResolveFailed) as raised:
            douyin_browser._media_info(payload(aweme_id=OTHER), PAGE, WANTED)

        self.assertIn(OTHER, str(raised.exception))
        self.assertIn(WANTED, str(raised.exception))

    def test_a_payload_with_no_identity_is_refused(self):
        anonymous = payload()
        del anonymous["aweme_detail"]["aweme_id"]

        with self.assertRaises(douyin_browser.ResolveFailed):
            douyin_browser._media_info(anonymous, PAGE, WANTED)

    def test_the_requested_video_is_accepted(self):
        resolved = douyin_browser._media_info(payload(), PAGE, WANTED)

        self.assertEqual(resolved.info["id"], WANTED)


class MediaInfoTests(unittest.TestCase):
    def test_duration_is_converted_from_milliseconds(self):
        # Upstream reports milliseconds; MediaAsset.duration is seconds. Passing
        # the raw number through would report a ten-minute video as seven days.
        resolved = douyin_browser._media_info(payload(), PAGE, WANTED)

        self.assertEqual(resolved.info["duration"], 604.647)

    def test_downstream_asset_reads_every_field_it_needs(self):
        resolved = douyin_browser._media_info(payload(), PAGE, WANTED)
        adapter = SourceAdapter(name="douyin", platform="douyin")
        asset = MediaAsset(
            media_path=Path("/tmp/source.mp4"),
            info=adapter.normalize_info(PAGE, resolved.info),
        )

        self.assertEqual(asset.source_id, WANTED)
        self.assertEqual(asset.duration, 604.647)
        self.assertEqual(asset.platform, "douyin")
        self.assertEqual(asset.webpage_url, PAGE)
        self.assertNotEqual(asset.title, asset.source_id)

    def test_addresses_that_cannot_be_fetched_are_not_addresses(self):
        broken = payload()
        broken["aweme_detail"]["video"]["play_addr"]["url_list"] = ["blob:x", "data:y"]

        with self.assertRaises(douyin_browser.ResolveFailed):
            douyin_browser._media_info(broken, PAGE, WANTED)

    def test_unusable_payloads_are_refused_rather_than_guessed(self):
        for broken in (
            {},
            {"aweme_detail": None},
            {"aweme_detail": {"aweme_id": WANTED}},
            {"aweme_detail": {"aweme_id": WANTED, "video": {"play_addr": {"url_list": []}}}},
        ):
            with self.subTest(payload=broken):
                with self.assertRaises(douyin_browser.ResolveFailed):
                    douyin_browser._media_info(broken, PAGE, WANTED)


class LadderPlacementTests(unittest.TestCase):
    def setUp(self):
        self.engine = AcquisitionEngine()
        self.config = Settings()

    def described(self, key, config=None):
        return [rung.describe() for rung in self.engine.strategies(key, config or self.config)]

    def test_the_browser_rung_is_last_and_only_for_douyin(self):
        self.assertEqual(self.described("adapter:douyin")[-1], "browser session")
        self.assertNotIn("browser session", self.described("adapter:youtube"))
        self.assertNotIn("browser session", self.described("host:example.com"))

    def test_the_rung_can_be_turned_off(self):
        off = replace(self.config, douyin_browser_enabled=False)

        self.assertNotIn("browser session", self.described("adapter:douyin", off))

    def test_probing_never_treats_the_browser_rung_as_a_probe_strategy(self):
        # A probe answers "what is this" inside a budget. Launching a browser to
        # decide whether one was needed would spend the budget on the question.
        rungs = self.engine.strategies("adapter:douyin", self.config)
        probeable = [rung for rung in rungs if isinstance(rung, CookieRung)]

        self.assertTrue(probeable)
        self.assertNotIn("browser session", [rung.describe() for rung in probeable])


class RungFailureTests(unittest.IsolatedAsyncioTestCase):
    async def acquire_raising(self, error):
        async def raising(*args, **kwargs):
            raise error

        with patch.object(douyin_browser, "resolve", new=raising):
            return await DouyinBrowserRung().acquire(PAGE, Path("/tmp"), Settings())

    async def test_a_missing_browser_is_a_diagnostic_not_a_crash(self):
        acquired, failure = await self.acquire_raising(
            douyin_browser.BrowserUnavailable("playwright is not installed")
        )

        self.assertIsNone(acquired)
        self.assertIn("no usable browser", failure.reason)
        self.assertEqual(failure.label, "browser session")

    async def test_a_refused_page_is_a_diagnostic_not_a_crash(self):
        acquired, failure = await self.acquire_raising(
            douyin_browser.ResolveFailed(f"the page returned no detail for {WANTED}")
        )

        self.assertIsNone(acquired)
        self.assertIn("no detail", failure.reason)

    async def test_cancellation_still_propagates(self):
        # Swallowing this would turn a stopped job into a silent rung failure and
        # strand the cleanup that cancellation is supposed to trigger.
        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError

        with patch.object(douyin_browser, "resolve", new=cancelled):
            with self.assertRaises(asyncio.CancelledError):
                await DouyinBrowserRung().acquire(PAGE, Path("/tmp"), Settings())


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def stoppable_transfer(self, ticks=300, lingers=0.0):
        """A transfer that notices the stop flag and takes a moment to unwind."""
        state = {"running": threading.Event(), "exited": threading.Event(), "completed": []}

        def transfer(url, destination, chunk, stop):
            state["running"].set()
            try:
                for _ in range(ticks):
                    if stop.is_set():
                        raise douyin_browser._Stopped()
                    time.sleep(0.01)
                destination.write_bytes(b"whole file")
                state["completed"].append(url)
                return 10
            finally:
                if lingers:
                    time.sleep(lingers)
                state["exited"].set()

        return state, transfer

    async def test_a_failing_address_falls_through_to_the_next(self):
        attempted = []

        def transfer(url, destination, chunk, stop):
            attempted.append(url)
            if url.endswith("/bad"):
                raise OSError("refused")
            destination.write_bytes(b"media")
            return 5

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "source.mp4"
            with patch.object(douyin_browser, "_fetch_to", transfer):
                path = await douyin_browser.download(
                    ["https://cdn.example/bad", "https://cdn.example/good"], destination
                )

            self.assertEqual(attempted, ["https://cdn.example/bad", "https://cdn.example/good"])
            self.assertEqual(path, destination)

    async def test_no_usable_address_is_refused(self):
        def transfer(url, destination, chunk, stop):
            raise OSError("refused")

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(douyin_browser, "_fetch_to", transfer):
                with self.assertRaises(douyin_browser.ResolveFailed):
                    await douyin_browser.download(
                        ["https://cdn.example/a"], Path(temporary) / "source.mp4"
                    )

    async def test_an_empty_body_is_not_a_download(self):
        def transfer(url, destination, chunk, stop):
            destination.write_bytes(b"")
            return 0

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(douyin_browser, "_fetch_to", transfer):
                with self.assertRaises(douyin_browser.ResolveFailed):
                    await douyin_browser.download(
                        ["https://cdn.example/a"], Path(temporary) / "source.mp4"
                    )

    async def cancel_download(self, state, transfer, times):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "source.mp4"
            with patch.object(douyin_browser, "_fetch_to", transfer):
                task = asyncio.create_task(
                    douyin_browser.download(["https://cdn.example/a"], destination)
                )
                await asyncio.to_thread(state["running"].wait, 5)
                for _ in range(times):
                    task.cancel()
                    for _ in range(3):
                        await asyncio.sleep(0)
                with self.assertRaises(asyncio.CancelledError):
                    await task
            return destination

    async def test_cancelling_stops_the_transfer_and_waits_for_it_to_exit(self):
        """Cancelling the wait is not cancelling the transfer."""
        state, transfer = self.stoppable_transfer()

        destination = await self.cancel_download(state, transfer, times=1)

        self.assertTrue(state["exited"].is_set(), "the transfer was still running")
        self.assertEqual(state["completed"], [])
        self.assertFalse(destination.exists())

    async def test_repeated_cancellation_still_waits_for_the_transfer_to_exit(self):
        """A second cancel must not cut the wait short.

        Returning while the thread is still unwinding hands a live writer to the
        cleanup that cancellation exists to trigger.
        """
        state, transfer = self.stoppable_transfer(lingers=0.4)

        destination = await self.cancel_download(state, transfer, times=2)

        self.assertTrue(state["exited"].is_set(), "a second cancel cut the wait short")
        self.assertEqual(state["completed"], [])
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
