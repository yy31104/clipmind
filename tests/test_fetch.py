from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from clipmind import fetch
from clipmind.config import Settings


class FetchErrorTests(unittest.TestCase):
    def test_wechat_channels_share_uses_dedicated_browser(self) -> None:
        import asyncio
        from clipmind.sources import wechat_browser

        with TemporaryDirectory() as temp:
            root = Path(temp) / "owned"
            for url in (
                "https://weixin.qq.com/sph/Awo78WDUPG",
                "https://channels.weixin.qq.com/finder-preview/pages/sph?id=Awo78WDUPG",
            ):
                async def fake_acquire(source, destination, **kwargs):
                    destination.write_bytes(b"video")
                    return wechat_browser.ResolvedMedia("https://example.invalid/media", {
                        "id": "Awo78WDUPG", "title": "示例", "webpage_url": source
                    })

                with self.subTest(url=url), patch.object(wechat_browser, "acquire", fake_acquire):
                    result = asyncio.run(fetch.fetch(url, root))
                    self.assertEqual(result.platform, "wechat")
                    self.assertEqual(result.source_id, "Awo78WDUPG")
                    self.assertEqual(result.info["_clipmind_strategy"], "wechat browser session")
                    self.assertEqual(result.media_path.read_bytes(), b"video")

    def test_wechat_failure_does_not_claim_chrome_cookies_are_unreadable(self) -> None:
        import asyncio
        from clipmind.sources import wechat_browser

        async def missing_login(*args, **kwargs):
            raise wechat_browser.ResolveFailed("请先在腾讯元宝官方网页扫码登录")

        with TemporaryDirectory() as temp, patch.object(wechat_browser, "acquire", missing_login):
            with self.assertRaises(fetch.FetchError) as raised:
                asyncio.run(fetch.fetch("https://weixin.qq.com/sph/Awo78WDUPG", Path(temp)))
            self.assertEqual(raised.exception.code, "media_fetch_failed")
            self.assertIn("扫码登录", raised.exception.user_message)
            self.assertNotIn("Cookie", raised.exception.user_message)

    def test_fresh_cookie_failure_is_safe_and_actionable(self) -> None:
        error = fetch._fetch_error(
            [
                "chrome cookies: ERROR: Fresh cookies are needed",
                "safari cookies: Operation not permitted: /Users/private/Cookies",
            ]
        )

        self.assertEqual(error.code, "cookies_stale")
        self.assertIn("Chrome", error.action)
        self.assertNotIn("/Users/", str(error))
        self.assertNotIn("Safari", str(error))

    def test_expired_link_has_a_fresh_link_recovery_action(self) -> None:
        error = fetch._fetch_error(["ERROR: This video is not available"])

        self.assertEqual(error.code, "link_unavailable")
        self.assertIn("fresh share link", error.action)

    def test_failure_categories_remain_distinct(self) -> None:
        cases = {
            "ERROR: This is a private video": "private_video",
            "ERROR: login required": "login_required",
            "ERROR: Operation not permitted while reading cookies": "cookies_unavailable",
            "ERROR: connection reset": "media_fetch_failed",
        }
        for diagnostic, code in cases.items():
            with self.subTest(code=code):
                self.assertEqual(fetch._fetch_error([diagnostic]).code, code)

    def test_the_user_message_never_carries_the_raw_diagnostic(self) -> None:
        error = fetch._fetch_error(
            ["ERROR: /Users/private/Library/Cookies could not be opened"]
        )

        self.assertNotIn("/Users/", str(error))
        self.assertNotIn("/Users/", error.action)

    def test_safari_is_not_a_default_cookie_source(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            configured = Settings.from_env()

        self.assertEqual(configured.cookie_sources, ("chrome", "-"))

    def test_settings_are_rebuilt_from_the_current_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CLIPMIND_SAMPLE_FPS": "3.5",
                "CLIPMIND_OCR_LANGUAGES": "en-US, ja-JP",
            },
            clear=True,
        ):
            first = Settings.from_env()
        with patch.dict(
            "os.environ",
            {"CLIPMIND_SAMPLE_FPS": "1.25"},
            clear=True,
        ):
            second = Settings.from_env()

        self.assertEqual(first.sample_fps, 3.5)
        self.assertEqual(first.ocr_languages, ("en-US", "ja-JP"))
        self.assertEqual(second.sample_fps, 1.25)
        self.assertNotEqual(first.sample_fps, second.sample_fps)

    def test_invalid_numeric_environment_values_fall_back_safely(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CLIPMIND_SAMPLE_FPS": "not-a-number",
                "CLIPMIND_MAX_VIDEOS": "not-an-integer",
            },
            clear=True,
        ):
            configured = Settings.from_env()

        self.assertEqual(configured.sample_fps, 2.0)
        self.assertEqual(configured.max_videos, 4)

    def test_provider_choices_are_runtime_configuration(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CLIPMIND_ASR_PROVIDER": "FASTER-WHISPER",
                "CLIPMIND_OCR_PROVIDER": "TESSERACT",
            },
            clear=True,
        ):
            configured = Settings.from_env()

        self.assertEqual(configured.asr_provider, "faster-whisper")
        self.assertEqual(configured.ocr_provider, "tesseract")


