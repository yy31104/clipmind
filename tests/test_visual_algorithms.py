from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from clipmind import media, visual_states
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


class OCRAnnotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ocr_failure_is_attached_to_the_affected_frame(self) -> None:
        frame = make_frame(0)
        with patch.object(
            visual_states.ocr,
            "read_text",
            side_effect=RuntimeError("injected OCR failure"),
        ):
            error = await visual_states.annotate([frame], asyncio.Semaphore(1))

        self.assertEqual(frame.lines, ())
        self.assertEqual(frame.text, "")
        self.assertEqual(
            frame.ocr_warning,
            "RuntimeError: Vision OCR failed; image retained",
        )
        self.assertIn("OCR failed on 1/1 frames", error)


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



if __name__ == "__main__":
    unittest.main()
