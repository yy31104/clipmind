from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import cv2
import numpy as np

from clipmind import screen_interaction, screen_rank
from clipmind.media import Frame


class ScreenInteractionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def frames(self, images, timestamps, lines=None):
        frames = []
        for i, (image, timestamp) in enumerate(zip(images, timestamps, strict=True)):
            path = self.root / f"{i}.png"
            self.assertTrue(cv2.imwrite(str(path), image))
            frame = Frame(i, timestamp, path)
            if lines:
                frame.ocr_layout = tuple(
                    {"text": line, "bbox": (20 / 640, (y - 17) / 360, .5, 18 / 360)}
                    for line, y in lines[i]
                )
            frames.append(frame)
        return frames

    def selected(self, frames, scores):
        adjusted = screen_interaction.adjust_scores(frames, scores)
        return screen_rank._select([frame.timestamp for frame in frames], adjusted, set())

    def menu(self, image):
        rendered = image.copy()
        cv2.rectangle(rendered, (52, 26), (155, 110), 85, cv2.FILLED)
        for y in (45, 64, 83):
            cv2.putText(rendered, "Item", (61, y), cv2.FONT_HERSHEY_SIMPLEX, .45, 220, 1)
        return rendered

    def editor(self, glyph):
        image = np.full((360, 640), 28, np.uint8)
        cv2.putText(image, f"limit = {glyph};", (218, 146), cv2.FONT_HERSHEY_SIMPLEX, .6, 235, 1)
        return image

    def screen_text(self, lines, scale=.65):
        image = np.full((360, 640), 28, np.uint8)
        for line, y in lines:
            cv2.putText(image, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, 220, 2)
        return image

    def test_reversible_menu_only_is_discounted_but_persistent_edit_is_selected(self):
        old = self.editor(4)
        new = self.editor(9)
        frames = self.frames(
            [old, self.menu(old), old, self.menu(old), self.menu(new), new],
            [0., 2., 4., 6., 7., 9.],
        )
        self.assertEqual(self.selected(frames, {1: .5, 2: .5, 3: .5, 4: .07, 5: .5}), {4})

    def test_menu_opening_with_independent_edit_keeps_its_score(self):
        old = self.editor(4)
        new = old.copy()
        cv2.putText(new, "result = 90", (245, 220), cv2.FONT_HERSHEY_SIMPLEX, .6, 235, 2)
        frames = self.frames([old, self.menu(new), new], [0., 2., 4.])
        self.assertIn(1, self.selected(frames, {1: .5}))

    def test_highlight_in_open_menu_does_not_hide_a_nearby_edit(self):
        old, new = self.editor(4), self.editor(9)
        opened = self.menu(old)
        highlighted = opened.copy()
        highlighted[55:68, 54:152] = 160
        edited = self.menu(new)
        edited[55:68, 54:152] = 160
        frames = self.frames(
            [old, opened, highlighted, edited, new],
            [0., 2., 4., 6., 9.],
        )
        self.assertEqual(self.selected(frames, {1: .8, 2: .8, 3: .8, 4: .8}), {3})

    def test_temporary_textured_tile_keeps_its_score(self):
        base = np.full((360, 640), 220, np.uint8)
        tile = base.copy()
        for i, shade in enumerate((75, 125, 175)):
            x = 170 + i * 52
            tile[28:130, x:x + 52] = shade
        frames = self.frames([base, tile, base], [0., 2., 4.])
        self.assertIn(1, self.selected(frames, {1: .5}))

    def test_recurring_chrome_is_discounted_and_stable_canvas_move_is_selected(self):
        old = np.full((360, 640), 250, np.uint8)
        cv2.rectangle(old, (255, 175), (285, 198), 30, cv2.FILLED)
        toolbar = old.copy()
        cv2.rectangle(toolbar, (25, 8), (470, 18), 95, cv2.FILLED)
        moved = toolbar.copy()
        moved[175:199, 255:286] = 250
        cv2.rectangle(moved, (285, 170), (315, 193), 30, cv2.FILLED)
        frames = self.frames(
            [old, toolbar, old, toolbar, old, toolbar, moved, moved],
            [0., 2., 10., 18., 26., 34., 42., 45.],
        )
        self.assertEqual(self.selected(frames, {1: .4, 2: .4, 3: .4, 4: .4, 5: .4, 6: .08}), {6})

    def test_entered_shell_input_is_discounted_and_new_prompt_with_output_is_selected(self):
        transcripts = [
            [("host:~$", 120)],
            [("host:~$ ls -al", 120)],
            [("host:~$ ls -al", 120), ("archive documents scripts", 148), ("host:~$", 176)],
            [("host:~$ ls -al", 120), ("archive documents scripts", 148), ("host:~$", 176)],
        ]
        images = [self.screen_text(lines) for lines in transcripts]
        frames = self.frames(images, [0., 2., 5., 8.], lines=transcripts)
        self.assertEqual(self.selected(frames, {1: .5, 2: .5}), {2})

    def test_settled_statement_is_selected_and_partial_loop_is_omitted(self):
        context = [("int run() {", 96)]
        partial = [*context, ("value", 120)]
        complete = [*context, ("value += 2;", 120)]
        open_loop = [*complete, ("for (i = 0;", 154)]
        transcripts = [partial, complete, open_loop, open_loop]
        images = [self.screen_text(lines, scale=.56) for lines in transcripts]
        frames = self.frames(images, [0., 3., 7., 11.], lines=transcripts)
        self.assertEqual(self.selected(frames, {1: .08, 2: .08, 3: .08}), {1})
