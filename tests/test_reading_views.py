"""Rendered reading-view regressions, with no source media or model downloads.

The public selector may choose a clearer moving-camera view of a paragraph.
That reading aid must not hide a new paragraph or a stable document edit.
"""
from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from clipmind.media import Frame
from clipmind.visual_content import select_content_frames


class ReadingViewTests(unittest.TestCase):
    SIZE = (1600, 1200)
    PARAGRAPHS = (
        "Record the research question before choosing a model or an implementation.",
        "Keep the original observations so another reader can check each conclusion.",
        "Separate development examples from the final independent evaluation set.",
        "Write down operational assumptions and the evidence that could reject them.",
        "Compare a simple baseline with the proposed workflow using the same inputs.",
        "Measure failure cases and inspect the examples that contradict the summary.",
        "Preserve the explanation and relevant document alongside the result.",
        "Review changes with another person before relying on the final report.",
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="clipmind-reading-views-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.font = ImageFont.load_default(size=20)

    def frame(self, index, *, rows=None, shift=(0., 0.), confidence=.99,
              blurred=False, kind=None, timestamp=None):
        image = Image.new("RGB", self.SIZE, "white")
        draw = ImageDraw.Draw(image)
        layout = []
        texts = list(self.PARAGRAPHS if rows is None else rows)
        if kind == "code":
            texts.append("import numpy as np")
        for number, text in enumerate(texts):
            # These are actual rendered glyph bounds, not mocked hashes or
            # whole-image OCR boxes. Translation changes both pixels and OCR.
            position = (round((.08 + shift[0]) * image.width),
                        round((.12 + number * .075 + shift[1]) * image.height))
            draw.text(position, text, font=self.font, fill="black")
            left, top, right, bottom = draw.textbbox(position, text, font=self.font)
            layout.append({"text": text, "confidence": confidence,
                           "bbox": [left / image.width, top / image.height,
                                    (right-left) / image.width, (bottom-top) / image.height]})
        if kind == "chart":
            # The paragraphs alone qualify as a reading view. A plotted figure
            # below them must keep this candidate out of approximate merging.
            sx, sy = round(shift[0]*image.width), round(shift[1]*image.height)
            draw.line([(120+sx, 750+sy), (120+sx, 1120+sy), (1450+sx, 1120+sy)],
                      fill="black", width=5)
            draw.line([(140+sx, 1080+sy), (500+sx, 960+sy),
                       (900+sx, 1010+sy), (1420+sx, 780+sy)], fill="blue", width=4)
        if blurred:
            image = image.filter(ImageFilter.GaussianBlur(1.1))
        path = self.root / f"frame-{index}.png"
        image.save(path)
        return Frame(index, float(index if timestamp is None else timestamp), path,
                     text="\n".join(texts), lines=tuple(texts), ocr_layout=tuple(layout))

    def select(self, frames):
        decisions = {}
        selected = select_content_frames(frames, decisions=decisions)
        return selected, decisions

    def test_jittering_paragraph_views_choose_the_more_readable_observation(self):
        blurred = self.frame(0, confidence=.90, blurred=True)
        sharp = self.frame(1, shift=(.02, .015))
        selected, decisions = self.select([blurred, sharp])
        self.assertEqual(selected, [sharp])
        self.assertEqual(decisions[blurred.index]["reason"], "repeated_reading_view")
        self.assertEqual(decisions[blurred.index]["representative_indexes"], [sharp.index])

    def test_new_paragraph_and_brief_insert_remain_available(self):
        new = "Investigate survivorship bias before interpreting the reported improvement."
        for variant in (self.PARAGRAPHS + (new,),
                        self.PARAGRAPHS[:-1] + (new,)):
            with self.subTest(appended=len(variant) > len(self.PARAGRAPHS)):
                first = self.frame(0, timestamp=0.)
                inserted = self.frame(1, rows=variant, shift=(.02, .015), timestamp=.2)
                later = self.frame(2, shift=(-.02, -.015), timestamp=3.)
                selected, _ = self.select([later, inserted, first])
                self.assertIn(inserted, selected)
                self.assertTrue(any(new in frame.lines for frame in selected))
                self.assertEqual(selected, sorted(selected, key=lambda item: item.timestamp))

    def test_static_document_small_edits_survive_even_with_existing_reference_values(self):
        changes = (
            ("The production estimate is 100 after applying the stated constraints.",
             "The production estimate is 101 after applying the stated constraints."),
            ("The selected policy is A for the complete independent evaluation.",
             "The selected policy is B for the complete independent evaluation."),
            ("The API response must be archived with every evaluation result.",
             "The api response must be archived with every evaluation result."),
            ("The final report should allow automatic deployment after review.",
             "The final report should not allow automatic deployment after review."),
        )
        reference = "Reference examples already contain values 100 and 101 and policies A and B."
        for before, after in changes:
            with self.subTest(before=before, after=after):
                rows = (before, reference) + self.PARAGRAPHS[:6]
                first = self.frame(0, rows=rows)
                changed = self.frame(1, rows=(after,) + rows[1:])
                selected, decisions = self.select([first, changed])
                self.assertEqual(selected, [first, changed])
                self.assertFalse(any(item["reason"] == "repeated_reading_view"
                                     for item in decisions.values()))

    def test_invalid_extra_layout_cannot_crash_or_prove_a_repeated_reading_view(self):
        invalid = (None, "unusable block", 7, {},
                   {"text": self.PARAGRAPHS[0], "confidence": .99, "bbox": None},
                   {"text": "Unlocated extra observation", "confidence": .99})
        for block in invalid:
            with self.subTest(block=block):
                first = self.frame(0)
                uncertain = self.frame(1, shift=(.02, .015))
                uncertain.ocr_layout += (block,)
                selected, _ = self.select([first, uncertain])
                # The healthy rows still prove that this is readable content,
                # but incomplete OCR cannot prove all visible text is covered.
                self.assertEqual(selected, [first, uncertain])

    def test_moved_numeric_edit_is_not_excused_by_values_in_another_row(self):
        before = "The production estimate is 100 after applying the stated constraints."
        after = "The production estimate is 101 after applying the stated constraints."
        reference = "The unrelated reference examples contain both values 100 and 101."
        rows = (before, reference) + self.PARAGRAPHS[:6]
        first = self.frame(0, rows=rows)
        moved = self.frame(1, rows=(after,) + rows[1:], shift=(.02, .015))
        selected, _ = self.select([first, moved])
        self.assertEqual(selected, [first, moved])

    def test_moved_trustworthy_letter_case_and_negation_edits_remain_visible(self):
        changes = (
            ("The selected policy is A for the complete independent evaluation.",
             "The selected policy is B for the complete independent evaluation."),
            ("The API response must be archived with every evaluation result.",
             "The api response must be archived with every evaluation result."),
            ("The final report should allow automatic deployment after review.",
             "The final report should not allow automatic deployment after review."),
        )
        for before, after in changes:
            with self.subTest(before=before, after=after):
                rows = (before,) + self.PARAGRAPHS[:7]
                first = self.frame(0, rows=rows)
                moved = self.frame(1, rows=(after,) + rows[1:], shift=(.02, .015))
                selected, _ = self.select([first, moved])
                self.assertEqual(selected, [first, moved])

    def test_chart_and_code_views_keep_native_visual_differences(self):
        for kind in ("chart", "code"):
            with self.subTest(kind=kind):
                # Three long paragraph rows are sufficient to tempt a generic
                # text-similarity merger; code and plots need strict comparison.
                first = self.frame(0, rows=self.PARAGRAPHS[:3], kind=kind)
                moved = self.frame(1, rows=self.PARAGRAPHS[:3], kind=kind,
                                   shift=(.02, .015))
                selected, decisions = self.select([first, moved])
                self.assertEqual(selected, [first, moved])
                self.assertEqual({item["reason"] for item in decisions.values()},
                                 {"independent_" + kind})

    def test_ocr_or_deduplication_warnings_keep_uncertain_views(self):
        first = self.frame(0)
        bad_ocr = self.frame(1, shift=(.02, .015))
        bad_hash = self.frame(2, shift=(-.02, -.015))
        bad_ocr.ocr_warning = "OCR confidence cannot be verified"
        bad_hash.dedupe_warning = "Image comparison was incomplete"
        selected, decisions = self.select([first, bad_ocr, bad_hash])
        self.assertEqual(selected, [first, bad_ocr, bad_hash])
        self.assertFalse(any(item["reason"] == "repeated_reading_view"
                             for item in decisions.values()))

    def test_sparse_reading_labels_are_not_merged_as_repeated_paragraphs(self):
        labels = ("Portfolio alpha", "Risk budget map", "Result notebook")
        first = self.frame(0, rows=labels)
        moved = self.frame(1, rows=labels, shift=(.02, .015))
        selected, decisions = self.select([first, moved])
        self.assertEqual(selected, [first, moved])
        self.assertEqual({item["reason"] for item in decisions.values()},
                         {"independent_reading_layout"})

    def test_sparse_photo_labels_do_not_establish_independent_reading_value(self):
        for rows, useful in (
            (("Founder of Example Company", "Co-founder of Another Company", "Managing Partner"), False),
            (("1. Record assumptions before testing", "2. Compare independent evidence", "3. Review operational failures"), True),
            (("Portfolio balance 12500 USD", "Account equity 13800 USD", "Maximum drawdown 7 percent"), True),
        ):
            with self.subTest(useful=useful):
                frame = self.frame(0, rows=rows)
                # A photographic backdrop, not a low-saturation page canvas.
                photo = Image.effect_noise(self.SIZE, 45).convert("RGB")
                with Image.open(frame.path) as source:
                    text = source.convert("L").point(lambda value: 255 if value < 80 else 0)
                    photo.paste((0, 0, 0), mask=text)
                photo.save(frame.path)
                selected, _ = self.select([frame])
                self.assertEqual(bool(selected), useful)

    def test_omission_references_are_selected_and_sources_are_unchanged(self):
        frames = [self.frame(0, shift=(-.02, -.015), confidence=.90), self.frame(1),
                  self.frame(2, shift=(.02, .015))]
        # Include a strict pixel duplicate to check that its representative
        # reference is also redirected if the initial reading view is omitted.
        duplicate = copy.deepcopy(frames[0])
        duplicate.index, duplicate.timestamp = 3, 3.
        frames.append(duplicate)
        original_frames = copy.deepcopy(frames)
        original_files = {frame.path: hashlib.sha256(frame.path.read_bytes()).hexdigest()
                          for frame in frames}
        selected, decisions = self.select(list(reversed(frames)))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].index, 1)
        selected_ids = {frame.index for frame in selected}
        omitted = [frame for frame in frames if frame.index not in selected_ids]
        self.assertTrue(omitted)
        for frame in omitted:
            decision = decisions[frame.index]
            self.assertIn(decision["representative_index"], selected_ids)
            for index in decision.get("representative_indexes", []):
                self.assertIn(index, selected_ids)
        self.assertEqual(frames, original_frames)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in original_files}, original_files)


if __name__ == "__main__":
    unittest.main()
