"""Hallucination guards reach both Whisper providers and are recorded in packs.

Both providers are replaced by stub modules that record their keyword
arguments, so no model is downloaded or run.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from clipmind import asr
from clipmind.config import Settings


class SettingsFromEnvTests(unittest.TestCase):
    def test_guards_default_on(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLIPMIND_ASR_VAD_FILTER", None)
            os.environ.pop("CLIPMIND_ASR_HALLUCINATION_SILENCE_THRESHOLD", None)
            config = Settings.from_env()
        self.assertTrue(config.asr_vad_filter)
        self.assertEqual(config.asr_hallucination_silence_threshold, 2.0)

    def test_guards_can_be_turned_off(self) -> None:
        env = {
            "CLIPMIND_ASR_VAD_FILTER": "0",
            "CLIPMIND_ASR_HALLUCINATION_SILENCE_THRESHOLD": "off",
        }
        with patch.dict(os.environ, env):
            config = Settings.from_env()
        self.assertFalse(config.asr_vad_filter)
        self.assertIsNone(config.asr_hallucination_silence_threshold)

    def test_threshold_accepts_seconds_and_rejects_non_positive(self) -> None:
        with patch.dict(os.environ, {"CLIPMIND_ASR_HALLUCINATION_SILENCE_THRESHOLD": "3.5"}):
            self.assertEqual(Settings.from_env().asr_hallucination_silence_threshold, 3.5)
        with patch.dict(os.environ, {"CLIPMIND_ASR_HALLUCINATION_SILENCE_THRESHOLD": "0"}):
            self.assertIsNone(Settings.from_env().asr_hallucination_silence_threshold)


class _RecordingFasterWhisper:
    calls: list[dict] = []

    class WhisperModel:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def transcribe(self, audio, **kwargs):
            _RecordingFasterWhisper.calls.append(kwargs)
            return iter(()), types.SimpleNamespace(language="en")


class ProviderWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        _RecordingFasterWhisper.calls = []
        asr._faster_models.clear()
        self.audio = Path(tempfile.mkdtemp()) / "audio.wav"
        self.audio.write_bytes(b"")

    def tearDown(self) -> None:
        asr._faster_models.clear()

    def _faster(self, config: Settings) -> dict:
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = _RecordingFasterWhisper.WhisperModel
        with patch.dict(sys.modules, {"faster_whisper": module}):
            asr.FasterWhisperProvider(config)._transcribe_sync(self.audio)
        return _RecordingFasterWhisper.calls[-1]

    def test_faster_whisper_receives_vad_and_silence_threshold(self) -> None:
        kwargs = self._faster(Settings())
        self.assertTrue(kwargs["vad_filter"])
        self.assertEqual(kwargs["hallucination_silence_threshold"], 2.0)
        self.assertTrue(kwargs["word_timestamps"])
        self.assertFalse(kwargs["condition_on_previous_text"])

    def test_faster_whisper_guards_follow_settings(self) -> None:
        config = replace(Settings(), asr_vad_filter=False, asr_hallucination_silence_threshold=None)
        kwargs = self._faster(config)
        self.assertFalse(kwargs["vad_filter"])
        self.assertIsNone(kwargs["hallucination_silence_threshold"])

    def _mlx(self, config: Settings, *, legacy: bool = False) -> list[dict]:
        calls: list[dict] = []

        def transcribe(audio, **kwargs):
            if legacy and "word_timestamps" in kwargs:
                raise TypeError("unexpected keyword argument 'word_timestamps'")
            calls.append(kwargs)
            return {"segments": [], "language": "en"}

        module = types.ModuleType("mlx_whisper")
        module.transcribe = transcribe
        with patch.dict(sys.modules, {"mlx_whisper": module}):
            asr._transcribe_sync(self.audio, config)
        return calls

    def test_mlx_receives_silence_threshold_with_word_timestamps(self) -> None:
        kwargs = self._mlx(Settings())[-1]
        self.assertTrue(kwargs["word_timestamps"])
        self.assertEqual(kwargs["hallucination_silence_threshold"], 2.0)

    def test_mlx_omits_threshold_when_disabled(self) -> None:
        kwargs = self._mlx(replace(Settings(), asr_hallucination_silence_threshold=None))[-1]
        self.assertNotIn("hallucination_silence_threshold", kwargs)

    def test_older_mlx_without_word_timestamps_still_transcribes(self) -> None:
        kwargs = self._mlx(Settings(), legacy=True)[-1]
        self.assertNotIn("word_timestamps", kwargs)
        self.assertNotIn("hallucination_silence_threshold", kwargs)


if __name__ == "__main__":
    unittest.main()
