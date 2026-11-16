from __future__ import annotations

import unittest

import numpy as np

from clipmind import media


class StationaryStrokesTests(unittest.TestCase):
    def image(self):
        return np.zeros((180, 320), dtype=np.int16)

    def test_persistent_code_stroke_remains_visible(self):
        before = self.image()
        current = before.copy()
        current[40:50, 65:75] = 160
        self.assertTrue(media._persistent_detail(before, current, current.copy()))

    def test_a_moving_brightening_region_is_not_a_stationary_stroke(self):
        before = self.image()
        current, following = before.copy(), before.copy()
        current[40:60, 65:85] = 96
        following[40:60, 69:89] = 192
        self.assertFalse(media._persistent_detail(before, current, following))

    def test_eighty_percent_stationary_pixels_are_required(self):
        before = self.image()
        current = before.copy()
        current[40:50, 65:75] = 160
        following = current.copy()
        following[40:42, 65:75] = 0
        self.assertTrue(media._persistent_detail(before, current, following))
        following[42, 65] = 0
        self.assertFalse(media._persistent_detail(before, current, following))

    def test_stationary_tolerance_includes_twelve_but_not_thirteen(self):
        before = self.image()
        current = before.copy()
        current[40:50, 65:75] = 160
        following = current.copy()
        following[40:50, 65:75] += 12
        self.assertTrue(media._persistent_detail(before, current, following))
        following[40:50, 65:75] += 1
        self.assertFalse(media._persistent_detail(before, current, following))

    def test_final_sample_stays_conservative(self):
        before = self.image()
        current = before.copy()
        current[40:50, 65:75] = 160
        self.assertTrue(media._persistent_detail(before, current, None))

    def test_activation_minimum_is_unchanged(self):
        before = self.image()
        current = before.copy()
        current[40:43, 65:84] = 160
        self.assertFalse(media._persistent_detail(before, current, current.copy()))
        current[43, 65] = 160
        self.assertTrue(media._persistent_detail(before, current, current.copy()))


if __name__ == "__main__":
    unittest.main()
