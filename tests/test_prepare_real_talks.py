from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from scripts import prepare_real_talks as prepare


class PrepareRealTalkTests(unittest.TestCase):
    def test_server_ignoring_range_is_rejected_before_body_is_read(self) -> None:
        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                raise AssertionError("Must not consume a full-body fallback")

        with patch.object(prepare.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(ValueError, "refusing full-download fallback"):
                prepare.verify_http_seeking("https://example.test/talk.mp4")

    def test_invalid_download_never_becomes_a_published_clip(self) -> None:
        case = {"sample_start_seconds": 60, "sample_end_seconds": 240,
                "duration_seconds": 600, "video_url": "https://example.test/talk.mp4"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "input.mp4"

            def fake_run(command, _timeout):
                Path(command[-1]).write_bytes(b"truncated media")

            with patch.object(prepare, "verify_http_seeking", return_value={}), \
                    patch.object(prepare, "run", side_effect=fake_run), \
                    patch.object(prepare, "validate_clip", side_effect=ValueError("bad duration")):
                with self.assertRaisesRegex(ValueError, "bad duration"):
                    prepare.download_clip(case, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_failed_preparation_does_not_publish_a_partial_case(self) -> None:
        case = {"case_id": "case-1", "guid": "guid", "metadata_url": "meta", "captions_url": "vtt",
                "license_proof_text": "CC proof", "video_url": "media"}
        publisher = {"guid": "guid", "description": "CC proof", "recordings": [{"recording_url": "media"}]}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(prepare, "fetch", side_effect=[json.dumps(publisher).encode(), b"WEBVTT\n"]), \
                    patch.object(prepare, "download_clip", side_effect=TimeoutError("download stopped")):
                with self.assertRaises(TimeoutError):
                    prepare.prepare_case(case, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_contact_sheets_are_uniform_source_frames_not_selected_outputs(self) -> None:
        def fake_ffmpeg(command, _timeout):
            pattern = command[-1]
            for index in range(1, 181):
                Image.new("RGB", (240, 136), (index, 0, 0)).save(pattern % index)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(prepare, "run", side_effect=fake_ffmpeg) as command:
                sheets = prepare.make_contact_sheets(root / "input.mp4", root, 930)
            self.assertEqual(len(sheets), 3)
            arguments = command.call_args.args[0]
            self.assertIn("fps=1:start_time=0", arguments[arguments.index("-vf") + 1])
            for name in sheets:
                with Image.open(root / name) as sheet:
                    self.assertEqual(sheet.size, (1440, 1560))
            self.assertFalse(any(p.name.startswith(".source-frames-") for p in root.iterdir()))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_real_ffmpeg_handles_yuv420_source_contact_dimensions(self) -> None:
        # The source is tiny but really encoded as yuv420p. An odd pad height
        # failed in ffmpeg even though mocked frame generation passed.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clip = root / "input.mp4"
            prepare.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "color=c=blue:s=128x72:r=1:d=180", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-threads", "1", str(clip),
            ], 30)
            sheets = prepare.make_contact_sheets(clip, root, 930)
            self.assertEqual(len(sheets), 3)
            for name in sheets:
                with Image.open(root / name) as image:
                    self.assertEqual(image.size, (1440, 1560))

    def test_subprocess_failure_retains_actionable_stderr(self) -> None:
        failure = subprocess.CalledProcessError(234, ["ffmpeg"], stderr=b"Padded dimensions cannot be smaller")
        with patch.object(prepare.subprocess, "run", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "Padded dimensions cannot be smaller"):
                prepare.run(["ffmpeg"], 30)


if __name__ == "__main__":
    unittest.main()
