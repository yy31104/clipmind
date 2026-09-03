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
        self.assertEqual(bundle.diarization.name, "none")

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

    def test_unknown_provider_names_are_not_silently_treated_as_auto(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported ASR provider"):
            default_providers(Settings(asr_provider="remote-magic"))
        with self.assertRaisesRegex(ValueError, "Unsupported OCR provider"):
            default_providers(Settings(ocr_provider="remote-magic"))


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

    async def test_default_diarizer_never_invents_speaker_labels(self) -> None:
        transcript = asr.Transcript([asr.Segment(0, 1, "hello")])
        provider = providers.NoSpeakerDiarizer()

        result = await provider.assign(Path("audio.wav"), transcript)

        self.assertIs(result, transcript)
        self.assertFalse(result.has_speakers)

    def test_word_timestamps_are_normalized_at_the_provider_boundary(self) -> None:
        words = asr._mapping_words(
            {
                "words": [
                    {"start": 0.1, "end": 0.6, "word": " hello", "probability": 0.9}
                ]
            }
        )

        self.assertEqual(words[0].text, "hello")
        self.assertEqual((words[0].start, words[0].end), (0.1, 0.6))

    def test_diarization_assigns_the_speaker_with_the_greatest_overlap(self) -> None:
        transcript = asr.Transcript(
            [
                asr.Segment(0.0, 2.0, "first"),
                asr.Segment(2.0, 4.0, "second"),
            ]
        )

        assigned = providers.assign_speakers(
            transcript,
            [(0.0, 1.5, "SPEAKER_00"), (1.5, 4.0, "SPEAKER_01")],
        )

        self.assertEqual(
            [segment.speaker for segment in assigned.segments],
            ["SPEAKER_00", "SPEAKER_01"],
        )
        self.assertTrue(assigned.has_speakers)


if __name__ == "__main__":
    unittest.main()
