"""Synthetic ordered Live Photo metadata/downloads; no site or credentials."""
import asyncio
import copy
import io
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind.sources import MediaAsset, douyin_browser, douyin_motion
from clipmind.sources.douyin_images import ImagePostBudgetExceeded, ImagePostError
from clipmind.sources.douyin_embedded import embedded_media_info
from tests.test_douyin_embedded import PAGE, WANTED, OTHER, element, html, props

CDN = "https://v3-dy-o.zjcdn.com/"
IMAGE = "https://p3-pc-sign.douyinpic.com/"


def raw_detail():
    return {"aweme_id": WANTED, "aweme_type": 68, "images": [
        {"url_list": [IMAGE + "first.webp"]},
        {"url_list": [IMAGE + "second.webp"], "clip_type": 5, "live_photo_type": 1,
         "video": {"duration": 2648, "play_addr": {
             "url_list": [CDN + "second.mp4", "https://v5-dy-ov-experiment.zjcdn.com/second.mp4"],
             "data_size": 300_000,
         }}},
        {"url_list": [IMAGE + "third.webp"]},
    ]}


def pace_props():
    data = props()
    data["aweme"]["detail"]["images"] = [
        {"urlList": [IMAGE + f"page-{index}.webp"], "clipType": 5, "livePhotoType": 1,
         "video": {"duration": 2648 + index, "dataSize": 300_000 + index,
                   "playAddr": [{"src": CDN + f"page-{index}.mp4"},
                                {"src": "https://v5-dy-ov-experiment.zjcdn.com/" + f"page-{index}.mp4"}]}}
        for index in range(5)
    ]
    return data


class MotionMetadataTests(unittest.TestCase):
    def test_raw_metadata_preserves_static_slots_and_own_motion_mirrors(self):
        entry = raw_detail()
        before = copy.deepcopy(entry)
        media = douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)
        self.assertEqual(media.image_motion_urls, ((),
            (CDN + "second.mp4", "https://v5-dy-ov-experiment.zjcdn.com/second.mp4"), ()))
        self.assertEqual(len(media.image_urls), 3)
        self.assertEqual(media.info["_clipmind_image_motion_count"], 1)
        self.assertEqual(entry, before)

    def test_embedded_five_live_photo_pages_keep_all_five_stills_and_motions(self):
        data = pace_props()
        before = copy.deepcopy(data)
        media = embedded_media_info(html(element(data)), PAGE, WANTED)
        self.assertEqual(len(media.image_urls), 5)
        self.assertEqual(len(media.image_motion_urls), 5)
        self.assertTrue(all(len(group) == 2 for group in media.image_motion_urls))
        self.assertEqual([group[0] for group in media.image_motion_urls],
                         [CDN + f"page-{index}.mp4" for index in range(5)])
        self.assertEqual(media.info["duration"], 0)
        self.assertEqual(media.info["_clipmind_image_audio"], "not_acquired")
        self.assertEqual(data, before)

    def test_embedded_mixed_pages_keep_static_empty_motion_slot(self):
        data = pace_props()
        data["aweme"]["detail"]["images"][1].update(video=None, clipType=0, livePhotoType=0)
        media = embedded_media_info(html(element(data)), PAGE, WANTED)
        self.assertEqual(media.image_motion_urls[1], ())
        self.assertEqual(len(media.image_motion_urls), 5)

    def test_recommended_live_photo_cannot_replace_the_requested_target(self):
        data = pace_props()
        data["awemeId"] = data["aweme"]["detail"]["awemeId"] = OTHER
        with self.assertRaises(douyin_browser.ResolveFailed):
            embedded_media_info(html(element(data)), PAGE, WANTED)

    def test_declared_live_page_without_motion_does_not_return_its_cover(self):
        for value in (None, {}, "$undefined"):
            entry = raw_detail()
            entry["images"][1]["video"] = value
            with self.subTest(value=value), self.assertRaises(ImagePostError):
                douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)

    def test_cover_and_play_api_are_not_motion_addresses(self):
        entry = raw_detail()
        entry["images"][1]["video"] = {"cover": {"url_list": [IMAGE + "cover.webp"]},
                                          "play_api": CDN + "maybe.mp4"}
        with self.assertRaises(ImagePostError):
            douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)

    def test_empty_optional_video_metadata_is_a_static_page(self):
        entry = raw_detail()
        entry["images"][0]["video"] = {}
        self.assertEqual(douyin_motion.motion_url_groups(entry)[0], ())

    def test_malformed_or_untrusted_motion_never_drops_only_the_bad_page(self):
        for value in (None, [], [CDN + "second.mp4"] * 17, ["http://v3-dy-o.zjcdn.com/a"],
                      ["https://127.0.0.1/private"], ["https://zjcdn.com.evil.invalid/a"],
                      ["https://user:secret@v3-dy-o.zjcdn.com/a"], ["https://v3-dy-o.zjcdn.com:8443/a"],
                      [IMAGE + "cover.webp"], [CDN + "a\nHeader: injected"]):
            entry = raw_detail()
            entry["images"][1]["video"]["play_addr"]["url_list"] = value
            with self.subTest(value=value), self.assertRaises(ImagePostError):
                douyin_motion.motion_url_groups(entry)

    def test_invalid_duration_and_size_hints_are_not_fabricated(self):
        for value in (-1, float("nan"), "not a number", True):
            for field in ("duration", "data_size"):
                entry = raw_detail()
                video = entry["images"][1]["video"]
                (video if field == "duration" else video["play_addr"])[field] = value
                with self.subTest(value=value, field=field), self.assertRaises(ImagePostError):
                    douyin_motion.motion_url_groups(entry)

    def test_embedded_motion_requires_the_measured_src_object_shape(self):
        for addresses in (CDN + "second.mp4", [CDN + "second.mp4"], [{}], [], [{"src": None}]):
            data = pace_props()
            data["aweme"]["detail"]["images"][1]["video"]["playAddr"] = addresses
            with self.subTest(addresses=addresses), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(html(element(data)), PAGE, WANTED)

    def test_asset_motion_paths_keep_image_page_order_and_legacy_default(self):
        images = [Path("first.png"), Path("second.png"), Path("third.png")]
        motions = [None, Path("second.mp4"), None]
        asset = MediaAsset(image_paths=images, image_motion_paths=motions)
        self.assertEqual(asset.image_motion_paths, tuple(motions))
        self.assertEqual(asset.media_path, images[0])
        self.assertEqual(MediaAsset(video_path=Path("video.mp4")).image_motion_paths, ())
        with self.assertRaises(ValueError):
            MediaAsset(image_paths=images, image_motion_paths=[Path("wrong.mp4")])
        with self.assertRaises(ValueError):
            MediaAsset(video_path=Path("video.mp4"), image_motion_paths=[Path("wrong.mp4")])


