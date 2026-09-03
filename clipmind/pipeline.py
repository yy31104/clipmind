"""Per-video orchestration.

Speech and vision are independent, so they run concurrently and only rejoin
when the Evidence Pack is written. Each stage reports progress so the UI can
show real movement rather than a fake spinner.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import evidence, media, preflight, render, visual_states
from .config import Settings, settings
from .fetch import fetch
from .providers import ProviderBundle, default_providers

# (label, share of the bar) - fetch and the two analysis legs dominate.
STAGES = (
    ("fetching", 0.20),
    ("sampling", 0.12),
    ("analysing", 0.55),
    ("writing", 0.13),
)


@dataclass
class Pools:
    """Separate limits per resource so one heavy stage cannot starve the rest."""
    fetch: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(settings.max_fetch))
    asr: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(settings.max_asr))
    ocr: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(settings.max_ocr))

    @classmethod
    def from_settings(cls, config: Settings) -> Pools:
        return cls(
            fetch=asyncio.Semaphore(config.max_fetch),
            asr=asyncio.Semaphore(config.max_asr),
            ocr=asyncio.Semaphore(config.max_ocr),
        )


def cleanup_temporary(workdir: Path, keep_source: bool = False) -> None:
    """Remove pipeline-owned temporary files without touching final artifacts."""
    shutil.rmtree(workdir / "samples", ignore_errors=True)
    shutil.rmtree(workdir / "evidence_samples", ignore_errors=True)
    if keep_source:
        return
    for path in [workdir / "audio.wav", *workdir.glob("source.*")]:
        if path.name == "source.json":
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


async def process(
    url: str,
    workdir: Path,
    pools: Pools,
    report,
    *,
    config: Settings | None = None,
    providers: ProviderBundle | None = None,
    options: dict | None = None,
) -> dict:
    """Run one video end to end. ``report(stage, progress, note)`` drives the UI."""
    config = config or settings
    workdir.mkdir(parents=True, exist_ok=True)
    base = 0.0
    completed = False
    timings: dict[str, float] = {}
    providers = providers or default_providers(config)
    options = dict(options or {})

    try:
        # --- acquire -------------------------------------------------------
        report("fetching", base, "resolving link")
        stage_started = time.perf_counter()
        async with pools.fetch:
            item = await fetch(
                url, workdir,
                on_note=lambda n: report("fetching", base, n),
                config=config,
            )
        timings["acquisition_seconds"] = round(time.perf_counter() - stage_started, 3)
        base += STAGES[0][1]
        report("sampling", base, item.title)

        # --- cheap prep ----------------------------------------------------
        stage_started = time.perf_counter()
        candidates = await media.sample_frames(
            item.video_path,
            workdir / "samples",
            fps=config.sample_fps,
            width=config.sample_width,
        )
        unique = media.dedupe(candidates, threshold=config.dedupe_threshold)
        estimate = preflight.estimate(
            item.duration,
            candidates,
            unique,
            config,
            forced=bool(options.get("force")),
        )
        preflight.write(workdir, estimate)
        report(
            "preflight",
            base,
            f"preflight: ~{estimate.estimated_canonical_states} states, "
            f"~{estimate.estimated_ocr_seconds:g}s OCR, "
            f"~{estimate.estimated_pack_mb:g} MB",
        )
        if not estimate.within_budget and not estimate.forced:
            raise preflight.CostLimitExceeded(estimate)

        audio_path = await media.extract_audio(item.video_path, workdir / "audio.wav")
        evidence_candidates = await media.sample_frames(
            item.video_path,
            workdir / "evidence_samples",
            fps=config.sample_fps,
            width=config.evidence_width,
        )
        canonical = visual_states.retain_all(
            unique,
            workdir / "visual_states" / "all",
            evidence_sources={
                frame.index: frame.path for frame in evidence_candidates
            },
        )
        timings["sampling_seconds"] = round(time.perf_counter() - stage_started, 3)
        dedupe_failure_count = sum(
            frame.dedupe_warning is not None for frame in canonical
        )
        base += STAGES[1][1]
        diagnostic = (
            f", {dedupe_failure_count} dedupe warning(s)"
            if dedupe_failure_count else ""
        )
        report(
            "analysing",
            base,
            f"{len(candidates)} frames -> {len(canonical)} canonical states{diagnostic}",
        )

        # --- speech and vision, concurrently ------------------------------
        async def speech():
            async with pools.asr:
                report("analysing", base + 0.05, "transcribing speech")
                started = time.perf_counter()
                value = await providers.transcript.transcribe(audio_path)
                return value, round(time.perf_counter() - started, 3)

        async def vision():
            started = time.perf_counter()
            problem = await visual_states.annotate(
                canonical, pools.ocr, providers.text
            )
            ocr_seconds = round(time.perf_counter() - started, 3)
            report("analysing", base + 0.05, problem or "reading on-screen text")
            build_groups = visual_states.group_progressive_builds(canonical)
            return build_groups, problem, ocr_seconds

        (transcript, asr_seconds), (
            build_groups,
            ocr_error,
            ocr_seconds,
        ) = await asyncio.gather(speech(), vision())
        stage_started = time.perf_counter()
        spoken = tuple(
            (segment.start, segment.end, segment.text)
            for segment in transcript.segments
        )
        # Measured on canonical states, never used to decide canonical
        # membership: an ASR or OCR slip must not be able to delete evidence.
        visual_states.annotate_transcript_alignment(canonical, spoken)
        preview = visual_states.materialize_preview(
            visual_states.derive_preview(canonical, spoken_intervals=spoken),
            workdir / "visual_states" / "preview",
        )
        preview_seconds = round(time.perf_counter() - stage_started, 3)
        timings.update(
            asr_seconds=asr_seconds,
            ocr_seconds=ocr_seconds,
            preview_seconds=preview_seconds,
        )
        evidence_manifest = evidence.write_pack(
            workdir,
            item,
            transcript,
            canonical,
            preview,
            build_groups,
            candidate_frame_count=len(candidates),
            ocr_error=ocr_error,
            timings=timings,
            config=config,
            preflight_result=estimate.public(),
        )
        base += STAGES[2][1]
        report("writing", base, f"{len(preview)} preview states, "
               f"{len(transcript.segments)} speech segments")

        try:
            metadata = render.write_all(
                workdir,
                item,
                transcript,
                ocr_error=ocr_error,
                visual_states=canonical,
                visual_preview=preview,
                build_groups=build_groups,
                candidate_frame_count=len(candidates),
                evidence_manifest=evidence_manifest,
                stage_timings=timings,
                preflight_result=estimate.public(),
            )
        except Exception as exc:  # noqa: BLE001 - legacy output is non-canonical
            metadata = render.build_metadata(
                workdir,
                item,
                transcript,
                ocr_error=ocr_error,
                visual_states=canonical,
                visual_preview=preview,
                build_groups=build_groups,
                candidate_frame_count=len(candidates),
                evidence_manifest=evidence_manifest,
                stage_timings=timings,
                preflight_result=estimate.public(),
            )
            metadata["compatibility_error"] = f"{type(exc).__name__}: {exc}"
        report("done", 1.0, "complete")
        completed = True
        return metadata
    finally:
        try:
            cleanup_temporary(
                workdir,
                keep_source=completed and config.keep_source_video,
            )
        except Exception:  # noqa: BLE001 - cleanup must not replace the root error
            pass
