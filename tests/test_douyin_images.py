"""Synthetic image-post metadata and transfers; no browser/site/network used."""
import asyncio
import io
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from clipmind.sources import MediaAsset, douyin_browser, douyin_images

WANTED = "7812345678901234567"
OTHER = "7812345678901234568"
PAGE = "https://www.douyin.com/note/" + WANTED
CDN = "https://p3-pc-sign.douyinpic.com/"


def detail(identity=WANTED):
    return {
        "aweme_id": identity, "aweme_type": 68, "desc": "Image lesson",
        "author": {"nickname": "Example"},
        "images": [
            {"url_list": [CDN + "page-one.webp", CDN + "page-one-mirror.webp"]},
            {"url_list": [CDN + "page-two.webp"]},
        ],
    }


def png(color):
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(stream, format="PNG")
    return stream.getvalue()


class MetadataTests(unittest.TestCase):
    def test_exact_id_image_post_keeps_page_order_and_mirror_groups(self):
        media = douyin_browser._media_info({"aweme_detail": detail()}, PAGE, WANTED)
        self.assertFalse(media.urls)
        self.assertEqual(media.image_urls, (
            (CDN + "page-one.webp", CDN + "page-one-mirror.webp"),
            (CDN + "page-two.webp",),
        ))
        self.assertEqual(media.info["_clipmind_media_type"], "image_collection")
        self.assertEqual(media.info["duration"], 0)

    def test_mobile_image_post_shape_is_not_an_avatar_or_cover_scan(self):
        entry = detail()
        del entry["images"]
        entry["image_post_info"] = {"images": [
            {"display_image": {"url_list": [CDN + "first.webp"]}},
            {"display_image": {"url_list": [CDN + "second.webp"]}},
        ]}
        entry["author"]["avatar_larger"] = {"url_list": [CDN + "avatar.webp"]}
        groups = douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED).image_urls
        self.assertEqual(groups, ((CDN + "first.webp",), (CDN + "second.webp",)))

    def test_recommendation_never_supplies_the_requested_pages(self):
        with self.assertRaises(douyin_browser.ResolveFailed):
            douyin_browser._media_info({"aweme_detail": detail(OTHER)}, PAGE, WANTED)

    def test_image_post_music_is_not_misreported_as_transcribed_or_silent(self):
        entry = detail()
        entry["music"] = {"play_url": {"url_list": [CDN + "song.mp3"]}}
        # Some image posts also expose a rendered preview. It is not the
        # requested set of original image pages.
        entry["video"] = {"play_addr": {"url_list": ["https://cdn.douyinvod.com/preview.mp4"]}}
        media = douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)
        self.assertEqual(len(media.image_urls), 2)
        self.assertFalse(media.urls)
        self.assertEqual(media.info["_clipmind_image_audio"], "not_acquired")

    def test_regular_video_with_unrelated_images_keeps_playable_video(self):
        entry = detail()
        entry["aweme_type"] = 0
        entry["video"] = {"play_addr": {"url_list": ["https://cdn.douyinvod.com/actual.mp4"]}}
        media = douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)
        self.assertFalse(media.image_urls)
        self.assertTrue(media.urls)

    def test_a_missing_page_or_malformed_metadata_rejects_the_collection(self):
        for images in (None, [], [{}], [None], [{"url_list": []}], [{"url_list": "not-a-list"}]):
            entry = detail()
            entry["images"] = images
            with self.subTest(images=images), self.assertRaises(douyin_images.ImagePostError):
                douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)

    def test_untrusted_addresses_are_not_a_retry_or_cover_fallback(self):
        for url in (
            "http://p3.douyinpic.com/a", "https://127.0.0.1/a", "https://douyinpic.com.evil.invalid/a",
            "https://douyinpic.com@evil.invalid/a", "https://user:secret@p3.douyinpic.com/a",
            "file:///a", "data:image/png,abc", "https://p3.douyinpic.com:8443/a",
            "https://p3.douyinpic.com/a\nHeader: injected",
        ):
            entry = detail()
            entry["images"][1]["url_list"] = [url]
            with self.subTest(url=url), self.assertRaises(douyin_images.ImagePostError):
                douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)

    def test_exact_repeated_pages_are_preserved_not_deduplicated_as_mirrors(self):
        entry = detail()
        entry["images"][1] = entry["images"][0]
        media = douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)
        self.assertEqual(len(media.image_urls), 2)
        self.assertEqual(media.image_urls[0], media.image_urls[1])

    def test_detail_and_public_feed_keep_motion_separate_from_live_photo_stills(self):
        from clipmind.sources import douyin_public
        entry = detail()
        entry["images"][1]["video"] = {
            "play_addr": {"url_list": ["https://cdn.douyinvod.com/motion.mp4"]}
        }
        expected = ((), ("https://cdn.douyinvod.com/motion.mp4",))
        media = douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)
        public = douyin_public.media_info({"aweme_list": [entry]}, PAGE, WANTED)
        self.assertEqual(media.image_motion_urls, expected)
        self.assertEqual(public.image_motion_urls, expected)
        self.assertEqual(len(media.image_urls), 2)

    def test_empty_optional_motion_metadata_does_not_reject_static_pages(self):
        entry = detail()
        entry["images"][0]["video"] = None
        entry["images"][1]["video"] = {}
        media = douyin_browser._media_info({"aweme_detail": entry}, PAGE, WANTED)
        self.assertEqual(len(media.image_urls), 2)

    def test_asset_addition_keeps_legacy_constructor_and_order(self):
        legacy = MediaAsset(video_path=Path("old.mp4"), info={"id": "old"})
        pages = MediaAsset(image_paths=[Path("b.png"), Path("a.png")])
        self.assertEqual(legacy.video_path, Path("old.mp4"))
        self.assertEqual(legacy.image_paths, ())
        self.assertEqual(pages.media_path, Path("b.png"))
        self.assertEqual(pages.image_paths, (Path("b.png"), Path("a.png")))
        self.assertEqual(pages.media_type, "image_collection")


