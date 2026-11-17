from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from clipmind import screen_motion, visual_states
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


if __name__ == "__main__":
    unittest.main()
