"""Public-note contracts and adversarial redirects; no accounts or network."""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from clipmind import fetch
from clipmind.config import Settings
from clipmind.sources import adapter_for
from clipmind.sources.base import SourceError
from clipmind.sources import xiaohongshu as xhs

NOTE = "6adeaebf000000001200dfdf"
OTHER = "674051740000000007027a15"
PAGE = f"https://www.xiaohongshu.com/explore/{NOTE}"
SHORT = "https://xhslink.cn/o/A5OuQVEOT3E"
IMAGE = "https://sns-webpic-qc.xhscdn.com/page.png?sign=private"
VIDEO = "https://sns-video-bd.xhscdn.com/video.mp4?sign=private"


def note(kind="normal"):
    return {"noteId": NOTE, "title": "中文文档与图表", "type": kind,
        "user": {"nickname": "作者"}, "imageList": [{"urlDefault": IMAGE}],
        "video": {"media": {"stream": {"h264": [{"masterUrl": VIDEO,
            "duration": 12000, "width": 1080}]}}}}


def html(record=None, *, identity=NOTE):
    state = {"note": {"noteDetailMap": {identity: {"note": record or note()}}}}
    return ("<script>window.__INITIAL_STATE__=" + json.dumps(state) + ";</script>").encode()


class Response(io.BytesIO):
    def __init__(self, url, body, *, status=200, headers=None):
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}


class Opener:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request.full_url)
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def redirect(url, target):
    return urllib.error.HTTPError(url, 302, "Found", {"Location": target}, None)


class IdentityTests(unittest.TestCase):
    def test_nonfinite_or_overflowing_video_measurements_are_refused(self):
        for value in (float("nan"), float("inf"), 10 ** 400, -1):
            item = note("video")
            item["video"]["media"]["stream"]["h264"][0]["duration"] = value
            with self.subTest(value_type=type(value).__name__), self.assertRaises(SourceError):
                xhs.parse_page(html(item), PAGE)

    def test_short_and_signed_long_identity_do_not_persist_tokens(self):
        adapter = adapter_for(SHORT)
        self.assertEqual(adapter.name, "xiaohongshu")
        signed = PAGE + "?xsec_token=secret&xsec_source=pc_share"
        self.assertEqual(adapter.canonicalize_source(signed), PAGE)
        self.assertEqual(adapter.source_id(signed), NOTE)
        self.assertIsNone(adapter.source_id(SHORT))
        self.assertEqual(adapter.canonicalize_source(SHORT + "?tracking=secret"), SHORT)
        resolved = xhs.parse_page(html(), signed)
        self.assertNotIn("secret", json.dumps(resolved.info))
        self.assertNotIn("private", json.dumps(resolved.info))
        self.assertEqual(resolved.info["webpage_url"], PAGE)

    def test_invalid_authority_and_unsupported_collection_routes(self):
        for url in ("https://xhslink.cn.evil.test/o/Abc123", "https://u:p@xhslink.cn/o/Abc123",
                    "https://xhslink.cn:444/o/Abc123", "https://www.xiaohongshu.com/user/profile/123",
                    "https://xhslink.cn/o/a", "https://127.0.0.1/o/Abc123"):
            with self.subTest(url=url), self.assertRaises(SourceError):
                xhs._page_url(url)

    def test_images_keep_order_repeated_pages_and_honest_audio_status(self):
        record = note()
        record["imageList"] = [{"urlDefault": IMAGE}, {"urlDefault": IMAGE.replace("page", "second")},
            {"urlDefault": IMAGE}]
        resolved = xhs.parse_page(html(record), PAGE)
        self.assertEqual(len(resolved.image_urls), 3)
        self.assertEqual(resolved.image_urls[0], resolved.image_urls[2])
        self.assertEqual(resolved.info["_clipmind_audio_status"], "not_acquired")
        self.assertEqual(resolved.urls, ())

    def test_video_is_not_misread_as_cover_images(self):
        resolved = xhs.parse_page(html(note("video")), PAGE)
        self.assertEqual(resolved.urls, (VIDEO,))
        self.assertEqual(resolved.image_urls, ())
        self.assertEqual(resolved.info["duration"], 12)

    def test_recommendation_or_inconsistent_embedded_identity_never_selected(self):
        wrong = note()
        wrong["noteId"] = OTHER
        for payload in (html(wrong), html(identity=OTHER)):
            with self.subTest(payload=payload[:60]), self.assertRaises(SourceError) as caught:
                xhs.parse_page(payload, PAGE)
            self.assertEqual(caught.exception.code, "source_metadata_unavailable")

    def test_unknown_type_live_photo_missing_page_and_untrusted_assets_refused(self):
        records = []
        item = note("live"); records.append(item)
        item = note(); item["imageList"][0]["livePhoto"] = True; records.append(item)
        item = note(); item["imageList"] = []; records.append(item)
        item = note(); item["imageList"][0]["urlDefault"] = "https://xhscdn.com.evil.test/a"; records.append(item)
        for item in records:
            with self.subTest(item=item), self.assertRaises(SourceError):
                xhs.parse_page(html(item), PAGE)

    def test_missing_and_duplicate_state_are_not_generic_page_scraping(self):
        for payload in (b"<video src='https://evil.test/a'></video>", html() + html(),
                        html().replace(b'"note": {', b'"note": {}, "note": {', 1)):
            with self.subTest(payload=payload[:40]), self.assertRaises(SourceError):
                xhs.parse_page(payload, PAGE)


