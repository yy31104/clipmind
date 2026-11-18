import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from clipmind.media import Frame
from clipmind.screen_projection import _same_projection, unchanged_projection_indices
from clipmind.visual_states import annotate_change_kinds


class ProjectedScreenTests(unittest.TestCase):
    def test_registration_keeps_a_small_colored_change(self):
        canvas = np.full((220, 350, 3), 128, np.uint8)
        cv2.putText(canvas, "def tick():", (30, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 25, 40), 2)
        cv2.rectangle(canvas, (20, 110), (290, 164), (100, 170, 130), 2)
        shift = np.float32([[1, 0, 1], [0, 1, -1]])
        brighter = np.uint8(np.clip(cv2.warpAffine(canvas, shift, (350, 220)) * 0.94 + 15, 0, 255))
        self.assertTrue(_same_projection(canvas, brighter, cv2))
        # This changes blue in just eight native pixels. A grayscale or area
        # threshold alone can miss the change.
        brighter[127:129, 140:144, 0] += 36
        self.assertFalse(_same_projection(canvas, brighter, cv2))

    def test_requires_a_recurring_rim_and_preserves_the_projected_change(self):
        with tempfile.TemporaryDirectory() as folder:
            frames = []
            corners = np.int32([[246, 78], [747, 90], [751, 410], [237, 400]])
            for index in range(7):
                image = np.full((540, 960, 3), 174, np.uint8)
                cv2.fillConvexPoly(image, corners, (195, 201, 203))
                cv2.polylines(image, [corners], True, (31, 34, 33), 8)
                cv2.rectangle(image, (345, 272), (533, 325), (66, 102, 55), 2)
                cv2.putText(image, "Projected page", (315, 155),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (38, 39, 42), 2)
                if index >= 4:
                    cv2.rectangle(image, (537, 217), (549, 221), (142, 201, 203), -1)
                cv2.ellipse(image, (63 + index * 15, 432), (35, 90), 0, 0, 360, (61, 48, 79), -1)
                path = Path(folder) / f"room-\u6703-{index}.png"
                encoded, data = cv2.imencode(".png", image)
                self.assertTrue(encoded)
                path.write_bytes(data.tobytes())
                frame = Frame(index, float(index), path)
                frame.change_kind = "content"
                frames.append(frame)

            self.assertEqual(unchanged_projection_indices(frames[:5]), set())
            self.assertTrue({1, 2, 3}.issubset(unchanged_projection_indices(frames)))
            self.assertNotIn(4, unchanged_projection_indices(frames))
            frames[2].ocr_warning = "recognizer unavailable"
            self.assertNotIn(2, unchanged_projection_indices(frames))
            frames[2].ocr_warning = None
            annotate_change_kinds(frames)
            self.assertIsNone(frames[4].ocr_warning)
            self.assertIn(frames[4].change_kind, {"content", "build", "uncertain"})
            self.assertEqual(frames[3].change_kind, "layout")

    def test_perspective_projection_keeps_new_text_on_a_separate_board(self):
        self._check_new_writing(near_rim=False)

    def test_perspective_projection_keeps_a_new_line_near_its_rim(self):
        self._check_new_writing(near_rim=True)

    def _check_new_writing(self, *, near_rim):
        with tempfile.TemporaryDirectory() as folder:
            frames = []
            corners = np.int32([[246, 78], [747, 101], [751, 410], [234, 393]])
            for index in range(9):
                image = np.full((540, 960, 3), 174, np.uint8)
                cv2.fillConvexPoly(image, corners, (180, 181, 183))
                cv2.polylines(image, [corners], True, (31, 34, 33), 8)
                cv2.rectangle(image, (345, 272), (533, 325), (66, 102, 55), 2)
                cv2.putText(image, "Projected page", (315, 155),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (38, 39, 42), 2)
                if not near_rim:
                    cv2.rectangle(image, (765, 87), (939, 293), (219, 223, 226), -1)
                cv2.ellipse(image, (63 + index * 15, 432), (35, 90), 0, 0, 360, (61, 48, 79), -1)
                if index >= 4 and near_rim:
                    cv2.putText(image, "42 accepted", (375, 100),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (29, 32, 36), 1)
                elif index >= 4:
                    cv2.putText(image, "42 accepted", (775, 180),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.67, (29, 32, 36), 2)
                    cv2.line(image, (787, 239), (886, 210), (31, 34, 41), 3)
                path = Path(folder) / f"{index}.png"
                self.assertTrue(cv2.imwrite(str(path), image))
                frame = Frame(index, index * 3.5, path)
                frame.stable_duration = 3.5
                frame.observed_sample_count = 7
                frame.ocr_layout = ({"text": "Projected page", "confidence": 0.98,
                                     "bbox": (0.32, 0.27, 0.22, 0.035)},)
                if index >= 4:
                    frame.ocr_layout += ({
                        "text": "System reports 42 items accepted and zero errors",
                        "confidence": 0.96,
                        "bbox": (0.39, 0.17, 0.16, 0.025) if near_rim else (0.8, 0.29, 0.16, 0.055),
                    },)
                frame.lines = tuple(block["text"] for block in frame.ocr_layout)
                frames.append(frame)

            annotate_change_kinds(frames)
            self.assertIn(frames[4].change_kind, {"content", "build", "uncertain"})
            self.assertEqual(frames[5].change_kind, "layout")
