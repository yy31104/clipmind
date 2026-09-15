"""A live stream is refused before any of its media is downloaded.

Local ClipMind has no time or cost limit, but an analysis job needs an end: a
24-hour stream would download until it stopped, holding a queue slot the whole
time. The unit tests stand in for yt-dlp. The integration tests run the real
tool against a local server and count the bytes that actually leave it, because
asserting on arguments would only prove we asked for no download.
"""

import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from clipmind import acquisition, fetch, pipeline
from clipmind.config import Settings

YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
NO_COOKIES = Settings(cookie_sources=("-",))


def reporting(info: dict, calls: list[list[str]]):
    """Stand in for yt-dlp that resolves metadata and, unless live, writes media."""

    async def run(args: list[str], **kwargs):
        calls.append(args)
        payload = dict(info)
        if not fetch._is_live(payload):
            root = Path(args[args.index("-o") + 1]).parent
            root.mkdir(parents=True, exist_ok=True)
            media = root / "source.mp4"
            media.write_bytes(b"media")
            payload["requested_downloads"] = [{"filepath": str(media)}]
        return 0, json.dumps(payload), ""

    return run


def failing(message: str, calls: list[list[str]]):
    async def run(args: list[str], **kwargs):
        calls.append(args)
        return 1, "", f"ERROR: [youtube] dQw4w9WgXcQ: {message}"

    return run


class LiveStreamRefusalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.engine = patch.object(fetch, "_engine", fetch.AcquisitionEngine())
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()
        self.tempdir.cleanup()

    async def fetch_with(self, run, config: Settings | None = None):
        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run", new=run):
            return await fetch.fetch(YOUTUBE, self.root / "job", config=config or Settings())

    async def test_a_live_stream_is_refused_without_trying_other_rungs(self) -> None:
        calls: list[list[str]] = []
        with self.assertRaises(fetch.FetchError) as raised:
            await self.fetch_with(reporting({"id": "x", "is_live": True, "live_status": "is_live"}, calls))
        self.assertEqual(raised.exception.code, "live_stream_unsupported")
        self.assertEqual(len(calls), 1, "another cookie source would find the same stream")
        args = calls[0]
        self.assertEqual(args[args.index("--match-filter") + 1], fetch.LIVE_FILTER)

    async def test_a_scheduled_stream_is_refused(self) -> None:
        calls: list[list[str]] = []
        with self.assertRaises(fetch.FetchError) as raised:
            await self.fetch_with(reporting({"id": "x", "is_live": False, "live_status": "is_upcoming"}, calls))
        self.assertEqual(raised.exception.code, "live_stream_unsupported")

    async def test_platform_wording_for_a_scheduled_event_is_classified(self) -> None:
        for message in ("This live event will begin in 3 hours.", "Premieres in 2 hours"):
            with self.subTest(message=message):
                with self.assertRaises(fetch.FetchError) as raised:
                    await self.fetch_with(failing(message, []), NO_COOKIES)
                self.assertEqual(raised.exception.code, "live_stream_unsupported")

    async def test_the_recording_of_a_finished_stream_is_acquired(self) -> None:
        asset = await self.fetch_with(
            reporting({"id": "x", "title": "Replay", "is_live": False, "live_status": "was_live"}, []),
            NO_COOKIES,
        )
        self.assertEqual(asset.media_path.name, "source.mp4")

    async def test_process_anyway_does_not_bypass_the_refusal(self) -> None:
        workdir = self.root / "forced"
        calls: list[list[str]] = []
        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run", new=reporting({"id": "x", "is_live": True}, calls)):
            with self.assertRaises(fetch.FetchError) as raised:
                await pipeline.process(
                    YOUTUBE,
                    workdir,
                    pipeline.Pools.from_settings(NO_COOKIES),
                    lambda *args: None,
                    config=NO_COOKIES,
                    options={"force": True},
                )
        self.assertEqual(raised.exception.code, "live_stream_unsupported")
        self.assertEqual(acquisition.leftovers(workdir), [])


class _CountingServer:
    """Serves one media file and remembers how many bytes it sent."""

    def __init__(self) -> None:
        self.sent = 0
        payload = b"\x00" * (256 * 1024)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                    outer.sent += len(payload)
                except BrokenPipeError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(shutil.which("yt-dlp"), "yt-dlp required")
class RealFilterTests(unittest.IsolatedAsyncioTestCase):
    """The shipped acquisition arguments, run by the real yt-dlp."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.media = _CountingServer()
        self.engine = patch.object(fetch, "_engine", fetch.AcquisitionEngine())
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()
        self.media.close()
        self.tempdir.cleanup()

    async def acquire(self, extra: dict):
        info = {
            "id": "sample", "title": "sample", "extractor": "generic", "extractor_key": "Generic",
            "webpage_url": f"{self.media.base}/page", "_type": "video",
            "formats": [{"format_id": "0", "url": f"{self.media.base}/media.mp4", "ext": "mp4",
                         "vcodec": "h264", "acodec": "aac", "protocol": "http"}],
            **extra,
        }
        info_path = self.root / "info.json"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        real_run = fetch._run

        async def from_info_json(args: list[str], **kwargs):
            # Same arguments ClipMind sends, with metadata loaded from a file
            # instead of a site, so the test needs no network beyond localhost.
            return await real_run([*args[:-1], "--load-info-json", str(info_path)], **kwargs)

        workdir = self.root / "job"
        with patch("clipmind.fetch._run", new=from_info_json):
            return await fetch.fetch(YOUTUBE, workdir, config=NO_COOKIES), workdir

    async def test_a_live_stream_downloads_no_media(self) -> None:
        for extra in ({"is_live": True}, {"live_status": "is_upcoming", "is_live": False}):
            with self.subTest(extra=extra):
                with self.assertRaises(fetch.FetchError) as raised:
                    await self.acquire(extra)
                self.assertEqual(raised.exception.code, "live_stream_unsupported")
                self.assertEqual(self.media.sent, 0)
                self.assertEqual(sorted((self.root / "job").rglob("source.*")), [])

    async def test_an_ordinary_video_still_downloads(self) -> None:
        asset, _workdir = await self.acquire({"is_live": False})
        self.assertGreater(self.media.sent, 0)
        self.assertTrue(asset.media_path.is_file())


if __name__ == "__main__":
    unittest.main()
