from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from clipmind import screen_motion, screen_viewport, visual_states
from clipmind.media import Frame


class ScreenMotionTests(unittest.TestCase):
    face = ((0.875, 0.48, 0.04, 0.06),)

    def frames(self, root, *, update=None, count=4, timestamps=None):
        frames = []
        for index in range(count):
            image = Image.new("RGB", (640, 360), "#101010")
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 10, 465, 285), fill="white")
            draw.text((35, 30), "Architecture", fill="black")
            draw.ellipse((100, 120, 210, 220), fill="black")
            # A bounded side pane supplies its own extent, independent of OCR.
            draw.rectangle((500, 150, 630, 350), fill="#402080")
            draw.rectangle((555 + index, 170, 585 + index, 205), fill="#e0b080")
            draw.rectangle((540, 208, 595, 310), fill=(50 + index * 30, 90, 120))
            if update:
                update(draw, index)
            path = root / f"state-{index}.png"
            image.save(path)
            frames.append(Frame(index, timestamps[index] if timestamps else float(index), path,
                                lines=("Architecture",), ocr_layout=({
                                    "text": "Architecture", "bbox": [0.05, 0.08, 0.2, 0.04]},)))
        return frames

    def test_recurring_pane_motion_has_positive_region_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory))
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.screen_motion_evidence(frames)
            self.assertEqual([result[f.index].kind for f in frames],
                             ["uncertain", "speaker", "speaker", "speaker"])
            self.assertTrue(result[1].regions)
            self.assertEqual(result[1].supporting_frames, 4)
            self.assertEqual(len(frames), 4)
            self.assertTrue(all(frame.change_kind == "uncertain" for frame in frames))

    def test_same_title_new_diagram_outside_supported_pane_stays_uncertain(self):
        def new_diagram(draw, index):
            if index:
                draw.rectangle((280, 120, 380, 220), fill="black")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), update=new_diagram)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertEqual(result[1], "uncertain")
            self.assertEqual(result[2], "speaker")

    def test_small_code_stroke_outside_pane_is_not_hidden_by_low_global_change(self):
        def new_stroke(draw, index):
            if index:
                draw.rectangle((300, 100, 302, 108), fill="black")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), update=new_stroke)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertEqual(result[1], "uncertain")

    def test_rgb_change_is_detected_even_when_grayscale_is_similar(self):
        def colour_change(draw, index):
            draw.rectangle((300, 100, 310, 110), fill=(255, 0, 0) if index else (0, 130, 0))
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), update=colour_change)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertEqual(result[1], "uncertain")

    def test_a_shifted_diagram_is_not_proved_by_a_repeated_title(self):
        def shifted(draw, index):
            draw.rectangle((90, 110, 230, 230), fill="white")
            draw.rectangle((100 + index * 10, 120, 210 + index * 10, 220), fill="black")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), update=shifted)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertTrue(all(kind == "uncertain" for kind in result.values()))

    def test_no_face_and_one_face_do_not_establish_a_speaker_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory))
            for detections in ([()] * 4, [(), self.face, (), ()]):
                with self.subTest(detections=detections), patch.object(
                        screen_motion.screen_faces, "boxes", side_effect=detections):
                    result = screen_motion.annotate_screen_motion(frames)
                self.assertTrue(all(kind == "uncertain" for kind in result.values()))

    def test_local_support_can_bridge_a_missed_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory))
            with patch.object(screen_motion.screen_faces, "boxes",
                              side_effect=[self.face, (), self.face, self.face]):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertEqual(result[1], "speaker")

    def test_distant_face_detections_cannot_support_a_current_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), timestamps=[0, 1, 60, 61])
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertTrue(all(kind == "uncertain" for kind in result.values()))

    def test_large_camera_view_does_not_supply_a_bounded_side_pane(self):
        def full_camera(draw, index):
            draw.rectangle((0, 0, 639, 359), fill="#402080")
            draw.rectangle((250 + index, 130, 390 + index, 310), fill="#e0b080")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), update=full_camera)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertTrue(all(kind == "uncertain" for kind in result.values()))

    def test_read_failure_and_recognition_warning_stay_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory))
            frames[1].path.unlink()
            frames[3].ocr_warning = "Recognition failed."
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                result = screen_motion.annotate_screen_motion(frames)
            self.assertTrue(all(kind == "uncertain" for kind in result.values()))

    def test_pipeline_uses_supported_pane_motion_before_ocr_text_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory))
            frames[1].lines = ("Recognition varies while the image stays still",)
            before = [(f.index, f.timestamp, f.path) for f in frames]
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                visual_states.annotate_change_kinds(frames)
            self.assertEqual([f.change_kind for f in frames],
                             ["content", "speaker", "speaker", "speaker"])
            self.assertEqual([(f.index, f.timestamp, f.path) for f in frames], before)

    def test_pipeline_keeps_recognition_and_image_read_failures_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory))
            frames[1].ocr_warning = "Recognition failed."
            frames[2].path.unlink()
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                visual_states.annotate_change_kinds(frames)
            self.assertEqual([f.change_kind for f in frames[1:]],
                             ["uncertain", "uncertain", "uncertain"])
            self.assertEqual(len(frames), 4)

    def test_pipeline_preserves_small_new_code_outside_a_supported_pane(self):
        def code_update(draw, index):
            if index:
                draw.text((300, 100), "count += 1", fill="black")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), update=code_update)
            for frame in frames[1:]:
                frame.lines = ("Architecture", "count += 1")
            with patch.object(screen_motion.screen_faces, "boxes", return_value=self.face):
                visual_states.annotate_change_kinds(frames)
            self.assertIn(frames[1].change_kind, {"build", "content", "uncertain"})
            self.assertEqual(frames[2].change_kind, "speaker")

    def test_unreadable_pointer_sized_change_with_the_same_text_is_transient(self):
        def pointer(draw, index):
            if index == 1:
                draw.rectangle((300, 100, 302, 108), fill="black")
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), update=pointer)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=()):
                visual_states.annotate_change_kinds(frames)
            self.assertNotIn(frames[1].change_kind, {"build", "content"})
            self.assertEqual(len(frames), 4)