class _Response(io.BytesIO):
    def __init__(self, body, url, headers=None, status=200):
        super().__init__(body)
        self.url = url
        self.headers = headers or {}
        self.status = status

    def read1(self, size=-1):
        return self.read(size)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def opener(self, bodies, attempts):
        def open(request, timeout):
            url = request.full_url
            attempts.append(url)
            value = bodies[url]
            if isinstance(value, Exception):
                raise value
            if isinstance(value, tuple):
                return _Response(*value)
            return _Response(value, url)
        return types.SimpleNamespace(open=open)

    async def test_ordered_pages_are_validated_from_bytes_not_url_extensions(self):
        attempts = []
        groups = ((CDN + "one.webp", CDN + "mirror.webp"), (CDN + "two.webp",))
        bodies = {CDN + "one.webp": OSError("offline"),
                  CDN + "mirror.webp": png("red"), CDN + "two.webp": png("blue")}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_images.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            paths = await douyin_images.download_images(groups, Path(folder))
            self.assertEqual([path.suffix for path in paths], [".png", ".png"])
            self.assertEqual([path.read_bytes() for path in paths], [png("red"), png("blue")])
            self.assertEqual(attempts, [CDN + "one.webp", CDN + "mirror.webp", CDN + "two.webp"])

    async def test_failed_second_page_never_returns_partial_collection_and_cleans_files(self):
        attempts = []
        groups = ((CDN + "one",), (CDN + "two",))
        bodies = {CDN + "one": png("red"), CDN + "two": b"<html>not media</html>"}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_images.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            with self.assertRaises(douyin_images.ImagePostError):
                await douyin_images.download_images(groups, Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_declared_byte_limit_rejects_before_body_read(self):
        attempts = []
        url = CDN + "one"
        bodies = {url: (png("red"), url, {"Content-Length": "500"})}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_images.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            with self.assertRaises(douyin_images.ImagePostBudgetExceeded):
                await douyin_images.download_images(((url,),), Path(folder), max_total_bytes=10)
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_partial_or_length_mismatched_image_never_counts_as_a_complete_page(self):
        body, url = png("red"), CDN + "one"
        for status, headers in (
            (206, {}), (200, {"Content-Range": "bytes 0-99/999"}),
            (200, {"Content-Length": str(len(body) + 1)}),
            (200, {"Content-Length": str(len(body) - 1)}),
        ):
            attempts = []
            with self.subTest(status=status, headers=headers), tempfile.TemporaryDirectory() as folder, patch.object(
                douyin_images.urllib.request, "build_opener",
                return_value=self.opener({url: (body, url, headers, status)}, attempts),
            ):
                with self.assertRaises(douyin_images.ImagePostError):
                    await douyin_images.download_images(((url,),), Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_available_blocks_allow_deadline_checks_before_aggregate_reads(self):
        clock = types.SimpleNamespace(now=0.0)
        class SlowResponse(_Response):
            def read(self, size=-1):
                raise AssertionError("aggregate read must not hide the deadline")
            def read1(self, size=-1):
                clock.now += 1.0
                return b"part"
        response = SlowResponse(b"", CDN + "one")
        opener = types.SimpleNamespace(open=lambda *args, **kwargs: response)
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_images.urllib.request, "build_opener", return_value=opener
        ), patch.object(douyin_images, "time", types.SimpleNamespace(monotonic=lambda: clock.now)):
            with self.assertRaises(douyin_images.ImagePostBudgetExceeded):
                await douyin_images.download_images(((CDN + "one",),), Path(folder), timeout=.5)
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_failed_mirror_bytes_still_count_against_budget(self):
        attempts = []
        url, mirror = CDN + "one", CDN + "mirror"
        bodies = {url: b"not an image", mirror: png("blue")}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_images.urllib.request, "build_opener", return_value=self.opener(bodies, attempts)
        ):
            with self.assertRaises(douyin_images.ImagePostBudgetExceeded):
                await douyin_images.download_images(((url, mirror),), Path(folder),
                                                   max_total_bytes=len(bodies[mirror]))
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_untrusted_redirect_response_and_non_images_are_rejected(self):
        for body in ((png("red"), "https://127.0.0.1/secret", {}),
                     (png("red"), CDN + "one", {"Content-Encoding": "gzip"}),
                     (b"", CDN + "one", {})):
            attempts = []
            with self.subTest(body=body), tempfile.TemporaryDirectory() as folder, patch.object(
                douyin_images.urllib.request, "build_opener",
                return_value=self.opener({CDN + "one": body}, attempts),
            ):
                with self.assertRaises(douyin_images.ImagePostError):
                    await douyin_images.download_images(((CDN + "one",),), Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_count_limit_never_downloads_a_truncated_prefix(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_images, "_download_collection"
        ) as worker:
            with self.assertRaises(douyin_images.ImagePostBudgetExceeded):
                await douyin_images.download_images(((CDN + "one",), (CDN + "two",)),
                                                   Path(folder), max_images=1)
            worker.assert_not_called()

    async def test_decoded_pixel_limit_stops_complete_collection(self):
        attempts = []
        with tempfile.TemporaryDirectory() as folder, patch.object(
            douyin_images.urllib.request, "build_opener",
            return_value=self.opener({CDN + "one": png("red")}, attempts),
        ):
            with self.assertRaises(douyin_images.ImagePostBudgetExceeded):
                await douyin_images.download_images(((CDN + "one",),), Path(folder), max_image_pixels=10)
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_repeated_cancellation_waits_for_writer_before_cleanup(self):
        started, exited = threading.Event(), threading.Event()
        def writer(groups, directory, stop, **kwargs):
            started.set()
            try:
                stop.wait(5)
                (directory / "last-write.part").write_bytes(b"stopped")
            finally:
                exited.set()
            raise douyin_images._Stopped()
        with tempfile.TemporaryDirectory() as folder, patch.object(douyin_images, "_download_collection", writer):
            task = asyncio.create_task(douyin_images.download_images(((CDN + "one",),), Path(folder)))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(exited.is_set())
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_redirect_guard_refuses_before_following_untrusted_target(self):
        with self.assertRaises(douyin_images.ImagePostError):
            douyin_images._ImageRedirects().redirect_request(
                urllib_request(), None, 302, "Found", {}, "https://127.0.0.1/secret"
            )


def urllib_request():
    import urllib.request
    return urllib.request.Request(CDN + "one")


class BrowserWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_out_browser_captures_image_metadata_for_exact_requested_note(self):
        import sys
        from tests.test_douyin_browser import _Clock, browser_substitute
        entries = [{"aweme_detail": detail(OTHER)}, {"aweme_detail": detail()}]
        with patch.dict(sys.modules, browser_substitute(PAGE, entries)), \
             patch.object(douyin_browser, "time", _Clock()):
            media = await douyin_browser.resolve(PAGE, timeout=10)
        self.assertEqual(media.info["id"], WANTED)
        self.assertEqual(len(media.image_urls), 2)

    async def resolve_entries(self, entries):
        import sys
        from tests.test_douyin_browser import _Clock, browser_substitute
        with patch.dict(sys.modules, browser_substitute(PAGE, entries)), \
             patch.object(douyin_browser, "time", _Clock()):
            return await douyin_browser.resolve(PAGE, timeout=10)

    def oversized(self, identity=WANTED):
        entry = detail(identity)
        entry["images"] = [{"url_list": [CDN + "page.webp"]}] * 1001
        return {"aweme_detail": entry}

    async def test_exact_target_metadata_count_limit_survives_browser_listener(self):
        with self.assertRaises(douyin_images.ImagePostBudgetExceeded):
            await self.resolve_entries([self.oversized()])

    async def test_an_oversized_recommendation_cannot_refuse_requested_item(self):
        media = await self.resolve_entries([self.oversized(OTHER), {"aweme_detail": detail()}])
        self.assertEqual(media.info["id"], WANTED)
        self.assertEqual(len(media.image_urls), 2)

    async def test_available_exact_media_takes_precedence_over_prior_limit_metadata(self):
        media = await self.resolve_entries([self.oversized(), {"aweme_detail": detail()}])
        self.assertEqual(len(media.image_urls), 2)

    async def test_only_oversized_recommendation_still_means_target_is_missing(self):
        with self.assertRaises(douyin_browser.ResolveFailed) as raised:
            await self.resolve_entries([self.oversized(OTHER)])
        self.assertNotIsInstance(raised.exception, douyin_images.ImagePostBudgetExceeded)
        self.assertIn(WANTED, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
