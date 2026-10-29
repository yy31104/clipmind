from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import cli
from clipmind.config import Settings


class WindowsPortabilityTests(unittest.TestCase):
    def test_runtime_settings_default_and_environment(self) -> None:
        with patch.dict(os.environ, {
            "CLIPMIND_FASTER_WHISPER_DEVICE": "CUDA",
            "CLIPMIND_FASTER_WHISPER_COMPUTE_TYPE": "FLOAT16",
        }):
            configured = Settings.from_env()
        self.assertEqual(configured.faster_whisper_device, "cuda")
        self.assertEqual(configured.faster_whisper_compute_type, "float16")
        self.assertEqual(Settings().faster_whisper_device, "auto")
        self.assertEqual(Settings().faster_whisper_compute_type, "auto")

    def test_basic_doctor_does_not_claim_runtime_verification(self) -> None:
        bundle = SimpleNamespace(
            transcript=SimpleNamespace(name="faster-whisper", available=lambda: True),
            text=SimpleNamespace(name="tesseract", available=lambda: True),
        )
        with (
            patch.object(cli.Settings, "from_env", return_value=Settings()),
            patch.object(cli, "default_providers", return_value=bundle),
            patch.object(cli.shutil, "which", return_value="installed"),
            patch.object(cli, "_deep_asr_smoke") as smoke,
        ):
            report = cli.doctor()
        smoke.assert_not_called()
        self.assertEqual(report["asr"]["check_scope"], "import_only")
        self.assertEqual(report["asr"]["runtime_test"], "not_run")
        self.assertEqual(report["asr"]["model"], "large-v3-turbo")

    def test_deep_doctor_reports_cuda_failure_and_action(self) -> None:
        bundle = SimpleNamespace(
            transcript=SimpleNamespace(name="faster-whisper", available=lambda: True),
            text=SimpleNamespace(name="tesseract", available=lambda: True),
        )
        config = Settings(faster_whisper_device="cuda", faster_whisper_compute_type="float16")
        with (
            patch.object(cli.Settings, "from_env", return_value=config),
            patch.object(cli, "default_providers", return_value=bundle),
            patch.object(cli.shutil, "which", return_value="installed"),
            patch.object(cli, "_deep_asr_smoke", return_value={
                "passed": False, "reason": "RuntimeError: Library cublas64_12.dll is not found"
            }),
        ):
            report = cli.doctor(deep=True)
        self.assertFalse(report["checks"]["asr"])
        self.assertEqual(report["asr"]["runtime_test"], "failed")
        self.assertEqual(report["asr"]["device"], "cuda")
        self.assertIn("CLIPMIND_FASTER_WHISPER_DEVICE=cpu", report["asr"]["runtime_action"])

    def test_windows_launcher_binds_loopback(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "scripts" / "clipmind_windows.ps1")
        source = script.read_text(encoding="utf-8")
        self.assertIn("& $clipmind serve --host 127.0.0.1 --port 8420", source)
        self.assertIn("$scripts = Join-Path $repo '.venv\\Scripts'", source)


if __name__ == "__main__":
    unittest.main()
