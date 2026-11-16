from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from clipmind import ocr


class VisionHealthBoundaryTests(unittest.TestCase):
    def setUp(self):
        ocr._close_vision_worker()
        parent_native = patch.object(
            ocr, "read_text", side_effect=AssertionError("health check bypassed the worker")
        )
        parent_native.start()
        self.addCleanup(parent_native.stop)

    def tearDown(self):
        ocr._close_vision_worker()

    def test_health_check_deadline_reaps_child_and_removes_probe_image(self):
        command = [
            sys.executable, "-u", "-c",
            "import sys,time; sys.stdin.readline(); time.sleep(60)",
        ]
        processes = []
        popen = subprocess.Popen
        named_file = tempfile.NamedTemporaryFile

        def start(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as folder, patch.object(
            ocr, "_vision_worker_command", return_value=command
        ), patch.object(ocr, "_VISION_REQUEST_TIMEOUT", 0.1), patch.object(
            ocr.subprocess, "Popen", side_effect=start
        ), patch.object(
            ocr.tempfile, "NamedTemporaryFile",
            side_effect=lambda **kwargs: named_file(dir=folder, **kwargs),
        ):
            started = time.monotonic()
            available, reason = ocr._smoke_test()
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse(available)
            self.assertIn("deadline", reason)
            self.assertEqual(list(Path(folder).iterdir()), [])

        self.assertEqual(len(processes), 1)
        process = processes[0]
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)
        self.assertIsNone(ocr._worker_process)
        self.assertIsNone(ocr._worker_reader)
        self.assertIsNone(ocr._worker_error_reader)

    def test_failed_health_check_removes_probe_image(self):
        named_file = tempfile.NamedTemporaryFile
        with tempfile.TemporaryDirectory() as folder, patch.object(
            ocr, "_recognize_in_worker", side_effect=ocr.OCRError("controlled failure")
        ) as recognize, patch.object(
            ocr.tempfile, "NamedTemporaryFile", side_effect=lambda **kwargs: named_file(dir=folder, **kwargs)
        ):
            self.assertEqual(ocr._smoke_test(), (False, "OCRError: controlled failure"))
            self.assertEqual(list(Path(folder).iterdir()), [])
            image = recognize.call_args.args[0]
            self.assertEqual(image.suffix, ".png")
            self.assertEqual(recognize.call_args.kwargs["languages"], ocr.settings.ocr_languages)

    def test_successful_health_check_removes_probe_image(self):
        named_file = tempfile.NamedTemporaryFile
        with tempfile.TemporaryDirectory() as folder, patch.object(
            ocr, "_recognize_in_worker", return_value=ocr.OCRResult(())
        ), patch.object(
            ocr.tempfile, "NamedTemporaryFile", side_effect=lambda **kwargs: named_file(dir=folder, **kwargs)
        ):
            self.assertEqual(ocr._smoke_test(), (True, None))
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