def failure(reason: str, *, label: str = "chrome cookies", strategy: str = "chrome"):
    return fetch.AttemptFailure(strategy=strategy, label=label, reason=reason)


class FailureClassificationTests(unittest.TestCase):
    """Which attempt explains the failure, across all of them."""

    def test_the_explaining_rung_wins_even_from_outside_a_fixed_window(self) -> None:
        failures = [
            failure("ERROR: This is a private video", label="chrome cookies"),
            failure("ERROR: connection reset", label="firefox cookies"),
            failure("ERROR: connection reset", label="edge cookies"),
            failure("ERROR: connection reset", label="brave cookies"),
            failure("ERROR: connection reset", label="no cookies"),
            failure("ERROR: connection reset", label="cookie file"),
        ]

        # The one rung that explains anything is the first of six. A window over
        # the last four would never see it.
        self.assertEqual(
            fetch.classify_failures(failures, platform="douyin").code, "private_video"
        )

    def test_a_platform_permission_error_is_not_read_as_a_cookie_problem(self) -> None:
        # Every browser rung is labelled "<browser> cookies", so a classifier
        # reading the label would call this a cookie failure.
        result = fetch.classify_failures(
            [failure("ERROR: [douyin] Permission denied by the uploader")],
            platform="douyin",
        )

        self.assertNotEqual(result.code, "cookies_unavailable")
        self.assertEqual(result.code, "media_fetch_failed")

    def test_a_permission_error_about_cookies_still_classifies(self) -> None:
        self.assertEqual(
            fetch.classify_failures(
                [failure("Operation not permitted while reading cookies")]
            ).code,
            "cookies_unavailable",
        )

    def test_an_adapter_may_classify_a_failure_the_shared_rules_miss(self) -> None:
        class Adapter:
            name = "example"

            def classify_failure(self, attempt):
                if "geo restricted" in attempt.reason.lower():
                    return "link_unavailable"
                return None

        result = fetch.classify_failures(
            [failure("ERROR: geo restricted in your region")],
            adapter=Adapter(),
            platform="example",
        )

        self.assertEqual(result.code, "link_unavailable")

    def test_an_adapter_without_the_hook_keeps_the_shared_rules(self) -> None:
        class Adapter:
            name = "legacy"

        self.assertEqual(
            fetch.classify_failures(
                [failure("ERROR: This is a private video")],
                adapter=Adapter(),
                platform="legacy",
            ).code,
            "private_video",
        )

    def test_an_adapter_hook_that_raises_does_not_break_classification(self) -> None:
        class Adapter:
            name = "broken"

            def classify_failure(self, attempt):
                raise RuntimeError("plugin defect")

        with self.assertLogs("clipmind.fetch", level="ERROR"):
            result = fetch.classify_failures(
                [failure("ERROR: This is a private video")],
                adapter=Adapter(),
                platform="broken",
            )

        self.assertEqual(result.code, "private_video")

    def test_an_adapter_returning_an_unknown_code_falls_back(self) -> None:
        class Adapter:
            name = "inventive"

            def classify_failure(self, attempt):
                return "totally_made_up"

        with self.assertLogs("clipmind.fetch", level="WARNING"):
            result = fetch.classify_failures(
                [failure("ERROR: This is a private video")],
                adapter=Adapter(),
                platform="inventive",
            )

        self.assertEqual(result.code, "private_video")

    def test_a_hook_returning_a_non_string_is_ignored_not_fatal(self) -> None:
        # "Unknown values are ignored" has to hold for values that are not even
        # hashable, or the contract fails on the way to checking it.
        for value in (["private_video"], {"code": "x"}, 7, object()):
            with self.subTest(value=type(value).__name__):

                class Adapter:
                    name = "inventive"

                    def classify_failure(self, attempt, _value=value):
                        return _value

                with self.assertLogs("clipmind.fetch", level="WARNING"):
                    result = fetch.classify_failures(
                        [failure("ERROR: This is a private video")],
                        adapter=Adapter(),
                        platform="inventive",
                    )

                self.assertEqual(result.code, "private_video")

    def test_no_attempt_that_explains_anything_yields_the_shared_fallback(self) -> None:
        self.assertEqual(
            fetch.classify_failures([failure("ERROR: connection reset")]).code,
            "media_fetch_failed",
        )


if __name__ == "__main__":
    unittest.main()
