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
                path = Path(folder) / f"會場-{index}.png"
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
