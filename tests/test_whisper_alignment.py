"""Run the installed dependency's alignment boundary without model weights."""
from __future__ import annotations

import importlib.util
from types import SimpleNamespace
import unittest


@unittest.skipUnless(importlib.util.find_spec("faster_whisper"), "portable ASR not installed")
class WhisperAlignmentTests(unittest.TestCase):
    def test_short_window_empty_alignment_has_no_word_timestamps(self) -> None:
        from faster_whisper.transcribe import WhisperModel

        # CTranslate2 >=4.8.1 returns an empty alignment for a window shorter
        # than the encoder stride. Exercise the actual installed Python code.
        backend = SimpleNamespace(align=lambda *args, **kwargs: [
            SimpleNamespace(text_token_probs=[1.0], alignments=[]),
        ])
        model = SimpleNamespace(model=backend, tokens_per_second=50)
        tokenizer = SimpleNamespace(
            sot_sequence=[0], eot=2,
            split_to_word_tokens=lambda tokens: (["speech", ""], [[1], [2]]),
        )
        self.assertEqual(
            WhisperModel.find_alignment(model, tokenizer, [[1]], None, num_frames=1),
            [[]],
        )


if __name__ == "__main__":
    unittest.main()
