from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clipmind.config import Settings
from clipmind.media import Frame
from clipmind.preflight import CostLimitExceeded, analysis_time_range, estimate


class PreflightCostTests(unittest.TestCase):
    def frames(self, count: int) -> list[Frame]:
        return [Frame(index, float(index), Path(f"frame-{index}.jpg")) for index in range(count)]

    def test_cost_follows_visual_complexity_not_duration_alone(self) -> None:
        config = Settings()
        candidates = self.frames(200)
        canonical = self.frames(40)

        short = estimate(60, candidates, canonical, config)
        long = estimate(7200, candidates, canonical, config)

        self.assertEqual(short.estimated_canonical_states, long.estimated_canonical_states)
        self.assertEqual(short.estimated_ocr_seconds, long.estimated_ocr_seconds)
        self.assertEqual(short.estimated_pack_mb, long.estimated_pack_mb)
        self.assertTrue(long.within_budget)

    def test_each_configured_limit_is_reported_independently(self) -> None:
        config = Settings(
            max_canonical_states=2,
            max_estimated_ocr_seconds=0.1,
            max_estimated_pack_mb=0.1,
            estimated_ocr_seconds_per_state=1.0,
            estimated_pack_mb_per_state=1.0,
        )
        value = estimate(10, self.frames(4), self.frames(3), config)

        self.assertEqual(
            value.exceeded,
            ("canonical_states", "ocr_seconds", "pack_mb"),
        )
        error = CostLimitExceeded(value)
        self.assertEqual(error.code, "cost_limit_exceeded")
        self.assertEqual(error.details["policy"], "complete_or_refuse")
        self.assertFalse(error.details["within_budget"])

    def test_time_range_uses_selected_backend_and_includes_cold_start_caveat(self) -> None:
        candidates, canonical = self.frames(120), self.frames(30)
        config = Settings()
        native = estimate(600, candidates, canonical, config,
                          asr_provider="mlx-whisper", ocr_provider="apple-vision")
        portable = estimate(600, candidates, canonical, config,
                            asr_provider="faster-whisper", ocr_provider="tesseract")
        self.assertLess(native.estimated_analysis_seconds[1], portable.estimated_analysis_seconds[1])
        self.assertGreater(portable.estimated_analysis_seconds[1], 600)
        self.assertEqual(portable.public()["estimated_analysis_seconds"], list(portable.estimated_analysis_seconds))
        self.assertIn("CPU", portable.time_estimate_basis)
        self.assertIn("model downloads are extra", portable.time_message())
        self.assertIn("one active video", portable.time_message())
        self.assertEqual(native.exceeded, portable.exceeded)

    def test_time_range_accounts_for_visual_complexity_without_rewriting_cost_limits(self) -> None:
        low, _ = analysis_time_range(1, 1, 2, Settings(),
                                    asr_provider="faster-whisper", ocr_provider="tesseract")
        high, _ = analysis_time_range(1, 1000, 2000, Settings(),
                                     asr_provider="faster-whisper", ocr_provider="tesseract")
        self.assertGreater(high[1], low[1])
        unknown = estimate(0, [], [], Settings())
        self.assertIsNone(unknown.estimated_analysis_seconds)
        self.assertIn("unknown", unknown.time_message())

    def test_cpu_planning_range_includes_the_measured_full_development_talk(self) -> None:
        # Published-image CPU run: 39:24 source, 7:31 extraction, warm models,
        # six CPU cores and 4 GiB. Model download is measured separately.
        interval, _ = analysis_time_range(2364.52, 379, 4729, Settings(),
                                         asr_provider="faster-whisper", ocr_provider="tesseract")
        self.assertLessEqual(interval[0], 450.892)
        self.assertGreaterEqual(interval[1], 450.892)


if __name__ == "__main__":
    unittest.main()
