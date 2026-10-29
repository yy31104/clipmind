from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from clipmind.media import Frame
from clipmind.visual_states import suppress_repeated_captions


class CaptionSelectionTests(unittest.TestCase):
    def make_frame(self, root: Path, index: int, text: str, *, chart: bool = False,
                   y: float = 0.8) -> Frame:
        image = Image.linear_gradient("L").convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 100, 140), fill="navy")
        draw.text((30, int(y * 256)), text, fill="white")
        if chart:
            draw.line((110, 150, 210, 60), fill="red", width=5)
        path = root / f"{index}.png"
        image.save(path)
        return Frame(index, float(index), path, lines=(text,), text=text,
                     ocr_layout=({"text": text, "bbox": [0.1, y, 0.8, 0.1]},))

    def test_different_subtitles_with_same_body_and_speech_keep_one_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [self.make_frame(root, 0, "first subtitle"),
                      self.make_frame(root, 1, "second subtitle")]
            spoken = [(0, 1, "first subtitle"), (1, 2, "second subtitle")]
            self.assertEqual(suppress_repeated_captions(frames, spoken), frames[:1])
            self.assertTrue(all(frame.path.is_file() for frame in frames))

    def test_a_changed_chart_survives_even_if_subtitles_match_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [self.make_frame(root, 0, "first subtitle"),
                      self.make_frame(root, 1, "second subtitle", chart=True)]
            self.assertEqual(suppress_repeated_captions(
                frames, [(0, 1, "first subtitle"), (1, 2, "second subtitle")]), frames)

    def test_small_plot_marker_on_flat_canvas_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [self.make_frame(root, i, "same subtitle") for i in range(2)]
            for i, frame in enumerate(frames):
                image = Image.new("RGB", (1280, 720), "white")
                draw = ImageDraw.Draw(image)
                draw.text((140, 580), "same subtitle", fill="black")
                if i:
                    draw.rectangle((700, 200, 724, 224), fill="red")
                image.save(frame.path)
            self.assertEqual(suppress_repeated_captions(
                frames, [(0, 4, "same subtitle")]), frames)

    def test_centered_slide_read_aloud_is_not_a_subtitle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [self.make_frame(root, 0, "first concept", y=0.3),
                      self.make_frame(root, 1, "second concept", y=0.3)]
            self.assertEqual(suppress_repeated_captions(
                frames, [(0, 1, "first concept"), (1, 2, "second concept")]), frames)

    def test_missing_layout_or_ocr_text_does_not_mean_disposable(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = [self.make_frame(Path(tmp), i, "same subtitle") for i in range(3)]
            frames[1].ocr_layout = ()
            frames[2].lines = ()
            self.assertEqual(suppress_repeated_captions(
                frames, [(0, 4, "same subtitle")]), frames)

    def test_ocr_or_asr_failure_retains_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = [self.make_frame(Path(tmp), i, "same subtitle") for i in range(2)]
            self.assertEqual(suppress_repeated_captions(frames, []), frames)
            frames[1].ocr_warning = "failed"
            self.assertEqual(suppress_repeated_captions(
                frames, [(0, 4, "same subtitle")]), frames)

    def test_unspoken_terms_and_code_are_not_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = [self.make_frame(Path(tmp), 0, "Sharpe ratio"),
                      self.make_frame(Path(tmp), 1, "return load()")]
            self.assertEqual(suppress_repeated_captions(
                frames, [(0, 1, "other words"), (1, 2, "return load()")]), frames)

    def test_a_missing_image_is_not_assumed_to_be_a_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = [self.make_frame(Path(tmp), i, "same subtitle") for i in range(2)]
            frames[1].path.unlink()
            self.assertEqual(suppress_repeated_captions(
                frames, [(0, 4, "same subtitle")]), frames)


if __name__ == "__main__":
    unittest.main()
