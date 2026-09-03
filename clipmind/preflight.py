"""Cheap complete-pack cost estimation performed before expensive analysis."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Settings
from .media import Frame


class CostLimitExceeded(RuntimeError):
    code = "cost_limit_exceeded"
    user_message = "This source exceeds the configured local processing budget."
    action = "Review the estimate, then choose Process anyway to extract it completely."

    def __init__(self, estimate: CostEstimate) -> None:
        super().__init__(self.user_message)
        self.details = estimate.public()


@dataclass(frozen=True)
class CostEstimate:
    duration_seconds: float
    candidate_frame_count: int
    estimated_canonical_states: int
    estimated_ocr_seconds: float
    estimated_pack_mb: float
    limits: dict[str, float | int]
    exceeded: tuple[str, ...]
    forced: bool = False

    @property
    def within_budget(self) -> bool:
        return not self.exceeded

    def public(self) -> dict:
        return {
            **asdict(self),
            "exceeded": list(self.exceeded),
            "within_budget": self.within_budget,
            "policy": "complete_or_refuse",
        }


def estimate(
    duration: float,
    candidates: list[Frame],
    canonical_candidates: list[Frame],
    config: Settings,
    *,
    forced: bool = False,
) -> CostEstimate:
    states = len(canonical_candidates)
    ocr_seconds = round(states * config.estimated_ocr_seconds_per_state, 1)
    pack_mb = round(states * config.estimated_pack_mb_per_state, 1)
    exceeded: list[str] = []
    if states > config.max_canonical_states:
        exceeded.append("canonical_states")
    if ocr_seconds > config.max_estimated_ocr_seconds:
        exceeded.append("ocr_seconds")
    if pack_mb > config.max_estimated_pack_mb:
        exceeded.append("pack_mb")
    return CostEstimate(
        duration_seconds=round(max(duration, 0.0), 3),
        candidate_frame_count=len(candidates),
        estimated_canonical_states=states,
        estimated_ocr_seconds=ocr_seconds,
        estimated_pack_mb=pack_mb,
        limits={
            "canonical_states": config.max_canonical_states,
            "ocr_seconds": config.max_estimated_ocr_seconds,
            "pack_mb": config.max_estimated_pack_mb,
        },
        exceeded=tuple(exceeded),
        forced=forced,
    )


def write(dest: Path, value: CostEstimate) -> None:
    (dest / "preflight.json").write_text(
        json.dumps(value.public(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