class _Response(io.BytesIO):
    def __init__(self, body, url, headers=None, status=200):
        super().__init__(body)
        self.url = url
        self.headers = headers or {}
        self.status = status

    def read1(self, size=-1):
        return self.read(size)


class MotionDownloadTests(unittest.IsolatedAsyncioTestCase):
    def opener(self, bodies, attempts):
        def open(request, timeout):
            url = request.full_url
            attempts.append(url)
            value = bodies[url]
            if isinstance(value, Exception):
                raise value
            if isinstance(value, _Response):
                return value
            return _Response(value, url)
        return types.SimpleNamespace(open=open)

    async def test_download_keeps_static_none_slots_and_motion_order(self):
        attempts = []
        groups = ((), (CDN + "one.mp4",), (), (CDN + "two.mp4",))
        bodies = {CDN + "one.mp4": b"first clip bytes", CDN + "two.mp4": b"second clip bytes"}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_motion.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            paths = await douyin_motion.download_motions(groups, Path(folder))
            self.assertEqual(paths[0], None)
            self.assertEqual(paths[2], None)
            self.assertEqual(paths[1].read_bytes(), bodies[CDN + "one.mp4"])
            self.assertEqual(paths[3].read_bytes(), bodies[CDN + "two.mp4"])
            self.assertEqual([paths[1].name, paths[3].name], ["motion-page-0002.mp4", "motion-page-0004.mp4"])
            self.assertEqual(attempts, [CDN + "one.mp4", CDN + "two.mp4"])

    async def test_all_static_pages_create_no_transfer_or_temporary_directory(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(douyin_motion, "_download_collection") as worker:
            self.assertEqual(await douyin_motion.download_motions(((), (), ()), Path(folder)), (None, None, None))
            worker.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_a_failed_mirror_uses_only_that_pages_next_named_mirror(self):
        attempts = []
        bodies = {CDN + "bad.mp4": OSError("refused"), CDN + "good.mp4": b"clip bytes"}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_motion.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            paths = await douyin_motion.download_motions(((CDN + "bad.mp4", CDN + "good.mp4"),), Path(folder))
            self.assertEqual(paths[0].read_bytes(), b"clip bytes")
            self.assertEqual(attempts, [CDN + "bad.mp4", CDN + "good.mp4"])

    async def test_missing_later_motion_rejects_whole_attempt_without_leaving_first_clip(self):
        attempts = []
        bodies = {CDN + "one.mp4": b"clip bytes", CDN + "two.mp4": OSError("https://private.invalid/?token=secret")}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_motion.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            with self.assertRaises(ImagePostError) as raised:
                await douyin_motion.download_motions(((CDN + "one.mp4",), (CDN + "two.mp4",)), Path(folder))
            self.assertIn("page 2", str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_declared_and_streamed_bytes_obey_the_same_total_limit(self):
        for headers in ({"Content-Length": "100"}, {}):
            attempts = []
            body = _Response(b"123456", CDN + "one.mp4", headers)
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as folder, patch.object(
                douyin_motion.urllib.request, "build_opener", return_value=self.opener({CDN + "one.mp4": body}, attempts)
            ):
                with self.assertRaises(ImagePostBudgetExceeded):
                    await douyin_motion.download_motions(((CDN + "one.mp4",),), Path(folder), max_total_bytes=5)
                self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_bytes_of_a_partly_failed_mirror_still_count(self):
        class Partial(_Response):
            reads = 0
            def read(self, size=-1):
                self.reads += 1
                if self.reads == 1:
                    return b"12345"
                raise OSError("closed midway")
        attempts = []
        bodies = {CDN + "bad.mp4": Partial(b"", CDN + "bad.mp4"), CDN + "good.mp4": b"abcdef"}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_motion.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            with self.assertRaises(ImagePostBudgetExceeded):
                await douyin_motion.download_motions(((CDN + "bad.mp4", CDN + "good.mp4"),), Path(folder), max_total_bytes=6)
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_empty_compressed_or_untrusted_responses_are_not_successful_clips(self):
        for body, url, headers in ((b"", CDN + "one.mp4", {}),
                                   (b"clip", CDN + "one.mp4", {"Content-Encoding": "gzip"}),
                                   (b"clip", "https://127.0.0.1/private", {})):
            attempts = []
            response = _Response(body, url, headers)
            with self.subTest(url=url, headers=headers), tempfile.TemporaryDirectory() as folder, patch.object(
                douyin_motion.urllib.request, "build_opener", return_value=self.opener({CDN + "one.mp4": response}, attempts)
            ):
                with self.assertRaises(ImagePostError):
                    await douyin_motion.download_motions(((CDN + "one.mp4",),), Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_partial_or_length_mismatched_motion_never_counts_as_complete(self):
        url, payload = CDN + "one.mp4", b"decodable prefix is not the whole response"
        for status, headers in (
            (206, {}), (200, {"Content-Range": "bytes 0-99/999"}),
            (200, {"Content-Length": str(len(payload) + 1)}),
            (200, {"Content-Length": str(len(payload) - 1)}),
        ):
            attempts = []
            response = _Response(payload, url, headers, status)
            with self.subTest(status=status, headers=headers), tempfile.TemporaryDirectory() as folder, patch.object(
                douyin_motion.urllib.request, "build_opener", return_value=self.opener({url: response}, attempts)
            ):
                with self.assertRaises(ImagePostError):
                    await douyin_motion.download_motions(((url,),), Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_available_blocks_allow_deadline_checks_before_aggregate_reads(self):
        clock = types.SimpleNamespace(now=0.0)
        class SlowResponse(_Response):
            def read(self, size=-1):
                raise AssertionError("aggregate read must not hide the deadline")
            def read1(self, size=-1):
                clock.now += 1.0
                return b"part"
        response = SlowResponse(b"", CDN + "one.mp4")
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_motion.urllib.request, "build_opener", return_value=self.opener({CDN + "one.mp4": response}, [])
        ), patch.object(douyin_motion, "time", types.SimpleNamespace(monotonic=lambda: clock.now)):
            with self.assertRaises(ImagePostBudgetExceeded):
                await douyin_motion.download_motions(((CDN + "one.mp4",),), Path(folder), timeout=.5)
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_collection_timeout_is_a_limit_error_and_cleans_the_attempt(self):
        now = iter((0.0, 1.0))
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_motion, "time", types.SimpleNamespace(monotonic=lambda: next(now))
        ), patch.object(douyin_motion.urllib.request, "build_opener") as opener:
            with self.assertRaises(ImagePostBudgetExceeded):
                await douyin_motion.download_motions(((CDN + "one.mp4",),), Path(folder), timeout=0.1)
            opener.return_value.open.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_repeated_cancellation_waits_for_the_writer_and_removes_its_last_write(self):
        started, exited = threading.Event(), threading.Event()
        def writer(groups, directory, stop, **kwargs):
            started.set()
            try:
                stop.wait(5)
                (directory / "last-write.part").write_bytes(b"stopped")
            finally:
                exited.set()
            raise douyin_motion._Stopped()
        with tempfile.TemporaryDirectory() as folder, patch.object(douyin_motion, "_download_collection", writer):
            task = asyncio.create_task(douyin_motion.download_motions(((CDN + "one.mp4",),), Path(folder)))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(exited.is_set())
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_untrusted_groups_and_excess_page_count_are_refused_before_worker(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(douyin_motion, "_download_collection") as worker:
            for groups in ((("https://127.0.0.1/a",),), ((CDN + "one.mp4",) * 17,)):
                with self.subTest(groups=groups), self.assertRaises(ImagePostError):
                    await douyin_motion.download_motions(groups, Path(folder))
            with self.assertRaises(ImagePostBudgetExceeded):
                await douyin_motion.download_motions(((),) * 1001, Path(folder))
            worker.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_redirect_guard_refuses_unsafe_target_before_following(self):
        import urllib.request
        with self.assertRaises(ImagePostError):
            douyin_motion._MotionRedirects().redirect_request(
                urllib.request.Request(CDN + "one.mp4"), None, 302, "Found", {}, "https://127.0.0.1/private"
            )


if __name__ == "__main__":
    unittest.main()
