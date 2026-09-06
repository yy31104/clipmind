"""A probe asks what a source is. It must not acquire it.

The important test here serves media from a local HTTP server and counts the
bytes that actually leave it. Asserting on yt-dlp's arguments would only prove
we asked for no download; counting bytes proves none arrived.
"""

import asyncio
import os
import shutil
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from clipmind import acquisition, fetch
from clipmind.config import Settings

# Large enough that socket buffering is noise. yt-dlp hangs up once it has
# sniffed the container, but the kernel has already accepted whatever the server
# pushed, so the server-side count is an upper bound on what was really read.
MEDIA_BYTES = 32 * 1024 * 1024
# Proportional rather than tuned: reading the head of a file to identify it is
# legitimate; reading the file is not. Observed here is well under a megabyte.
PROBE_BUDGET = MEDIA_BYTES // 8

has_yt_dlp = shutil.which("yt-dlp") is not None


class CountingMediaServer:
    """Serves one file and remembers how much of it it actually sent."""

    def __init__(self, payload: bytes, content_type: str = "video/mp4") -> None:
        self.payload = payload
        self.content_type = content_type
        self.bytes_sent = 0
        self.requests: list[str] = []
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # keep the test output quiet
                return

            def handle_one_request(self):
                try:
                    return super().handle_one_request()
                except (ConnectionResetError, BrokenPipeError):
                    self.close_connection = True

            def _range(self):
                header = self.headers.get("Range", "")
                if not header.startswith("bytes="):
                    return 0, len(server_self.payload) - 1
                start, _, end = header[6:].partition("-")
                first = int(start or 0)
                last = int(end) if end else len(server_self.payload) - 1
                return first, min(last, len(server_self.payload) - 1)

            def do_HEAD(self) -> None:
                server_self.requests.append(f"HEAD {self.path}")
                self.send_response(200)
                self.send_header("Content-Type", server_self.content_type)
                self.send_header("Content-Length", str(len(server_self.payload)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

            def do_GET(self) -> None:
                server_self.requests.append(f"GET {self.path}")
                first, last = self._range()
                body = server_self.payload[first : last + 1]
                partial = (first, last) != (0, len(server_self.payload) - 1)
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", server_self.content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                if partial:
                    self.send_header(
                        "Content-Range",
                        f"bytes {first}-{last}/{len(server_self.payload)}",
                    )
                self.end_headers()
                # Counted per chunk, and only once the write succeeds: a client
                # that hangs up after the headers must not be billed for a body
                # it never received.
                for offset in range(0, len(body), 32 * 1024):
                    chunk = body[offset : offset + 32 * 1024]
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    server_self.bytes_sent += len(chunk)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/video.mp4"


@unittest.skipUnless(has_yt_dlp, "needs yt-dlp, which the unit CI job omits")
class ProbeTransfersNoMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config = Settings(cookie_sources=("-",), probe_timeout=60.0)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_probing_a_direct_media_url_transfers_almost_nothing(self) -> None:
        with CountingMediaServer(b"\0" * MEDIA_BYTES) as server:
            result = await fetch.probe(server.url, config=self.config)
            probed_bytes = server.bytes_sent

        self.assertIn(result.status, {"reachable", "unknown"})
        self.assertLess(probed_bytes, PROBE_BUDGET)
        # The transfer was cut short rather than completed.
        self.assertLess(probed_bytes, MEDIA_BYTES)

    async def test_acquiring_the_same_url_does_transfer_the_media(self) -> None:
        # The contrast that makes the previous assertion mean something: the
        # same server, the same file, through acquisition instead.
        with CountingMediaServer(b"\0" * MEDIA_BYTES) as server:
            try:
                await fetch.fetch(server.url, self.root / "job", config=self.config)
            except fetch.FetchError as exc:  # pragma: no cover - diagnostic aid
                self.skipTest(f"yt-dlp could not acquire the fixture: {exc}")
            acquired_bytes = server.bytes_sent

        self.assertGreaterEqual(acquired_bytes, MEDIA_BYTES)

    async def test_a_user_config_cannot_make_a_probe_write_files(self) -> None:
        # An earlier version of this test planted the config in a directory the
        # child never ran in, so it passed without exercising anything. The real
        # exposure is the user's own config, which no working directory hides.
        home = self.root / "home"
        (home / "yt-dlp").mkdir(parents=True)
        (home / "yt-dlp" / "config").write_text(
            "--no-simulate\n--write-info-json\n", encoding="utf-8"
        )

        with patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home)}):
            with CountingMediaServer(b"\0" * (2 * 1024 * 1024)) as server:
                # The sandbox reports anything a probe leaves behind. Silence is
                # the assertion: the config was never allowed to take effect.
                with self.assertNoLogs("clipmind.fetch", level="WARNING"):
                    await fetch.probe(server.url, config=self.config)

    async def test_a_large_page_is_read_in_full(self) -> None:
        # A known limitation, pinned rather than hidden: yt-dlp exposes no cap
        # on the metadata response it reads, so ClipMind's budget for a page is
        # the wall clock, not a byte count. Flipping this assertion is what a
        # real read budget would look like.
        page = (
            b"<html><body>"
            + b"<p>padding padding padding</p>" * 120_000
            + b'<video src="clip.mp4"></video></body></html>'
        )
        with CountingMediaServer(page, content_type="text/html") as server:
            await fetch.probe(
                server.url.replace("/video.mp4", "/page.html"), config=self.config
            )
            served = server.bytes_sent

        self.assertGreaterEqual(served, len(page))


