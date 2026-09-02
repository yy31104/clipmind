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


class SamplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_width_controls_evidence_sampling(self) -> None:
        captured: list[str] = []

        async def fake_ffmpeg(args: list[str]) -> None:
            captured.extend(args)
            pattern = Path(args[-1])
            (pattern.parent / "s_00001.jpg").write_bytes(b"frame")

        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch.object(media, "_ffmpeg", new=fake_ffmpeg),
            patch.object(media, "settings", type("S", (), {"sample_fps": 3.0, "sample_width": 640})()),
        ):
            frames = await media.sample_frames(
                Path("video.mp4"), Path(tempdir), width=1280
            )

        self.assertIn("fps=3.0,scale=1280:-2", captured)
        self.assertEqual([(frame.index, frame.timestamp) for frame in frames], [(0, 0.0)])


class DedupeTests(unittest.TestCase):
    def test_dedupe_compares_against_the_last_kept_frame(self) -> None:
        frames = [make_frame(index) for index in range(3)]

        with patch.object(media, "dhash", side_effect=[0b000, 0b001, 0b011]):
            kept = media.dedupe(frames, threshold=1)

        self.assertEqual(kept, [frames[0], frames[2]])
        self.assertEqual([frame.phash for frame in frames], [0b000, 0b001, 0b011])

    def test_dedupe_removes_exact_and_near_duplicates(self) -> None:
        frames = [make_frame(index) for index in range(4)]

        with patch.object(media, "dhash", side_effect=[0b000, 0b000, 0b001, 0b111]):
            kept = media.dedupe(frames, threshold=1)

        self.assertEqual(kept, [frames[0], frames[3]])

    def test_hash_failure_retains_evidence_and_records_a_safe_warning(self) -> None:
        frames = [make_frame(index) for index in range(3)]

        with patch.object(
            media,
            "dhash",
            side_effect=[0b000, OSError("/Users/private/Cookies"), 0b000],
        ):
            kept = media.dedupe(frames, threshold=0)

        self.assertEqual(kept, frames)
        self.assertEqual(
            frames[1].dedupe_warning,
            "OSError: perceptual hash failed; frame retained",
        )
        self.assertNotIn("Cookies", frames[1].dedupe_warning)

    def test_comparison_failure_retains_evidence_and_records_a_warning(self) -> None:
        frames = [make_frame(index) for index in range(2)]

        with (
            patch.object(media, "dhash", side_effect=[0b000, 0b111]),
            patch.object(media, "hamming", side_effect=RuntimeError("injected")),
        ):
            kept = media.dedupe(frames, threshold=1)

        self.assertEqual(kept, frames)
        self.assertEqual(
            frames[1].dedupe_warning,
            "RuntimeError: perceptual comparison failed; frame retained",
        )


class VisualSelectionTests(unittest.TestCase):
    def test_collapse_builds_keeps_the_completed_state(self) -> None:
        partial = make_frame(0, timestamp=0.0, lines=("alpha",))
        completed = make_frame(1, timestamp=2.0, lines=("alpha beta",))
        next_scene = make_frame(2, timestamp=9.0, lines=("gamma",))

        collapsed = keyframes.collapse_builds([partial, completed, next_scene])

        self.assertEqual(collapsed, [completed, next_scene])

    def test_collapse_builds_does_not_cross_the_time_window(self) -> None:
        window = 1.0
        partial = make_frame(0, timestamp=0.0, lines=("alpha",))
        later = make_frame(1, timestamp=window + 0.1, lines=("alpha beta",))

        self.assertEqual(
            keyframes.collapse_builds([partial, later], window=window),
            [partial, later],
        )

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
        self.assertGreater(novel_text.score, repeated_text.score)
        self.assertLess(repeated_text.score, visual_only.score)
        self.assertGreater(one_new_character.score, repeated_text.score)

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
