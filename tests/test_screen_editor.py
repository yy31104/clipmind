from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import cv2
import numpy as np

from clipmind import screen_interaction, screen_rank
from clipmind.media import Frame


class ScreenEditorTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def editor(self, statement):
        image = np.full((360, 640), 28, np.uint8)
        font = cv2.FONT_HERSHEY_SIMPLEX
        code = [
            "fun refresh(path) {",
            "destination = resolve_path(path)",
            "image = read_buffer(destination)",
            statement,
            "cache = buffer_store(image)",
            "display.draw_image(cache)",
            "record_event(cache)",
            "display.refresh(cache)",
            "publish_event(cache)",
            "return latest_frame(cache)",
            "}",
        ]
        layout = []
        for number, line in enumerate(code, 11):
            y = 78 + (number - 11) * 20
            marker = str(number)
            (width, _), _ = cv2.getTextSize(marker, font, .37, 1)
            cv2.putText(image, marker, (118 - width, y), font, .37, 78, 1)
            if not line:
                continue
            (w, h), _ = cv2.getTextSize(line, font, .51, 1)
            cv2.putText(image, line, (144, y), font, .51, 226, 1, cv2.LINE_AA)
            layout.append({"text": line, "bbox": (144 / 640, (y - h) / 360, w / 640, h / 360)})
        # The image has a gutter. The OCR observations contain only code.
        return image, tuple(layout)

    def popup(self, editor):
        image, layout = editor
        image = image.copy()
        image[118:224, 139:451] = 55
        font = cv2.FONT_HERSHEY_SIMPLEX
        for y in (137, 154, 171, 188, 205):
            cv2.putText(image, "completion", (163, y), font, .43, 138, 1)
        return image, layout

    def timeline(self, editors):
        frames = []
        for i, (image, layout) in enumerate(editors):
            path = self.root / f"state-{i}.png"
            self.assertTrue(cv2.imwrite(str(path), image))
            frames.append(Frame(i, i * 2., path, ocr_layout=layout))
        return frames

    def selected(self, frames, scores):
        adjusted, fallback = screen_interaction.adjust_scores(frames, scores)
        return screen_rank._select(
            [frame.timestamp for frame in frames], adjusted, set(), fallback_onsets=fallback,
        )

    def test_raster_gutter_discounts_reversible_completion_panel(self):
        base = self.editor("")
        frames = self.timeline([base, self.popup(base), base, base])
        self.assertEqual(self.selected(frames, {1: .5, 2: .5}), set())

    def test_panel_close_keeps_a_real_underlying_call(self):
        base = self.editor("")
        edited = self.editor("new_result = format_preview(image)")
        frames = self.timeline([base, self.popup(base), edited, edited])
        adjusted, _ = screen_interaction.adjust_scores(frames, {1: .5, 2: .49})
        self.assertEqual(adjusted[2], .49)

    def test_settled_call_can_end_without_semicolon(self):
        base = self.editor("")
        partial = self.editor("new_result = format_preview(")
        complete = self.editor("new_result = format_preview(image)")
        frames = self.timeline([base, partial, complete, complete])
        self.assertEqual(self.selected(frames, {1: .5, 2: .08}), {2})
