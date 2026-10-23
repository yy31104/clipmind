"""Lossless image fixtures for content selection and protected subtitle dedup.

These exercise the public selector, with actual decoded PNG pixels and explicit
OCR layouts. They do not mock a hash or require real media, OCR, ASR or models.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from clipmind.media import Frame
from clipmind.visual_content import select_content_frames


class VisualDeduplicationTests(unittest.TestCase):
    SIZE = (1024, 768)
    BODY = ("Investment research checklist", "Portfolio estimate: 100", "Validate assumptions before deployment")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="clipmind-visual-dedup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.font = ImageFont.load_default(size=22)

    def line(self, image, text, x, y, *, confidence=.99):
        """Render text and measure its real glyph bounds for normalized OCR."""
        draw = ImageDraw.Draw(image)
        position = (round(x * image.width), round(y * image.height))
        draw.text(position, text, font=self.font, fill="black")
        left, top, right, bottom = draw.textbbox(position, text, font=self.font)
        return {"text": text, "confidence": confidence,
                "bbox": [left / image.width, top / image.height,
                         (right - left) / image.width, (bottom - top) / image.height]}

    def frame(self, index, image, layout=(), *, compress_level=6, timestamp=None):
        path = self.root / f"frame-{index}.png"
        image.save(path, compress_level=compress_level)
        lines = tuple(block["text"] for block in layout)
        return Frame(index, float(index * 10 if timestamp is None else timestamp), path,
                     text="\n".join(lines), lines=lines, ocr_layout=tuple(layout))

    def document(self, index, *, body=None, subtitle=None, ys=(.12, .28, .44),
                 confidence=.99, timestamp=None):
        image = Image.new("RGB", self.SIZE, "white")
        layout = [self.line(image, text, .10, y) for text, y in zip(body or self.BODY, ys)]
        if subtitle:
            # Centered captions are spatially separate from the left-aligned
            # document column; bottom body rows are tested separately below.
            x = (image.width - self.font.getlength(subtitle)) / (2 * image.width)
            layout.append(self.line(image, subtitle, x, .86, confidence=confidence))
        return self.frame(index, image, layout, timestamp=timestamp)

    def person(self, index, *, subtitle=None, shift=0, gesture=False, timestamp=None):
        image = Image.new("RGB", self.SIZE, "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((390 + shift, 100, 630 + shift, 340), fill=(175, 130, 100))
        draw.polygon([(360 + shift, 360), (660 + shift, 360), (750, 610), (275, 610)],
                     fill=(35, 45, 50))
        if gesture:
            draw.line((650, 420, 810, 270), fill=(175, 130, 100), width=35)
        layout = [self.line(image, subtitle, .10, .86)] if subtitle else []
        return self.frame(index, image, layout, timestamp=timestamp)

    def chart(self, index, *, marker=False, curve_shift=0, bottom=False, label=None):
        image = Image.new("RGB", self.SIZE, "white")
        draw = ImageDraw.Draw(image)
        top, baseline = (450, 720) if bottom else (70, 650)
        draw.line([(80, top), (80, baseline), (945, baseline)], fill="black", width=5)
        middle = (top + baseline) // 2
        draw.line([(90, baseline - 20), (350, middle + 30),
                   (650, middle + 70), (930, top + 25 + curve_shift)], fill="blue", width=4)
        if marker:
            image.putpixel((777, 123), (255, 0, 0))
        layout = [self.line(image, label, .40, .86)] if label else []
        return self.frame(index, image, layout)

    def speech(self, *frames):
        return [(frame.timestamp, frame.timestamp + 1, " ".join(frame.lines)) for frame in frames]

    def select(self, frames, spoken=()):
        return select_content_frames(frames, spoken_intervals=spoken)

    def test_same_document_with_fully_spoken_subtitle_changes_keeps_one(self):
        first = self.document(0, subtitle="first complete spoken explanation")
        second = self.document(1, subtitle="another complete spoken explanation")
        self.assertEqual(self.select([first, second], self.speech(first, second)), [first])

    def test_nonadjacent_duplicate_decoded_pixels_are_deduplicated(self):
        first = self.document(0)
        changed = self.document(1, body=(self.BODY[0], "Portfolio estimate: 101", self.BODY[2]))
        with Image.open(first.path) as image:
            repeated = self.frame(2, image.copy(), first.ocr_layout, compress_level=0)
        self.assertNotEqual(first.path.read_bytes(), repeated.path.read_bytes())
        with Image.open(first.path) as a, Image.open(repeated.path) as b:
            self.assertIsNone(ImageChops.difference(a, b).getbbox())
        # Input order is deliberately mixed; selection remains chronological.
        self.assertEqual(self.select([repeated, changed, first]), [first, changed])

    def test_portrait_motion_gestures_and_changing_spoken_captions_keep_no_images(self):
        frames = [self.person(0, subtitle="the first spoken sentence"),
                  self.person(1, subtitle="the next spoken sentence", shift=35, gesture=True),
                  self.person(2, subtitle="the final spoken sentence", shift=-20)]
        self.assertEqual(self.select(frames, self.speech(*frames)), [])

    def test_white_wall_spoken_code_subtitles_are_not_independent_code(self):
        for code in ("git status", "print('x')"):
            with self.subTest(code=code):
                frame = self.person(0, subtitle=code)
                self.assertEqual(self.select([frame], self.speech(frame)), [])

    def test_body_letters_numbers_case_punctuation_and_operators_are_lossless(self):
        differences = (("Factor alpha model", "Factor beta model"),
                       ("Portfolio value: 100", "Portfolio value: 101"),
                       ("Load API key", "Load api key"),
                       ("Risk limit: 1,000", "Risk limit: 1.000"),
                       ("signal = price > average", "signal = price < average"))
        for before, after in differences:
            with self.subTest(before=before, after=after):
                first = self.document(0, body=(self.BODY[0], before, self.BODY[2]))
                second = self.document(1, body=(self.BODY[0], after, self.BODY[2]))
                self.assertEqual(self.select([first, second], self.speech(first, second)), [first, second])

    def test_original_resolution_one_pixel_chart_marker_survives_without_ocr(self):
        first, second = self.chart(0), self.chart(1, marker=True)
        self.assertEqual(first.ocr_layout, ())
        self.assertEqual(second.ocr_layout, ())
        with Image.open(first.path) as a, Image.open(second.path) as b:
            self.assertEqual(ImageChops.difference(a, b).getbbox(), (777, 123, 778, 124))
        self.assertEqual(self.select([first, second]), [first, second])

    def test_one_pixel_curve_vertex_change_survives_without_ocr(self):
        first, second = self.chart(0), self.chart(1, curve_shift=1)
        self.assertEqual(self.select([first, second]), [first, second])

    def test_scroll_revealing_another_document_line_keeps_both_viewports(self):
        first = self.document(0, body=("Research hypothesis and inputs", "Portfolio estimate: 100", "Risk budget: 2 percent"))
        second = self.document(1, body=("Portfolio estimate: 100", "Risk budget: 2 percent", "Execution fee: 0.03 percent"))
        self.assertEqual(self.select([first, second], self.speech(first, second)), [first, second])

    def test_brief_document_between_portrait_frames_is_not_removed(self):
        first = self.person(0, subtitle="before the document", timestamp=0)
        document = self.document(1, timestamp=.5)
        last = self.person(2, subtitle="after the document", gesture=True, timestamp=1)
        self.assertEqual(self.select([last, document, first], self.speech(first, document, last)), [document])

    def test_bottom_document_body_is_not_a_subtitle_mask_even_when_spoken(self):
        first = self.document(0, body=(self.BODY[0], "Net profit: 120", "Risk budget: 2 percent"),
                              ys=(.56, .70, .84))
        second = self.document(1, body=(self.BODY[0], "Net profit: 121", "Risk budget: 2 percent"),
                               ys=(.56, .70, .84))
        self.assertEqual(self.select([first, second], self.speech(first, second)), [first, second])

    def test_bottom_chart_annotation_is_not_a_spoken_subtitle(self):
        first = self.chart(0, bottom=True, label="Net profit: 120")
        second = self.chart(1, bottom=True, label="Net profit: 121")
        self.assertEqual(self.select([first, second], self.speech(first, second)), [first, second])

    def test_changed_subtitles_without_asr_do_not_prove_redundancy(self):
        first = self.document(0, subtitle="first unverified subtitle")
        second = self.document(1, subtitle="second unverified subtitle")
        self.assertEqual(self.select([first, second]), [first, second])

    def test_partial_asr_coverage_does_not_prove_subtitle_redundancy(self):
        first = self.document(0, subtitle="spoken prefix with unique ALPHA123")
        second = self.document(1, subtitle="spoken prefix with unique BETA456")
        spoken = [(first.timestamp, first.timestamp + 1, "spoken prefix"),
                  (second.timestamp, second.timestamp + 1, "spoken prefix")]
        self.assertEqual(self.select([first, second], spoken), [first, second])

    def test_low_confidence_or_invalid_caption_boxes_do_not_prove_redundancy(self):
        for invalid in ("low-confidence", "missing-box", "nonfinite-box"):
            with self.subTest(invalid=invalid):
                first = self.document(0, subtitle="first complete spoken explanation")
                second = self.document(1, subtitle="another complete spoken explanation")
                caption = second.ocr_layout[-1]
                if invalid == "low-confidence":
                    caption["confidence"] = .2
                elif invalid == "missing-box":
                    caption.pop("bbox")
                else:
                    caption["bbox"][0] = float("nan")
                self.assertEqual(self.select([first, second], self.speech(first, second)), [first, second])

    def test_either_frame_ocr_or_hash_warning_prevents_subtitle_dedup(self):
        for warning in ("ocr_warning", "dedupe_warning"):
            for position in (0, 1):
                with self.subTest(warning=warning, position=position):
                    frames = [self.document(0, subtitle="first complete spoken explanation"),
                              self.document(1, subtitle="another complete spoken explanation")]
                    setattr(frames[position], warning, "synthetic uncertainty")
                    self.assertEqual(self.select(frames, self.speech(*frames)), frames)

    def test_chart_with_separate_spoken_captions_is_deduplicated(self):
        frames = []
        for index, text in enumerate(("first complete explanation", "another complete explanation")):
            frame = self.chart(index)
            with Image.open(frame.path) as source:
                image = source.copy()
            layout = [self.line(image, text, .35, .94)]
            frames.append(self.frame(index, image, layout))
        self.assertEqual(self.select(frames, self.speech(*frames)), frames[:1])

    def test_code_context_protects_short_and_long_bottom_assignments(self):
        first = self.document(0, body=("import os", "return client.fetch()", "profit = 100"),
                              ys=(.12, .28, .84))
        second = self.document(1, body=("import os", "return client.fetch()", "profit = 101"),
                               ys=(.12, .28, .84))
        self.assertEqual(self.select([first, second], self.speech(first, second)), [first, second])

    def test_same_bag_of_words_in_different_order_is_not_verified_speech(self):
        first = self.document(0, subtitle="do not buy sell later")
        second = self.document(1, subtitle="do not sell buy later")
        spoken = [(first.timestamp, first.timestamp + 1, "do not sell buy later"),
                  (second.timestamp, second.timestamp + 1, "do not buy sell later")]
        self.assertEqual(self.select([first, second], spoken), [first, second])

    def test_duplicate_anchor_does_not_drift_when_caption_masks_move(self):
        frames = []
        for index, (text, x) in enumerate((("buy now", .23),
                                         ("a much wider complete explanation", .34),
                                         ("sell now", .76))):
            original = self.document(index)
            with Image.open(original.path) as source:
                image = source.copy()
            if index != 1:
                image.putpixel((512, 680), (255, 0, 0) if index == 0 else (0, 0, 255))
            layout = [*original.ocr_layout, self.line(image, text, x, .86)]
            frames.append(self.frame(index, image, layout))
        # The middle subtitle covers the changed dot. It must not become a
        # replacement anchor that allows the last distinct image to disappear.
        frames[1].observed_sample_count = 100
        self.assertEqual(self.select(frames, self.speech(*frames)), [frames[0], frames[2]])

    def test_pixel_rounded_caption_box_never_masks_adjacent_body_pixel(self):
        frames = []
        for index, text in enumerate(("buy now", "sell now")):
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            body = []
            for number, y in enumerate((.1, .3, .5)):
                line = f"Independent document reading row {number}"
                body.append({"text": line, "confidence": .99, "bbox": [.05, y, .4, .05]})
                draw.rectangle((5, int(y*100), 44, int(y*100)+4), fill="black")
            draw.rectangle((30, 86, 49, 93), fill="black" if index == 0 else "gray")
            image.putpixel((50, 89), (255, 0, 0) if index == 0 else (0, 0, 255))
            layout = [*body,
                      {"text": text, "confidence": .99, "bbox": [.3, .86, .2000001, .08]},
                      {"text": "unspoken data label", "confidence": .99,
                       "bbox": [.5000002, .86, .3, .08]}]
            frames.append(self.frame(index, image, layout))
        spoken = [(frame.timestamp, frame.timestamp+1, frame.lines[-2]) for frame in frames]
        self.assertEqual(self.select(frames, spoken), frames)

    def test_unreliable_body_layout_disables_subtitle_masking(self):
        first = self.document(0, subtitle="first complete explanation")
        second = self.document(1, subtitle="another complete explanation")
        for frame in (first, second):
            frame.ocr_layout = (*frame.ocr_layout, {
                "text": "uncertain chart legend", "confidence": .2,
                "bbox": [.4, .86, .2, .04]})
            frame.lines = (*frame.lines, "uncertain chart legend")
        self.assertEqual(self.select([first, second], self.speech(first, second)), [first, second])

    def test_promotional_closing_card_is_not_an_independent_document(self):
        card = self.document(0, body=("LabBrand", "Follow for more research", "See you next time"))
        self.assertEqual(self.select([card], self.speech(card)), [])

    def test_closing_card_with_independent_data_is_still_preserved(self):
        card = self.document(0, body=("Annual return: 20 percent", "Follow for more research", "See you next time"))
        self.assertEqual(self.select([card], self.speech(card)), [card])

    def test_a_closing_phrase_does_not_turn_instructions_into_promotion(self):
        card = self.document(0, body=("风险管理", "关注仓位", "下期见"))
        # This compact instruction slide is short, but has two independent
        # substantive rows rather than a single logo and promotional text.
        decisions = {}
        select_content_frames([card], self.speech(card), decisions=decisions)
        self.assertNotEqual(decisions[card.index]["reason"], "promotional_outro")

    def test_spaced_traditional_outro_and_ornamental_ocr_are_not_a_document(self):
        card = self.document(0, body=("aa bcc cdd efe efe", "關 注 · 更 多 獨 立 策 略 研 究 教 程 文 章", "下 期 見"))
        decisions = {}
        self.assertEqual(select_content_frames([card], self.speech(card), decisions=decisions), [])
        self.assertEqual(decisions[card.index]["reason"], "promotional_outro")

    def test_figures_inside_a_follow_request_still_make_a_card_independent(self):
        card = self.document(0, body=("LabBrand", "Follow for more research: return 20 percent", "See you next time"))
        self.assertEqual(self.select([card], self.speech(card)), [card])

    def test_short_english_quotes_and_financial_abbreviations_are_not_ornament_noise(self):
        for text in ("to be or not to be", "pe pb roe pe pb roe"):
            with self.subTest(text=text):
                card = self.document(0, body=("LabBrand", text, "See you next time"))
                self.assertEqual(self.select([card], self.speech(card)), [card])


if __name__ == "__main__":
    unittest.main()
