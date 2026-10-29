"""Missing stream inspection must not turn an audio file into a video."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from clipmind import cli, fetch
from clipmind.config import Settings


class DependencyDoctorTests(unittest.TestCase):
    def test_doctor_is_not_ready_when_ffmpeg_exists_but_ffprobe_is_missing(self):
        providers = SimpleNamespace(
            transcript=SimpleNamespace(name="faster-whisper", available=lambda: True),
            text=SimpleNamespace(name="tesseract", available=lambda: True),
        )
        with (
            patch.object(cli.Settings, "from_env", return_value=Settings()),
            patch.object(cli, "default_providers", return_value=providers),
            patch.object(cli.shutil, "which", side_effect=lambda name: None if name == "ffprobe" else "available"),
            patch.object(cli.os, "access", return_value=True),
        ):
            report = cli.doctor()
        self.assertTrue(report["checks"]["ffmpeg"])
        self.assertFalse(report["checks"]["ffprobe"])
        self.assertFalse(report["ready"])
        self.assertTrue(all(value for key, value in report["checks"].items() if key != "ffprobe"))


class DependencyFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_ffprobe_reports_dependency_before_running_a_subprocess(self):
        with (
            patch.object(fetch.shutil, "which", return_value=None),
            patch.object(fetch, "_run", new=AsyncMock()) as run,
        ):
            with self.assertRaises(fetch.FetchError) as raised:
                await fetch._probe_local(Path("speech.wav"))
        run.assert_not_awaited()
        self.assertEqual(raised.exception.code, "missing_dependency")
        self.assertIn("ffprobe", raised.exception.user_message)
        self.assertIn("complete FFmpeg", raised.exception.action)
        self.assertIn("PATH", raised.exception.action)

    async def test_static_image_fetch_does_not_require_ffprobe_or_change_the_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "diagram.png"
            Image.new("RGB", (24, 16), "blue").save(original)
            contents = original.read_bytes()
            with (
                patch.object(fetch.shutil, "which", return_value=None),
                patch.object(fetch, "_run", new=AsyncMock()) as run,
                patch.object(fetch, "_probe_local", new=AsyncMock()) as probe,
            ):
                asset = await fetch.fetch(str(original), root / "job", config=Settings())
            run.assert_not_awaited()
            probe.assert_not_awaited()
            self.assertEqual(asset.media_type, "image_collection")
            self.assertEqual(len(asset.image_paths), 1)
            self.assertNotEqual(asset.image_paths[0], original)
            self.assertEqual(asset.image_paths[0].read_bytes(), contents)
            self.assertEqual(original.read_bytes(), contents)

    async def test_available_ffprobe_failures_keep_the_existing_metadata_fallback(self):
        for result in ((1, "", "unreadable stream"), (0, "{invalid", "")):
            with (
                self.subTest(result=result),
                patch.object(fetch.shutil, "which", return_value="ffprobe"),
                patch.object(fetch, "_run", new=AsyncMock(return_value=result)) as run,
            ):
                metadata = await fetch._probe_local(Path("legacy.mov"))
            run.assert_awaited_once()
            self.assertEqual(metadata, {"duration": 0.0})


if __name__ == "__main__":
    unittest.main()
