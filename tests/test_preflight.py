from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clipmind.config import Settings
from clipmind.media import Frame
from clipmind.preflight import CostLimitExceeded, estimate


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


if __name__ == "__main__":
    unittest.main()
