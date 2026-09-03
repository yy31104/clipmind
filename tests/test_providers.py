from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind import asr, ocr, providers
from clipmind.config import Settings
from clipmind.providers import default_providers


class ProviderSelectionTests(unittest.TestCase):
    def test_auto_prefers_native_apple_providers_when_available(self) -> None:
        with (
            patch.object(providers.sys, "platform", "darwin"),
            patch.object(providers.platform, "machine", return_value="arm64"),
        ):
            bundle = default_providers(Settings())

        self.assertEqual(bundle.transcript.name, "mlx-whisper")
        self.assertEqual(bundle.text.name, "apple-vision")

    def test_auto_selects_portable_boundaries_off_apple(self) -> None:
        with (
            patch.object(providers.sys, "platform", "linux"),
            patch.object(providers.platform, "machine", return_value="x86_64"),
        ):
            bundle = default_providers(Settings())

        self.assertEqual(bundle.transcript.name, "faster-whisper")
        self.assertEqual(bundle.text.name, "tesseract")

    def test_explicit_provider_choice_does_not_depend_on_probe_order(self) -> None:
        configured = Settings(asr_provider="faster-whisper", ocr_provider="tesseract")
        with (
            patch.object(providers.sys, "platform", "darwin"),
            patch.object(providers.platform, "machine", return_value="arm64"),
        ):
            bundle = default_providers(configured)

        self.assertEqual(bundle.transcript.name, "faster-whisper")
        self.assertEqual(bundle.text.name, "tesseract")


class PortableProviderDegradationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_faster_whisper_degrades_without_import_failure(self) -> None:
        provider = asr.FasterWhisperProvider(Settings())
        with patch.object(asr, "faster_whisper_available", return_value=False):
            transcript = await provider.transcribe(Path("audio.wav"))

        self.assertEqual(transcript.engine, "faster-whisper")
        self.assertIn("unavailable", transcript.error)

    def test_missing_tesseract_is_reported_at_the_provider_boundary(self) -> None:
        provider = ocr.TesseractTextRecognizer(Settings())
        with patch.object(ocr, "tesseract_available", return_value=False):
            with self.assertRaisesRegex(ocr.OCRError, "Tesseract unavailable"):
                provider.read_text(Path("frame.jpg"))


if __name__ == "__main__":
    unittest.main()
