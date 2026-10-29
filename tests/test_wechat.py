from __future__ import annotations

import unittest

from clipmind.sources.wechat import share_id
from clipmind.sources.registry import adapter_for, canonicalize_source
from clipmind.sources.wechat_browser import ResolveFailed, _feed_media, _parse_playable, _safe_media_url


class WechatTests(unittest.TestCase):
    def test_share_link_identity(self):
        short = "https://weixin.qq.com/sph/Awo78WDUPG"
        preview = "https://channels.weixin.qq.com/finder-preview/pages/sph?id=Awo78WDUPG"
        self.assertEqual(share_id(short), "Awo78WDUPG")
        self.assertEqual(share_id(preview), "Awo78WDUPG")
        self.assertEqual(canonicalize_source(preview), short)
        self.assertEqual(adapter_for(short).platform, "wechat")
        self.assertIsNone(share_id("https://weixin.qq.com.evil.invalid/sph/Awo78WDUPG"))

    def test_playable_must_be_official_feed(self):
        self.assertEqual(_parse_playable({"data": {"playable_url":
            "https://channels.weixin.qq.com/finder-preview/pages/feed?token=secret"}}).split("?")[0],
            "https://channels.weixin.qq.com/finder-preview/pages/feed")
        with self.assertRaises(ResolveFailed):
            _parse_playable({"data": {"playable_url": "http://127.0.0.1/feed"}})

    def test_feed_requires_video_and_matching_identity(self):
        source = "https://weixin.qq.com/sph/Awo78WDUPG"
        payload = {"errCode": 0, "data": {"feedInfo": {
            "exportId": "expected", "description": "日内交易真的有用吗？",
            "h264VideoInfo": {"videoUrl": "https://finder.video.qq.com/video.mp4"}},
            "authorInfo": {"nickname": "示例作者"}}}
        result = _feed_media(payload, source, "expected")
        self.assertEqual(result.info["id"], "Awo78WDUPG")
        self.assertEqual(result.info["title"], "日内交易真的有用吗？")
        payload["data"]["feedInfo"]["exportId"] = "other"
        with self.assertRaises(ResolveFailed):
            _feed_media(payload, source, "expected")

    def test_untrusted_media_and_preview_only_fail(self):
        with self.assertRaises(ResolveFailed):
            _safe_media_url("https://qq.com.evil.invalid/video.mp4")
        with self.assertRaises(ResolveFailed):
            _feed_media({"errCode": 0, "data": {"feedInfo": {}}},
                        "https://weixin.qq.com/sph/Awo78WDUPG", "expected")


if __name__ == "__main__":
    unittest.main()