class TransportTests(unittest.TestCase):
    def test_invalid_resource_limits_refused_before_network(self):
        for overrides in ({"timeout": float("nan")}, {"timeout": float("inf")},
                          {"max_bytes": -2}, {"max_bytes": float("inf")},
                          {"max_requests": -1}, {"max_requests": True}):
            with self.subTest(overrides=overrides), self.assertRaises(SourceError):
                xhs._Transport(**({"timeout": 30, "max_bytes": 1024, "max_requests": 8} | overrides))

    def test_streaming_read_checks_cancellation_between_socket_blocks(self):
        stop = threading.Event()
        class SlowResponse(Response):
            def read(self, n=-1):
                raise AssertionError("aggregating read must not be used")
            def read1(self, n=-1):
                stop.set()
                return b"first block"
        opener = Opener([SlowResponse(IMAGE, b"", headers={})])
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener), \
                self.assertRaises(xhs._Stopped):
            xhs._Transport(timeout=10, max_bytes=1024, max_requests=8, stop=stop).read(IMAGE, metadata=False)

    def test_shortlink_signed_resolution_stops_on_login_before_fetching_it(self):
        canonical = PAGE + "?xsec_token=secret"
        opener = Opener([redirect(SHORT, canonical), redirect(canonical,
            "https://www.xiaohongshu.com/login?redirect=secret")])
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener), \
                self.assertRaises(SourceError) as caught:
            xhs.resolve(SHORT)
        self.assertEqual(caught.exception.code, "login_required")
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(caught.exception.usage["network_requests"], 2)
        self.assertNotIn("secret", str(caught.exception))

    def test_redirect_cannot_change_exact_note_or_leave_allowlisted_host(self):
        for target in (PAGE.replace(NOTE, OTHER), "https://127.0.0.1/private",
                       "https://www.xiaohongshu.com.evil.test/explore/" + NOTE):
            opener = Opener([redirect(PAGE, target)])
            with patch.object(xhs.urllib.request, "build_opener", return_value=opener), \
                    self.subTest(target=target), self.assertRaises(SourceError):
                xhs.resolve(PAGE)
            self.assertEqual(len(opener.requests), 1)

    def test_probe_fetches_metadata_only_and_counts_actual_body(self):
        payload = html(note("video"))
        opener = Opener([redirect(SHORT, PAGE), Response(PAGE, payload)])
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener):
            result = asyncio.run(fetch.probe(SHORT, config=Settings(cookie_sources=("chrome",))))
        self.assertEqual(result.status, "reachable")
        self.assertEqual(result.source_id, NOTE)
        self.assertEqual(result.network_requests, 2)
        self.assertEqual(result.network_bytes, len(payload))
        self.assertEqual(opener.requests, [SHORT, PAGE])

    def test_truncated_oversized_compressed_and_partial_responses_fail(self):
        responses = [Response(PAGE, b"abc", headers={"Content-Length": "8"}),
            Response(PAGE, b"abc", headers={"Content-Length": "1"}),
            Response(PAGE, b"abc", headers={"Content-Encoding": "gzip"}),
            Response(PAGE, b"abc", status=206), Response(PAGE, b"", headers={}),
            Response(PAGE, b"abc", headers={"Content-Length": "3", "Content-Range": "bytes 0-2/100"})]
        for response in responses:
            opener = Opener([response])
            with patch.object(xhs.urllib.request, "build_opener", return_value=opener), \
                    self.subTest(response=response), self.assertRaises(SourceError):
                xhs.resolve(PAGE)

    def test_zero_request_budget_and_size_budget_prevent_unbounded_reads(self):
        opener = Opener([])
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener), self.assertRaises(SourceError):
            xhs.resolve(PAGE, max_requests=0)
        self.assertEqual(opener.requests, [])
        response = Response(PAGE, b"x" * 200, headers={})
        # Retain the cursor before context-manager close to measure the read.
        response.close = lambda: None
        opener = Opener([response])
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener), self.assertRaises(SourceError):
            xhs.resolve(PAGE, max_bytes=20)
        self.assertEqual(response.tell(), 21)  # one sentinel byte establishes overflow

    def test_cdn_redirect_guard_blocks_private_endpoint_before_contact(self):
        opener = Opener([redirect(IMAGE, "http://127.0.0.1/secret")])
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener), self.assertRaises(SourceError):
            xhs._Transport(timeout=1, max_bytes=1024, max_requests=8).read(IMAGE, metadata=False)
        self.assertEqual(opener.requests, [IMAGE])


class AcquisitionTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    async def test_downloaded_silent_mp4_is_verified_by_real_ffprobe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "fixture.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=blue:s=96x64:r=2", "-t", "1", "-an", "-c:v", "mpeg4", str(original)],
                check=True, capture_output=True, timeout=20)
            record = note("video")
            record["video"]["media"]["stream"]["h264"][0]["duration"] = 0
            body = original.read_bytes()
            opener = Opener([Response(PAGE, html(record)), Response(VIDEO, body)])
            with patch.object(xhs.urllib.request, "build_opener", return_value=opener):
                result = await fetch.fetch(PAGE, root / "job", config=Settings())
            self.assertEqual(result.media_path.read_bytes(), body)
            self.assertEqual(result.media_type, "video")
            self.assertAlmostEqual(result.duration, 1, places=2)
            self.assertEqual(result.source_id, NOTE)

    async def test_video_transfer_has_progress_and_sanitized_shared_metadata(self):
        body = b"video-container-fixture" * 10000
        opener = Opener([Response(PAGE + "?xsec_token=secret", html(note("video"))), Response(VIDEO, body)])
        ffprobe = {"format": {"duration": "12.25"}, "streams": [{"codec_type": "video"}]}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(xhs.urllib.request, "build_opener", return_value=opener), \
                patch.object(fetch.shutil, "which", return_value="ffprobe"), \
                patch.object(fetch, "_run", new=AsyncMock(return_value=(0, json.dumps(ffprobe), ""))) as run:
            notes = []
            result = await fetch.fetch(PAGE + "?xsec_token=secret", Path(temp), on_note=notes.append,
                config=Settings(cookie_sources=("chrome",)))
            self.assertEqual(result.media_path.read_bytes(), body)
            self.assertEqual(result.media_type, "video")
            self.assertEqual(result.webpage_url, PAGE)
            self.assertEqual(result.duration, 12.25)
            self.assertEqual(run.await_args.args[0][0], "ffprobe")
            self.assertNotIn("private", json.dumps(notes))
            self.assertNotIn("secret", json.dumps(result.info))

    async def test_video_declared_by_page_cannot_succeed_with_audio_or_unknown_duration(self):
        for streams, duration in (([{"codec_type": "audio"}], "12"),
                                  ([{"codec_type": "video"}], "0"),
                                  ([{"codec_type": "video"}], "nan")):
            acquired = fetch.AcquiredMedia(Path("source.mp4"), xhs.parse_page(html(note("video")), PAGE).info)
            payload = {"format": {"duration": duration}, "streams": streams}
            with self.subTest(duration=duration, streams=streams), \
                    patch.object(fetch.shutil, "which", return_value="ffprobe"), \
                    patch.object(fetch, "_run", new=AsyncMock(return_value=(0, json.dumps(payload), ""))), \
                    self.assertRaises(fetch.FetchError):
                await fetch._remote_media_info(acquired)

    async def test_real_png_collection_reaches_shared_asset_contract_in_order(self):
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 24), "white").save(image_bytes, format="PNG")
        body = image_bytes.getvalue()
        record = note()
        record["imageList"] *= 2
        opener = Opener([Response(PAGE, html(record)), Response(IMAGE, body), Response(IMAGE, body)])
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(xhs.urllib.request, "build_opener", return_value=opener), \
                patch.object(fetch, "_run", side_effect=AssertionError("no downloader or cookies")):
            result = await fetch.fetch(PAGE, Path(temp), config=Settings(cookie_sources=("chrome",)))
            self.assertEqual(result.media_type, "image_collection")
            self.assertEqual(len(result.image_paths), 2)
            self.assertEqual(result.image_paths[0].read_bytes(), body)
            self.assertEqual(result.image_paths[1].read_bytes(), body)
            self.assertNotEqual(result.image_paths[0], result.image_paths[1])
            self.assertEqual(result.source_id, NOTE)
            self.assertEqual(result.platform, "xiaohongshu")
            self.assertNotIn("private", json.dumps(result.info))

    async def test_missing_later_page_cleans_downloaded_prefix(self):
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 24), "white").save(image_bytes, format="PNG")
        record = note(); record["imageList"] *= 2
        opener = Opener([Response(PAGE, html(record)), Response(IMAGE, image_bytes.getvalue()),
            urllib.error.HTTPError(IMAGE, 403, "Forbidden", {}, None)])
        with tempfile.TemporaryDirectory() as temp, patch.object(xhs.urllib.request, "build_opener", return_value=opener):
            root = Path(temp)
            with self.assertRaises(SourceError):
                await xhs.acquire(PAGE, root, max_bytes=100000)
            self.assertEqual(list(root.iterdir()), [])

    async def test_cancel_joins_worker_before_temporary_directory_removal(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(url, directory, stop, **kwargs):
            (directory / "partial").write_bytes(b"partial")
            entered.set()
            release.wait(2)
            self.assertTrue(stop.is_set())
            self.assertTrue(directory.is_dir())
            raise xhs._Stopped()
        with tempfile.TemporaryDirectory() as temp, patch.object(xhs, "_acquire", side_effect=blocked):
            root = Path(temp)
            task = asyncio.create_task(xhs.acquire(PAGE, root, max_bytes=100000))
            await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(list(root.iterdir()), [])


class PortableExportTests(unittest.TestCase):
    def test_all_zip_profiles_remove_signed_retry_query_without_changing_original(self):
        from clipmind import compact, handoff
        from tests.pack_fixture import make_complete_pack

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = make_complete_pack(root, source_id=NOTE)
            for image in (pack / "visual_states").rglob("*.jpg"):
                Image.new("RGB", (64, 48), "white").save(image)
            source_path = pack / "source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source.update(platform="xiaohongshu", source_adapter="xiaohongshu", url=PAGE)
            source_path.write_text(json.dumps(source), encoding="utf-8")
            manifest_path = pack / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"].update(platform="xiaohongshu", id=NOTE)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            job_path = pack / "job.json"
            retry_url = PAGE + "?xsec_token=do-not-export-this-signature&xsec_source=pc_share"
            job = {"job": {"id": pack.name, "status": "done", "url": retry_url,
                "options": {"source": retry_url}, "result": {"url": retry_url}}}
            job_path.write_text(json.dumps(job), encoding="utf-8")
            original = job_path.read_bytes()
            paths = [handoff.export_zip(pack, root / "full.zip"),
                compact.export_compact_zip(pack, root / "content.zip"),
                compact.export_compact_zip(pack, root / "text.zip", image_policy="text")]
            for path in paths:
                with self.subTest(profile=path.name), zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    for name in archive.namelist():
                        self.assertNotIn(b"do-not-export-this-signature", archive.read(name), name)
                    exported = json.loads(archive.read(f"{pack.name}/job.json"))
                    self.assertEqual(exported["job"]["url"], PAGE)
                    self.assertEqual(exported["job"]["options"], {})
            self.assertEqual(job_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
