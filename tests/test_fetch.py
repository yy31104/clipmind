from __future__ import annotations

import unittest
from unittest.mock import patch

from clipmind import fetch
from clipmind.config import Settings


class FetchErrorTests(unittest.TestCase):
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
        error = fetch._fetch_error(["ERROR: Unsupported URL"])

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


if __name__ == "__main__":
    unittest.main()
