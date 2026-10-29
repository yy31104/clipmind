"""Small, meaningful changes that collide under global perceptual hashing."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from clipmind import media


class CanonicalDetailTests(unittest.TestCase):
    def frames(self, root, images):
        frames = []
        for index, image in enumerate(images):
            path = root / f"{index}.png"
            image.save(path)
            frames.append(media.Frame(index, index * .5, path))
        return frames

    def test_same_hash_chart_mark_change_is_preserved(self):
        image = Image.new("RGB", (640, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.line((50, 40, 50, 310, 600, 310), fill="black", width=2)
        draw.line((50, 250, 200, 230, 400, 200, 590, 140), fill="blue", width=2)
        changed = image.copy()
        ImageDraw.Draw(changed).rectangle((391, 191, 397, 197), fill="red")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), [image, changed])
            self.assertLessEqual(media.hamming(*(media.dhash(f.path) for f in frames)), 6)
            self.assertEqual(media.dedupe(frames), frames)

    def test_digit_and_operator_change_is_not_hidden_by_tiny_area(self):
        image = Image.new("RGB", (640, 360), "white")
        ImageDraw.Draw(image).text((100, 100), "risk_limit = 0.10", fill="black")
        changed = Image.new("RGB", image.size, "white")
        ImageDraw.Draw(changed).text((100, 100), "risk_limit = 0.19", fill="black")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), [image, changed])
            self.assertLessEqual(media.hamming(*(media.dhash(f.path) for f in frames)), 6)
            self.assertEqual(media.dedupe(frames), frames)

    def test_colour_only_change_with_identical_hash_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), [Image.new("RGB", (64, 64), colour)
                                                     for colour in ("red", "blue")])
            self.assertEqual(media.dhash(frames[0].path), media.dhash(frames[1].path))
            self.assertEqual(media.dedupe(frames), frames)

    def test_identical_and_small_codec_noise_still_collapse(self):
        base = np.full((128, 128, 3), 220, dtype=np.uint8)
        noisy = base.copy()
        noisy[::2, ::2] -= 2
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), [Image.fromarray(base), Image.fromarray(base), Image.fromarray(noisy)])
            self.assertEqual(media.dedupe(frames), [frames[0]])
            self.assertEqual(frames[0].observed_sample_count, 3)
            self.assertEqual(frames[0].stable_duration, 1)

    def test_unreadable_pixels_retain_frame_and_do_not_expose_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), [Image.new("RGB", (32, 32), "white")] * 2)
            with patch.object(media, "_comparison_pixels", side_effect=OSError("private/Cookies")):
                self.assertEqual(media.dedupe(frames), frames)
            self.assertIn("frame retained", frames[1].dedupe_warning)
            self.assertNotIn("Cookies", frames[1].dedupe_warning)


if __name__ == "__main__":
    unittest.main()