class ProbeContractTests(unittest.IsolatedAsyncioTestCase):
    """The parts that hold with no network and no yt-dlp."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.engine = patch.object(fetch, "_engine", fetch.AcquisitionEngine())
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()
        self.tempdir.cleanup()

    def test_the_probe_command_asks_for_no_media(self) -> None:
        args = fetch._probe_args("https://example.com/v", [], Settings())

        self.assertIn("--skip-download", args)
        self.assertNotIn("--no-simulate", args)
        self.assertNotIn("-o", args)
        self.assertNotIn("-f", args)

    async def test_a_probe_that_times_out_reports_unknown(self) -> None:
        async def never_returns(args, timeout, **kwargs):
            raise asyncio.TimeoutError

        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run_budgeted", new=never_returns):
            result = await fetch.probe("https://example.com/v", config=Settings())

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.failure_code, "probe_timeout")

    async def test_cancellation_still_propagates(self) -> None:
        async def cancelled(args, timeout, **kwargs):
            raise asyncio.CancelledError

        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run_budgeted", new=cancelled):
            with self.assertRaises(asyncio.CancelledError):
                await fetch.probe("https://example.com/v", config=Settings())

    async def test_a_failed_probe_never_falls_back_to_acquisition(self) -> None:
        async def must_not_acquire(*args, **kwargs):
            raise AssertionError("a probe must never acquire")

        async def refuses(args, timeout, **kwargs):
            return 1, "", "ERROR: This is a private video"

        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run_budgeted", new=refuses), \
             patch.object(fetch.AcquisitionEngine, "acquire", must_not_acquire):
            result = await fetch.probe("https://example.com/v", config=Settings())

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.failure_code, "private_video")

    async def test_our_own_cookie_problem_is_not_a_verdict_on_the_video(self) -> None:
        async def cookie_failure(args, timeout, **kwargs):
            return 1, "", "ERROR: could not copy chrome cookie database"

        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run_budgeted", new=cookie_failure):
            result = await fetch.probe("https://example.com/v", config=Settings())

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.failure_code, "cookies_unavailable")

    async def test_a_missing_local_file_is_unavailable_not_unknown(self) -> None:
        result = await fetch.probe(str(self.root / "absent.mp4"), config=Settings())

        self.assertEqual(result.status, "unavailable")

    async def test_probing_leaves_no_acquisition_ownership_behind(self) -> None:
        async def refuses(args, timeout, **kwargs):
            return 1, "", "ERROR: connection reset"

        with patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("clipmind.fetch._run_budgeted", new=refuses):
            await fetch.probe("https://example.com/v", config=Settings())

        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(acquisition.leftovers(self.root), [])


@unittest.skipUnless(has_yt_dlp, "needs yt-dlp, which the unit CI job omits")
class ProbeOutputBudgetTests(unittest.IsolatedAsyncioTestCase):
    """A child that floods its pipes is killed, not buffered."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _flooding_yt_dlp(self, megabytes: int) -> None:
        fake = self.root / "yt-dlp"
        fake.write_text(
            "#!/bin/sh\n"
            f"python3 -c \"import sys; sys.stderr.write('x'*{megabytes}*1024*1024)\"\n"
            'echo \'{"id":"abc","title":"T","duration":5}\'\n',
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["PATH"] = f"{self.root}{os.pathsep}{os.environ['PATH']}"

    async def test_a_flood_on_stderr_is_capped_not_collected(self) -> None:
        entered = os.environ["PATH"]
        self._flooding_yt_dlp(12)
        try:
            result = await fetch.probe(
                "https://example.com/v", config=Settings(cookie_sources=("-",))
            )
        finally:
            os.environ["PATH"] = entered

        # Valid metadata arrived on stdout; the budget still refuses the answer.
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.failure_code, "probe_budget_exceeded")


class ProbeMetadataTests(unittest.IsolatedAsyncioTestCase):
    """Valid JSON is not the same as usable metadata."""

    def setUp(self) -> None:
        self.engine = patch.object(fetch, "_engine", fetch.AcquisitionEngine())
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()

    async def _probe_returning(self, payload: str, adapter=None):
        async def responds(args, timeout, **kwargs):
            return 0, payload, ""

        patches = [
            patch("clipmind.fetch.shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("clipmind.fetch._run_budgeted", new=responds),
        ]
        if adapter is not None:
            patches.append(patch("clipmind.fetch.adapter_for", return_value=adapter))
        for entered in patches:
            entered.start()
        try:
            return await fetch.probe("https://example.com/v", config=Settings())
        finally:
            for entered in reversed(patches):
                entered.stop()

    async def test_json_that_identifies_no_source_is_unknown(self) -> None:
        for payload in ("null", "[]", "{}", '{"id": ""}'):
            with self.subTest(payload=payload):
                result = await self._probe_returning(payload)
                self.assertEqual(result.status, "unknown")

    async def test_an_adapter_without_the_identity_hook_still_probes(self) -> None:
        class Legacy:
            """Only the originally required protocol, as a plugin may be."""

            name = platform = "legacy"
            local = generic = False

            def matches(self, source: str) -> bool:
                return True

            def normalize_info(self, source: str, info: dict) -> dict:
                return dict(info)

        result = await self._probe_returning(
            '{"id": "abc", "title": "T", "duration": 3}', adapter=Legacy()
        )

        self.assertEqual(result.status, "reachable")
        self.assertEqual(result.source_id, "abc")


if __name__ == "__main__":
    unittest.main()
