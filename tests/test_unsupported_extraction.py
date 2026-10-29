"""Extractor capability failures are not evidence of a missing source.

All probes below use a mocked worker. No source or media is requested.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from clipmind import fetch
from clipmind.config import Settings
from clipmind.sources import SourceError


class UnsupportedExtractionTests(unittest.TestCase):
    def test_unsupported_url_is_a_capability_failure(self):
        for diagnostic in (
            "ERROR: Unsupported URL: https://example.invalid/article/123",
            "ERROR: UNSUPPORTED URL (extractor not available for this URL)",
        ):
            with self.subTest(diagnostic=diagnostic):
                error = fetch._fetch_error([diagnostic])
                self.assertEqual(error.code, "extractor_unsupported")

    def test_capability_message_does_not_assert_expiry_or_recommend_fresh_cookies(self):
        error = fetch._fetch_error(["ERROR: Unsupported URL: C:/private/source"])
        self.assertIn("does not support", error.user_message)
        self.assertIn("does not establish", error.action)
        self.assertNotIn("expired", error.user_message.lower())
        self.assertNotIn("fresh share link", error.action.lower())
        self.assertNotIn("cookie", error.action.lower())
        self.assertNotIn("C:/private", str(error))
        self.assertNotIn("C:/private", error.action)

    def test_actual_unavailability_remains_a_distinct_failure(self):
        for diagnostic in ("ERROR: Video not available", "ERROR: Video removed"):
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(fetch._fetch_error([diagnostic]).code, "link_unavailable")


class UnsupportedProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_explicit_local_paths_remain_unavailable(self):
        with TemporaryDirectory() as temp:
            missing = str(Path(temp) / "absent.mp4")
            for source in (missing, "file://" + missing):
                with self.subTest(source=source), patch("clipmind.fetch._run_budgeted", new=AsyncMock()) as worker:
                    result = await fetch.probe(source, config=Settings(cookie_sources=("-",)))
                worker.assert_not_called()
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.failure_code, "local_file_unavailable")

    async def test_unsupported_note_and_article_probes_report_unknown(self):
        envelope = json.dumps({
            "metadata": None,
            "error": "ERROR: Unsupported URL",
            "network_bytes": 0,
            "network_requests": 0,
        })
        for kind in ("note", "article"):
            with self.subTest(kind=kind), \
                 patch.object(fetch, "_engine", fetch.AcquisitionEngine()), \
                 patch("clipmind.fetch.shutil.which", return_value="mock-yt-dlp"), \
                 patch("clipmind.fetch._run_budgeted", new=AsyncMock(return_value=(0, envelope, ""))), \
                 patch.object(fetch.AcquisitionEngine, "acquire", side_effect=AssertionError("must not acquire")):
                result = await fetch.probe(
                    f"https://www.douyin.com/{kind}/123",
                    config=Settings(cookie_sources=("-",)),
                )
                self.assertEqual(result.status, "unknown")
                self.assertEqual(result.failure_code, "extractor_unsupported")
                self.assertEqual((result.network_bytes, result.network_requests), (0, 0))
                self.assertIn("could not determine", result.user_message)

    async def test_unrecognized_input_is_not_a_verdict_on_reachability(self):
        with patch("clipmind.fetch.adapter_for", side_effect=SourceError(
            "unsupported_source", "not recognized", "use a supported input"
        )), patch("clipmind.fetch._run_budgeted", new=AsyncMock()) as worker:
            result = await fetch.probe("unrecognized-input", config=Settings(cookie_sources=("-",)))
        worker.assert_not_called()
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.failure_code, "unsupported_source")


if __name__ == "__main__":
    unittest.main()