class DisplayEvidenceTests(unittest.TestCase):
    def frames(self, directory, *, edit=None):
        frames = []
        for index in range(9):
            image = Image.new("RGB", (1280, 720), "#403071")
            draw = ImageDraw.Draw(image)
            draw.rectangle((55, 85, 954, 580), fill="white")
            draw.text((96, 124), "Active display", fill="black")
            draw.rectangle((1000, 300, 1240, 570), fill=(15 * index, 50, 100))
            path = Path(directory) / f"display-{index}.png"
            lines = ("Active display", f"Recognized changing foreground text {index}")
            if edit is not None:
                lines = edit(draw, index, lines)
            image.save(path)
            frames.append(Frame(index, index * 3.5, path, lines=lines))
        return frames

    def test_recurring_display_outranks_text_from_motion_outside_it(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(directory)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=()):
                visual_states.annotate_change_kinds(frames)
            self.assertTrue(all(frame.change_kind == "layout" for frame in frames[1:]))
            self.assertTrue(all("display stayed the same" in frame.change_reason for frame in frames[1:]))
            self.assertEqual([(frame.index, frame.timestamp) for frame in frames],
                             [(index, index * 3.5) for index in range(9)])

    def test_native_display_evidence_vetoes_a_compact_glyph_and_colored_mark(self):
        def add_mark(draw, index, lines):
            if index >= 2:
                # A five-pixel stroke is largely lost in a 320 px signature.
                draw.line((450, 350, 454, 350), fill=(255, 0, 0))
            if index >= 5:
                draw.line((575, 380, 575, 388), fill="black")
            return lines

        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(directory, edit=add_mark)
            evidence = screen_viewport.DisplayEvidence(frames)
            self.assertTrue(evidence.unchanged(1, 0))
            self.assertFalse(evidence.unchanged(2, 0))
            self.assertTrue(evidence.unchanged(3, 2))
            self.assertFalse(evidence.unchanged(5, 2))

    def test_a_new_terminal_result_in_the_display_stays_eligible(self):
        result = "Task completed with 27 accepted items and zero errors"

        def command_result(draw, index, lines):
            if index >= 4:
                draw.rectangle((215, 230, 835, 460), fill="#242424")
                draw.text((250, 300), result, fill="white")
                return lines + (result,)
            return lines

        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(directory, edit=command_result)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=()):
                visual_states.annotate_change_kinds(frames)
            self.assertIn(frames[4].change_kind, {"build", "content", "uncertain"})
            self.assertEqual(frames[5].change_kind, "layout")

    def test_side_whiteboard_strokes_stay_eligible(self):
        def side_diagram(draw, index, lines):
            draw.rectangle((992, 100, 1248, 250), fill="white")
            if index >= 4:
                draw.line(((1020, 185), (1075, 155), (1120, 192), (1220, 125)), fill="black", width=4)
                draw.text((1030, 205), "Confirmed response after replay", fill="black")
            return lines

        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(directory, edit=side_diagram)
            with patch.object(screen_motion.screen_faces, "boxes", return_value=()):
                visual_states.annotate_change_kinds(frames)
            self.assertEqual(frames[3].change_kind, "layout")
            self.assertEqual(frames[4].change_kind, "content")
            self.assertEqual(frames[4].change_reason, visual_states.SIDE_DETAIL_REASON)
            self.assertEqual(frames[5].change_kind, "layout")

    def test_repeated_result_change_on_existing_side_console_stays_eligible(self):
        def text_at(index):
            return "Result: 0 success" if index < 4 else "Result: build 42 passed"

        def side_console(draw, index, lines):
            draw.rectangle((982, 107, 1255, 239), fill="#22242a")
            draw.text((1000, 165), text_at(index), fill="white")
            return lines + (text_at(index),)

        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(directory, edit=side_console)
            for frame in frames:
                frame.ocr_layout = (
                    {"text": "Active display", "bbox": (0.07, 0.17, 0.11, 0.03)},
                    {"text": text_at(frame.index), "bbox": (0.78, 0.22, 0.16, 0.04)},
                )
            with patch.object(screen_motion.screen_faces, "boxes", return_value=()):
                visual_states.annotate_change_kinds(frames)
            self.assertEqual(frames[4].change_kind, "content")
            self.assertEqual(frames[5].change_kind, "layout")

    def test_display_accepts_unicode_image_paths_and_empty_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "frame-\u00e9.png"
            Image.new("RGB", (32, 16)).save(image)
            empty = root / "empty.png"
            empty.touch()
            evidence = screen_viewport.DisplayEvidence([])
            self.assertEqual(evidence._read(image).shape, (16, 32, 3))
            self.assertIsNone(evidence._read(empty))
            self.assertIsNone(evidence._read(root / "missing.png"))

    def test_room_landmarks_preserve_projection_scope_before_it_lights_up(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = []
            for index in range(21):
                room = Image.new("RGB", (1280, 720), "#24262a")
                draw = ImageDraw.Draw(room)
                draw.rectangle((27, 24, 1262, 110), outline="#dddadd", width=4)
                draw.rectangle((73, 532, 440, 687), outline="#aaa9a7", width=4)
                for left in (42, 214, 1010, 1132):
                    draw.rectangle((left, 152, left + 65, 488), outline="#adaab8", width=10)
                for y in (41, 72, 556, 614):
                    draw.line((68, y, 1230, y), fill="#c6c2ca", width=3)
                draw.rectangle((385, 173, 835, 432), fill="#fcf9ef" if index >= 10 else "#24262a")
                if index in (2, 3):
                    draw.line((503, 254, 507, 254), fill="#0612eb")
                draw.rectangle((931 + index % 3 * 5, 540, 973, 579), fill=(50 + 7 * index, 35, 37))
                if index == 4:
                    room.paste("#3a383a", (0, 0, 1280, 720))
                    ImageDraw.Draw(room).line((0, 0, 1280, 720), fill="#bcbcbc", width=19)
                path = Path(directory) / f"room-{index}.png"
                room.save(path)
                frames.append(Frame(index, 8.0 * index, path))

            evidence = screen_viewport.DisplayEvidence(frames)
            self.assertTrue(evidence.projector[0])
            self.assertTrue(evidence.unchanged(1, 0))
            self.assertFalse(evidence.unchanged(2, 0), "The projected glyph is new content")
            self.assertFalse(evidence.unchanged(10, 9), "Projection lighting may introduce content")
            self.assertIsNone(evidence.regions[4], "A different camera must supply its own evidence")


if __name__ == "__main__":
    unittest.main()
