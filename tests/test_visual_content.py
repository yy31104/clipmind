from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from clipmind.media import Frame
from clipmind.visual_content import CONTENT_ALGORITHM, select_content_frames


class VisualContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def frame(self, index, image, lines=(), layout=()):
        path = self.root / f"{index}.png"
        image.save(path)
        return Frame(index, float(index), path, lines=tuple(lines), text="\n".join(lines),
                     ocr_layout=tuple(layout), phash=index * 1000003)

    def person(self):
        image = Image.new("RGB", (240, 180), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((83, 22, 157, 96), fill=(170, 130, 100))
        draw.polygon([(73, 100), (165, 100), (204, 179), (35, 179)], fill=(35, 45, 50))
        return image

    def document(self, index=1, texts=None):
        texts = texts or ("Investment research checklist", "Record independent assumptions", "Validate data before deployment")
        image = Image.new("RGB", (240, 180), "white")
        draw = ImageDraw.Draw(image)
        boxes = []
        for line, y in zip(texts, (.15, .35, .55)):
            draw.text((24, int(y * 180)), line, fill="black")
            boxes.append({"text": line, "confidence": .95, "bbox": [.1, y, .75, .07]})
        return self.frame(index, image, texts, boxes)

    def plot(self, index, dark=False, shift=0):
        background, foreground = ("black", "white") if dark else ("white", "black")
        image = Image.new("RGB", (240, 180), background)
        draw = ImageDraw.Draw(image)
        draw.line([(25, 15), (25, 155), (225, 155)], fill=foreground, width=2)
        draw.line([(26, 140), (80, 105), (145, 112), (220, 30 + shift)],
                  fill="yellow" if dark else "blue", width=2)
        return self.frame(index, image)

    def test_pure_speech_has_no_initial_final_or_scene_context_images(self):
        frames, spoken = [], []
        for index, text in enumerate(("first spoken sentence", "second spoken sentence", "third spoken sentence")):
            frames.append(self.frame(index, self.person(), (text,), (
                {"text": text, "confidence": .96, "bbox": [.1, .82, .8, .08]},)))
            spoken.append((float(index), float(index + 1), text))
        self.assertEqual(select_content_frames(frames, spoken), [])
        self.assertEqual(len(frames), 3)

    def test_short_syntactic_code_survives_even_when_read_aloud(self):
        code = "print('hello')"
        image = Image.new("RGB", (240, 180), "white")
        ImageDraw.Draw(image).text((24, 45), code, fill="black")
        frame = self.frame(0, image, (code,), ({
            "text": code, "confidence": .95, "bbox": [.1, .25, .6, .1]},))
        self.assertEqual(select_content_frames([frame], ((0., 1., "print hello"),)), [frame])

    def test_narrated_slide_and_ui_data_are_still_independent_layout(self):
        document = self.document()
        spoken = ((0., 3., " ".join(document.lines)),)
        self.assertEqual(select_content_frames([document], spoken), [document])
        data = self.document(2, ("Account balance 12500 USD", "Portfolio equity 13800 USD", "Maximum drawdown 7 percent"))
        self.assertEqual(select_content_frames([data]), [data])

    def test_independent_instruction_list_has_positive_aligned_structure(self):
        frame = self.document(1, ("1. Record source assumptions", "2. Test the strategy offline", "3. Inspect errors before deployment"))
        self.assertEqual(select_content_frames([frame]), [frame])

    def test_empty_ocr_light_and_dark_plots_survive_and_similar_changed_plot_is_not_collapsed(self):
        frames = [self.plot(0), self.plot(1, shift=1), self.plot(2, dark=True)]
        self.assertEqual(select_content_frames(frames), frames)

    def test_mixed_speech_document_speech_only_keeps_document(self):
        first = self.frame(0, self.person(), ("talking about research",))
        document = self.document(1)
        last = self.frame(2, self.person(), ("more spoken narration",))
        self.assertEqual(select_content_frames([last, first, document]), [document])

    def test_plain_white_black_and_white_wall_person_are_not_chart_canvases(self):
        frames = [self.frame(0, Image.new("RGB", (240, 180), "white")),
                  self.frame(1, Image.new("RGB", (240, 180), "black")),
                  self.frame(2, self.person())]
        self.assertEqual(select_content_frames(frames), [])

    def test_no_layout_ocr_garbage_and_old_document_hint_do_not_prove_a_document(self):
        text = tuple(f"random a = Lo @@ fragments {index}" for index in range(12))
        image = Image.effect_noise((240, 180), 80).convert("RGB")
        frame = self.frame(0, image, text)
        frame.content_hint = "document_slide"
        frame.transcript_novelty = 200
        self.assertEqual(select_content_frames([frame]), [])

    def test_generic_code_words_and_punctuation_are_not_syntax(self):
        image = self.person()
        text = ("return", "function", "importantly this matters", "{} ; =")
        frame = self.frame(0, image, text)
        self.assertEqual(select_content_frames([frame]), [])

    def test_missing_image_or_bad_layout_is_not_positive_evidence(self):
        frame = self.frame(0, self.person(), ("some background text",), (
            {"text": "unreliable line", "confidence": float("nan"), "bbox": [.1, .2, .8, .1]},))
        self.assertEqual(select_content_frames([frame]), [])
        frame.path.unlink()
        self.assertEqual(select_content_frames([frame]), [])
        self.assertEqual(CONTENT_ALGORITHM, "independent-visual-content-v2")


if __name__ == "__main__":
    unittest.main()
