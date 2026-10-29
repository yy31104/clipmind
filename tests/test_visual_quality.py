from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_visual_quality import cases, evaluate


class VisualQualityTests(unittest.TestCase):
    def test_labeled_information_coverage_and_redundancy(self):
        with tempfile.TemporaryDirectory() as temp:
            report = evaluate(cases(Path(temp)))
        for case in report["cases"]:
            with self.subTest(case=case["case"]):
                self.assertEqual(case["recalled_groups"], case["required_groups"])
                self.assertEqual(case["redundant_selected"], 0)
                self.assertEqual(case["irrelevant_selected"], 0)
                self.assertTrue(case["source_images_unchanged"])


if __name__ == "__main__":
    unittest.main()
