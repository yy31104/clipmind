"""Cheap complete-pack cost estimation performed before expensive analysis."""
from __future__ import annotations

import json
import math
import platform
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
    estimated_analysis_seconds: tuple[int, int] | None = None
    time_estimate_basis: str = ""
    time_estimate_note: str = ""

    @property
    def within_budget(self) -> bool:
        return not self.exceeded

    def public(self) -> dict:
        return {
            **asdict(self),
            "exceeded": list(self.exceeded),
            "within_budget": self.within_budget,
            "policy": "complete_or_refuse",
            "estimated_analysis_seconds": list(self.estimated_analysis_seconds)
                if self.estimated_analysis_seconds else None,
        }

    def time_message(self) -> str:
        if self.estimated_analysis_seconds is None:
            return "Analysis time is unknown until the source duration is available."
        lower, upper = self.estimated_analysis_seconds
        if upper < 60:
            interval = f"{lower}–{upper} seconds"
        else:
            interval = f"{max(1, math.ceil(lower / 60))}–{math.ceil(upper / 60)} minutes"
        return f"Estimated analysis time: about {interval}. {self.time_estimate_note}"


def analysis_time_range(duration: float, states: int, candidates: int, config: Settings,
                        *, asr_provider: str | None, ocr_provider: str | None) -> tuple[tuple[int, int] | None, str]:
    """A broad planning range, not a hardware benchmark or a completion timer.

    Speech and OCR overlap in the pipeline, so use their maximum rather than
    adding both durations. Portable speech assumes CPU; it makes no CUDA claim.
    Acquisition, model downloads, queue time and competing workloads are outside
    this range. Keep this separate from the existing complete-pack cost limits.
    """
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    asr = asr_provider or config.asr_provider
    ocr = ocr_provider or config.ocr_provider
    native_speech = "mlx" in asr or asr == "auto" and apple
    native_text = "vision" in ocr or ocr == "auto" and apple
    basis = ("Apple Silicon MLX" if native_speech else "portable ASR on CPU") + ", " + (
        "Apple Vision" if native_text else "Tesseract/portable OCR")
    if not math.isfinite(duration) or duration <= 0:
        return None, basis
    speech = ((duration * .02 + 3, duration * .25 + 15) if native_speech else
              (duration * .1 + 5, duration * 3 + 30))
    workers = max(1, config.max_ocr)
    text = ((states * .03 / workers, states * .7 / workers) if native_text else
            (states * .2 / workers, states * 4 / workers))
    encoding = (candidates * .005 + 2, candidates * .04 + 10)
    lower = math.ceil(max(speech[0], text[0]) + encoding[0])
    upper = math.ceil(max(speech[1], text[1]) + encoding[1])
    return (lower, max(lower, upper)), basis


def estimate(
    duration: float,
    candidates: list[Frame],
    canonical_candidates: list[Frame],
    config: Settings,
    *,
    forced: bool = False,
    asr_provider: str | None = None,
    ocr_provider: str | None = None,
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
    time_range, time_basis = analysis_time_range(duration, states, len(candidates), config,
                                               asr_provider=asr_provider, ocr_provider=ocr_provider)
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
        estimated_analysis_seconds=time_range,
        time_estimate_basis=time_basis,
        time_estimate_note="Assumes one active video. Acquisition and model downloads are extra; system load can extend this range.",
    )


def write(dest: Path, value: CostEstimate) -> None:
    (dest / "preflight.json").write_text(
        json.dumps(value.public(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
