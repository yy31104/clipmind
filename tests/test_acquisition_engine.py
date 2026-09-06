"""The cookie ladder: order, and what it remembers between videos.

Nothing covered this before, so these characterize the shipped behaviour first.
The ordering assertions must hold identically across the refactor; the two
isolation tests are new behaviour and are expected to fail before it.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind import fetch
from clipmind.config import Settings

YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
DOUYIN = "https://www.douyin.com/video/7100000000000000000"


def cookie_source(args: list[str]) -> str:
    if "--cookies-from-browser" in args:
        return args[args.index("--cookies-from-browser") + 1]
    if "--cookies" in args:
        return "file"
    return "-"


def fake_run(attempts: list[str], succeeds: str | None):
    """Stand in for yt-dlp: record the rung, and write media when it wins."""

    async def run(args: list[str], **kwargs):
        source = cookie_source(args)
        attempts.append(source)
        if source != succeeds:
            return 1, "", "ERROR: rung refused"
        root = Path(args[args.index("-o") + 1]).parent
        root.mkdir(parents=True, exist_ok=True)
        media = root / "source.mp4"
        media.write_bytes(b"media")
        return (
            0,
            json.dumps(
                {
                    "id": "abc",
                    "title": "T",
                    "duration": 1,
                    "requested_downloads": [{"filepath": str(media)}],
                }
            ),
            "",
        )

    return run


class CookieLadderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.counter = 0
        # What worked is per-process state. Giving each test its own engine is
        # only possible because it is an object rather than a module global.
        self.engine = patch.object(fetch, "_engine", fetch.AcquisitionEngine())
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()
        self.tempdir.cleanup()

    def workdir(self) -> Path:
        self.counter += 1
        return self.root / f"job-{self.counter}"

    async def attempt(self, url: str, *, succeeds: str | None, config=None):
        attempts: list[str] = []
        config = config or Settings()
        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run", new=fake_run(attempts, succeeds)):
            try:
                await fetch.fetch(url, self.workdir(), config=config)
            except fetch.FetchError:
                pass
        return attempts

    async def test_the_ladder_tries_cookie_sources_in_configured_order(self) -> None:
        self.assertEqual(await self.attempt(YOUTUBE, succeeds=None), ["chrome", "-"])

    async def test_a_cookie_file_is_tried_after_the_browser_sources(self) -> None:
        config = Settings(cookie_file="/tmp/cookies.txt")
        self.assertEqual(
            await self.attempt(YOUTUBE, succeeds=None, config=config),
            ["chrome", "-", "file"],
        )

    async def test_a_cookie_file_that_is_not_configured_is_never_tried(self) -> None:
        config = Settings(cookie_sources=("file",), cookie_file=None)
        self.assertEqual(await self.attempt(YOUTUBE, succeeds=None, config=config), [])

    async def test_the_rung_that_worked_is_tried_first_next_time(self) -> None:
        await self.attempt(YOUTUBE, succeeds="-")
        self.assertEqual(await self.attempt(YOUTUBE, succeeds="-"), ["-"])

    async def test_a_remembered_rung_that_is_no_longer_configured_is_ignored(
        self,
    ) -> None:
        await self.attempt(YOUTUBE, succeeds="-")
        config = Settings(cookie_sources=("chrome",))
        self.assertEqual(
            await self.attempt(YOUTUBE, succeeds="chrome", config=config), ["chrome"]
        )

    async def test_one_platforms_winner_does_not_reorder_anothers_ladder(self) -> None:
        await self.attempt(YOUTUBE, succeeds="-")
        # Douyin has learned nothing, so it must start at the top of its ladder.
        self.assertEqual(await self.attempt(DOUYIN, succeeds="chrome"), ["chrome"])

    async def test_generic_urls_are_remembered_per_host(self) -> None:
        await self.attempt("https://example.com/a.mp4", succeeds="-")
        self.assertEqual(
            await self.attempt("https://other.example.org/b.mp4", succeeds="chrome"),
            ["chrome"],
        )


class LadderFailureTests(unittest.IsolatedAsyncioTestCase):
    """Classification has to see the whole ladder, not a window on its tail."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.engine = patch.object(fetch, "_engine", fetch.AcquisitionEngine())
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()
        self.tempdir.cleanup()

    async def test_the_explaining_rung_survives_a_long_ladder(self) -> None:
        # Six rungs; only the first says anything useful. This is the shape a
        # window over the tail silently drops.
        config = Settings(
            cookie_sources=("chrome", "firefox", "edge", "brave", "-"),
            cookie_file="/tmp/cookies.txt",
        )

        async def run(args: list[str], **kwargs):
            if cookie_source(args) == "chrome":
                return 1, "", "ERROR: This is a private video"
            return 1, "", "ERROR: connection reset"

        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run", new=run):
            with self.assertRaises(fetch.FetchError) as raised:
                await fetch.fetch(YOUTUBE, self.root / "job", config=config)

        self.assertEqual(raised.exception.code, "private_video")


if __name__ == "__main__":
    unittest.main()
