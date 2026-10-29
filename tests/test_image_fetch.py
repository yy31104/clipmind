"""Offline acquisition wiring contracts for image posts and local media."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from clipmind import acquisition, fetch, pipeline
from clipmind.config import Settings
from clipmind.sources import douyin_browser, douyin_images, douyin_public
from clipmind.sources.douyin import ADAPTER


WANTED = "7812345678901234567"
OTHER = "7812345678901234568"
SOURCE = f"https://www.douyin.com/note/{WANTED}"
CDN = "https://p3-pc-sign.douyinpic.com/"


def detail(identity=WANTED):
    return {
        "aweme_id": identity, "aweme_type": 68, "desc": "Synthetic document pages",
        "images": [
            {"url_list": [CDN + identity + "-one.webp", CDN + identity + "-one-mirror.webp"]},
            {"url_list": [CDN + identity + "-two.webp"]},
        ],
    }


def resolved_images():
    return douyin_browser._media_info({"aweme_detail": detail()}, SOURCE, WANTED)


def public_missing():
    try:
        douyin_public.media_info({"aweme_list": [detail(OTHER)]}, SOURCE, WANTED)
    except douyin_browser.ResolveFailed as exc:
        return exc
    raise AssertionError("A recommended post must never satisfy the requested ID")


class ImageFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.downloaded = []

    def tearDown(self):
        self.temp.cleanup()

    async def download_images(self, groups, directory, **kwargs):
        self.downloaded.append((groups, directory, kwargs))
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(len(groups)):
            path = directory / f"image-{index + 1}.png"
            Image.new("RGB", (32, 24), (index * 100, 20, 80)).save(path)
            paths.append(path)
        return tuple(paths)

    async def test_public_image_post_downloads_ordered_pages_and_preserves_asset_contract(self):
        resolved = douyin_public.media_info(
            {"aweme_list": [detail(OTHER), detail()]}, SOURCE, WANTED)
        config = Settings(cookie_sources=(), max_upload_mb=5)
        workdir = self.root / "public"
        with (
            patch.object(douyin_public, "resolve", new=AsyncMock(return_value=resolved)) as resolve,
            patch.object(douyin_images, "download_images", side_effect=self.download_images) as download,
            patch.object(douyin_browser, "download", new=AsyncMock()) as video_download,
            patch.object(douyin_browser, "resolve", new=AsyncMock()) as browser_resolve,
        ):
            item = await fetch.AcquisitionEngine().acquire(SOURCE, workdir, ADAPTER, config=config)
        resolve.assert_awaited_once_with(SOURCE)
        download.assert_awaited_once()
        browser_resolve.assert_not_awaited()
        video_download.assert_not_awaited()
        groups, directory, options = self.downloaded[0]
        self.assertEqual(groups, resolved.image_urls)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]), 2)  # Mirrors do not become extra pages.
        self.assertEqual(directory, acquisition.workspace(workdir).resolve())
        self.assertEqual(options["max_total_bytes"], 5 * 1024 * 1024)
        self.assertEqual(item.source_id, WANTED)
        self.assertEqual(item.media_type, "image_collection")
        self.assertEqual(item.duration, 0)
        self.assertEqual(item.image_paths, tuple(directory / f"image-{i}.png" for i in (1, 2)))
        self.assertEqual(item.media_path, item.image_paths[0])
        self.assertTrue(all(path.is_file() for path in item.image_paths))

    async def test_browser_rung_image_urls_bypass_video_download(self):
        resolved = resolved_images()
        directory = self.root / "browser"
        config = Settings(douyin_browser_timeout=17)
        with (
            patch.object(douyin_browser, "resolve", new=AsyncMock(return_value=resolved)) as resolve,
            patch.object(douyin_images, "download_images", side_effect=self.download_images),
            patch.object(douyin_browser, "download", new=AsyncMock()) as video_download,
        ):
            acquired, failure = await fetch.DouyinBrowserRung().acquire(SOURCE, directory, config)
        resolve.assert_awaited_once_with(SOURCE, timeout=17)
        video_download.assert_not_awaited()
        self.assertIsNone(failure)
        self.assertEqual(acquired.info["id"], WANTED)
        self.assertEqual(len(acquired.image_paths), 2)
        self.assertEqual(acquired.path, acquired.image_paths[0])
        self.assertEqual(self.downloaded[0][0], resolved.image_urls)

    async def test_missing_public_target_falls_back_to_exact_browser_post(self):
        resolved = resolved_images()
        notes = []
        config = Settings(cookie_sources=(), douyin_browser_enabled=True)
        with (
            patch.object(douyin_public, "resolve", new=AsyncMock(side_effect=public_missing())) as public,
            patch.object(douyin_browser, "resolve", new=AsyncMock(return_value=resolved)) as browser,
            patch.object(douyin_images, "download_images", side_effect=self.download_images),
            patch.object(douyin_browser, "download", new=AsyncMock()) as video_download,
        ):
            item = await fetch.AcquisitionEngine().acquire(
                SOURCE, self.root / "fallback", ADAPTER, notes.append, config=config)
        public.assert_awaited_once_with(SOURCE)
        browser.assert_awaited_once_with(SOURCE, timeout=config.douyin_browser_timeout)
        self.assertEqual(notes, ["trying public feed", "trying browser session"])
        self.assertEqual(item.source_id, WANTED)
        self.assertEqual(item.info["_clipmind_strategy"], "browser session")
        self.assertEqual(len(item.image_paths), 2)
        self.assertEqual(len(self.downloaded), 1)
        self.assertTrue(all(OTHER not in url for group in self.downloaded[0][0] for url in group))
        video_download.assert_not_awaited()

    async def test_browser_disabled_explains_recovery_without_relabeling_cookies(self):
        config = Settings(cookie_sources=(), douyin_browser_enabled=False)
        with (
            patch.object(douyin_public, "resolve", new=AsyncMock(side_effect=public_missing())),
            patch.object(douyin_browser, "resolve", new=AsyncMock()) as browser,
            patch.object(douyin_images, "download_images", new=AsyncMock()) as images,
        ):
            with self.assertRaises(fetch.FetchError) as raised:
                await fetch.AcquisitionEngine().acquire(
                    SOURCE, self.root / "disabled", ADAPTER, config=config)
        error = raised.exception
        self.assertEqual(error.code, "source_metadata_unavailable")
        self.assertEqual(error.details["browser_fallback"], "disabled")
        self.assertEqual(error.details["metadata_attempts"][0]["reason"], "target_missing")
        self.assertIn("Chrome", error.action)
        self.assertIn("图片", error.action)
        self.assertIn("无需反复登录或导出 Cookie", error.action)
        browser.assert_not_awaited()
        images.assert_not_awaited()

    async def test_browser_missing_second_page_overrides_earlier_metadata_and_cookie_failures(self):
        workdir = self.root / "missing-second-page"
        resolved = resolved_images()
        config = Settings(cookie_sources=("chrome",), douyin_browser_enabled=True)
        events = []
        page_attempts = []

        async def incomplete_collection(groups, directory, **kwargs):
            self.assertEqual(groups, resolved.image_urls)
            first = directory / "downloaded-first-page.png"
            Image.new("RGB", (32, 24), "red").save(first)
            page_attempts.extend([1, 2])
            # A downloader diagnostic must never enter public details/action.
            raise douyin_images.ImagePostError(
                CDN + "second-page.webp?token=synthetic-private-diagnostic"
            )

        cookie_failure = fetch.AttemptFailure(
            "chrome", "chrome cookies", "failed to decrypt Chrome cookies"
        )
        with (
            patch.object(fetch, "_engine", fetch.AcquisitionEngine()),
            patch.object(fetch.shutil, "which", return_value="yt-dlp"),
            patch.object(douyin_public, "resolve", new=AsyncMock(side_effect=public_missing())),
            patch.object(fetch.CookieRung, "acquire", new=AsyncMock(return_value=(None, cookie_failure))) as cookies,
            patch.object(douyin_browser, "resolve", new=AsyncMock(return_value=resolved)) as browser,
            patch.object(douyin_images, "download_images", side_effect=incomplete_collection),
            patch.object(douyin_browser, "download", new=AsyncMock()) as video_download,
        ):
            with self.assertRaises(fetch.FetchError) as raised:
                await pipeline.process(
                    SOURCE, workdir, pipeline.Pools(), lambda *args: events.append(args),
                    config=config, providers=object(),
                )
        error = raised.exception
        self.assertEqual(error.code, "image_collection_fetch_failed")
        self.assertEqual(error.details, {"source_resolved": True, "media_type": "image_collection"})
        self.assertIn("原图", error.action)
        self.assertIn("未使用封面或推荐内容", error.action)
        self.assertNotIn("请保留原链接等待", error.action)
        public_error = str(error) + error.action + json.dumps(error.details)
        self.assertNotIn("synthetic-private-diagnostic", public_error)
        self.assertNotIn("p3-pc-sign.douyinpic.com", public_error)
        self.assertNotIn("cookies_unavailable", public_error)
        cookies.assert_awaited_once()
        browser.assert_awaited_once_with(SOURCE, timeout=config.douyin_browser_timeout)
        video_download.assert_not_awaited()
        self.assertEqual(page_attempts, [1, 2])
        self.assertFalse(any(event[0] == "done" for event in events))
        self.assertFalse((workdir / "manifest.json").exists())
        self.assertEqual(acquisition.leftovers(workdir), [])

    async def test_image_collection_download_budget_failure_never_becomes_partial_asset(self):
        with (
            patch.object(douyin_public, "resolve", new=AsyncMock(return_value=resolved_images())),
            patch.object(douyin_images, "download_images", new=AsyncMock(
                side_effect=douyin_images.ImagePostBudgetExceeded("synthetic byte limit"))),
            patch.object(douyin_browser, "download", new=AsyncMock()) as video_download,
        ):
            with self.assertRaises(fetch.FetchError) as raised:
                await fetch.DouyinPublicRung().acquire(SOURCE, self.root, Settings())
        self.assertEqual(raised.exception.code, "image_collection_limit_exceeded")
        video_download.assert_not_awaited()
        self.assertFalse((self.root / "manifest.json").exists())

    async def test_local_corrupt_still_preserves_original_and_cleans_owned_copy(self):
        original = self.root / "user-original.png"
        contents = b"This synthetic fixture is not a decodable PNG."
        original.write_bytes(contents)
        workdir = self.root / "corrupt-job"
        with self.assertRaises(fetch.FetchError) as raised:
            await pipeline.process(
                str(original), workdir, pipeline.Pools(), lambda *args: None,
                config=Settings(), providers=object(),
            )
        self.assertEqual(raised.exception.code, "image_decode_failed")
        self.assertEqual(original.read_bytes(), contents)
        self.assertFalse((workdir / "manifest.json").exists())
        self.assertEqual(acquisition.leftovers(workdir), [])

    async def test_audio_only_probe_uses_stream_types_instead_of_filename(self):
        for streams, expected in (
            ([{"codec_type": "audio"}], "audio"),
            ([{"codec_type": "audio"}, {"codec_type": "video"}], None),
            ([{"codec_type": "video"}], None),
        ):
            with (
                self.subTest(streams=streams),
                patch.object(fetch.shutil, "which", return_value="ffprobe"),
                patch.object(fetch, "_run", new=AsyncMock(return_value=(0, json.dumps({
                    "format": {"duration": "1.25"}, "streams": streams,
                }), ""))) as run,
            ):
                info = await fetch._probe_local(self.root / "misleading.mp4")
            self.assertEqual(info["duration"], 1.25)
            self.assertEqual(info.get("_clipmind_media_type"), expected)
            self.assertIn("format=duration:stream=codec_type:stream_disposition=attached_pic", run.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
