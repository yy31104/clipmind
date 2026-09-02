from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from clipmind import keyframes, media
from clipmind.media import Frame


def make_frame(
    index: int,
    *,
    timestamp: float | None = None,
    phash: int = 0,
    lines: tuple[str, ...] = (),
) -> Frame:
    return Frame(
        index=index,
        timestamp=float(index) if timestamp is None else timestamp,
        path=Path(f"frame-{index}.jpg"),
        phash=phash,
        lines=lines,
    )


class PerceptualHashTests(unittest.TestCase):
    def test_dhash_encodes_horizontal_brightness_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            flat_path = Path(tempdir) / "flat.png"
            rising_path = Path(tempdir) / "rising.png"

            Image.new("L", (9, 8), color=50).save(flat_path)
            rising = Image.new("L", (9, 8))
            rising.putdata([column for _row in range(8) for column in range(9)])
            rising.save(rising_path)

            self.assertEqual(media.dhash(flat_path), 0)
            self.assertEqual(media.dhash(rising_path), (1 << 64) - 1)

    def test_hamming_counts_changed_bits_symmetrically(self) -> None:
        self.assertEqual(media.hamming(0b1010, 0b0011), 2)
        self.assertEqual(media.hamming(0b0011, 0b1010), 2)
        self.assertEqual(media.hamming(0b1010, 0b1010), 0)


class DedupeTests(unittest.TestCase):
    def test_dedupe_compares_against_the_last_kept_frame(self) -> None:
        frames = [make_frame(index) for index in range(3)]

        with patch.object(media, "dhash", side_effect=[0b000, 0b001, 0b011]):
            kept = media.dedupe(frames, threshold=1)

        self.assertEqual(kept, [frames[0], frames[2]])
        self.assertEqual([frame.phash for frame in frames], [0b000, 0b001, 0b011])


class VisualSelectionTests(unittest.TestCase):
    def test_collapse_builds_keeps_the_completed_state(self) -> None:
        partial = make_frame(0, timestamp=0.0, lines=("alpha",))
        completed = make_frame(1, timestamp=2.0, lines=("alpha beta",))
        next_scene = make_frame(2, timestamp=9.0, lines=("gamma",))

        collapsed = keyframes.collapse_builds([partial, completed, next_scene])

        self.assertEqual(collapsed, [completed, next_scene])

    def test_collapse_builds_does_not_cross_the_time_window(self) -> None:
        partial = make_frame(0, timestamp=0.0, lines=("alpha",))
        later = make_frame(1, timestamp=6.1, lines=("alpha beta",))

        self.assertEqual(keyframes.collapse_builds([partial, later]), [partial, later])

    def test_collapse_builds_preserves_low_overlap_states(self) -> None:
        first_scene = make_frame(0, timestamp=0.0, lines=("alpha",))
        different_scene = make_frame(1, timestamp=2.0, lines=("bravo", "charlie"))

        self.assertEqual(
            keyframes.collapse_builds([first_scene, different_scene]),
            [first_scene, different_scene],
        )

    def test_score_tracks_text_novelty_and_visual_change(self) -> None:
        novel_text = make_frame(0, phash=0b000000, lines=("a b",))
        repeated_text = make_frame(1, phash=0b001111, lines=("ab",))
        visual_only = make_frame(2, phash=0b111111)
        one_new_character = make_frame(3, phash=0b111111, lines=("abc",))
        frames = [novel_text, repeated_text, visual_only, one_new_character]

        keyframes.score(frames)

        self.assertEqual([frame.novelty for frame in frames], [2, 0, 0, 1])
        self.assertEqual(novel_text.score, 36.0)
        self.assertAlmostEqual(repeated_text.score, 1.2)
        self.assertEqual(visual_only.score, 2.0)
        self.assertEqual(one_new_character.score, 2.0)

    def test_select_keeps_opening_context_and_returns_time_order(self) -> None:
        opening = make_frame(0, timestamp=0.0, phash=0)
        strongest_change = make_frame(1, timestamp=2.0, phash=(1 << 64) - 1)
        lesser_change = make_frame(2, timestamp=4.0, phash=(1 << 16) - 1)

        selected = keyframes.select(
            [opening, strongest_change, lesser_change],
            limit=2,
        )

        self.assertEqual(selected, [opening, strongest_change])
        self.assertEqual([frame.timestamp for frame in selected], [0.0, 2.0])


if __name__ == "__main__":
    unittest.main()
