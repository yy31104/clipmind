"""Per-video orchestration.

Speech and vision are independent, so they run concurrently and only rejoin at
summarisation. Each stage reports progress so the UI can show real movement
rather than a fake spinner.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import asr, evidence, keyframes, media, render, summarize, visual_states
from .config import settings
from .fetch import fetch

# (label, share of the bar) - fetch and the two analysis legs dominate.
STAGES = (
    ("fetching", 0.20),
    ("sampling", 0.12),
    ("analysing", 0.50),
    ("summarising", 0.15),
    ("writing", 0.03),
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
    llm: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(settings.max_llm))


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


async def process(url: str, workdir: Path, pools: Pools, report) -> dict:
    """Run one video end to end. ``report(stage, progress, note)`` drives the UI."""
    workdir.mkdir(parents=True, exist_ok=True)
    base = 0.0
    completed = False

    try:
        # --- acquire -------------------------------------------------------
        report("fetching", base, "resolving link")
        async with pools.fetch:
            item = await fetch(
                url, workdir,
                on_note=lambda n: report("fetching", base, n),
            )
        base += STAGES[0][1]
        report("sampling", base, item.title)

        # --- cheap prep ----------------------------------------------------
        audio_path = await media.extract_audio(item.video_path, workdir / "audio.wav")
        candidates = await media.sample_frames(item.video_path, workdir / "samples")
        unique = media.dedupe(candidates)
        evidence_candidates = await media.sample_frames(
            item.video_path,
            workdir / "evidence_samples",
            width=settings.evidence_width,
        )
        canonical = visual_states.retain_all(
            unique,
            workdir / "visual_states" / "all",
            evidence_sources={
                frame.index: frame.path for frame in evidence_candidates
            },
        )
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
                return await asr.transcribe(audio_path)

        async def vision():
            problem = await keyframes.annotate(canonical, pools.ocr)
            report("analysing", base + 0.05, problem or "reading on-screen text")
            build_groups = visual_states.group_progressive_builds(canonical)
            preview = visual_states.materialize_preview(
                visual_states.derive_preview(canonical),
                workdir / "visual_states" / "preview",
            )
            preview_candidates = [replace(frame) for frame in canonical]
            return keyframes.select(preview_candidates), preview, build_groups, problem

        transcript, (chosen, preview, build_groups, ocr_error) = await asyncio.gather(
            speech(), vision()
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
        )
        chosen = await keyframes.promote(
            item.video_path, chosen, workdir / "keyframes"
        )
        base += STAGES[2][1]
        report("summarising", base, f"{len(preview)} preview states, "
               f"{len(transcript.segments)} speech segments")

        # --- fuse ----------------------------------------------------------
        try:
            summary = await summarize.summarize(
                transcript, chosen, item.title, item.duration, pools.llm
            )
        except Exception as exc:  # noqa: BLE001 - optional compatibility view
            summary = summarize.Summary(
                "## Evidence Pack\n\nCanonical evidence was written to `evidence.md`.",
                "compatibility fallback",
                error=f"{type(exc).__name__}: {exc}",
            )
        base += STAGES[3][1]
        report("writing", base, "writing note")

        try:
            metadata = render.write_all(
                workdir,
                item,
                transcript,
                chosen,
                summary,
                ocr_error=ocr_error,
                visual_states=canonical,
                visual_preview=preview,
                build_groups=build_groups,
                candidate_frame_count=len(candidates),
                evidence_manifest=evidence_manifest,
            )
        except Exception as exc:  # noqa: BLE001 - legacy output is non-canonical
            metadata = {
                "id": item.video_id,
                "title": item.title,
                "uploader": item.uploader,
                "duration": item.duration,
                "url": item.webpage_url,
                "visual_preview": [
                    {
                        "timestamp": frame.timestamp,
                        "clock": render.clock(frame.timestamp),
                        "file": frame.path.relative_to(workdir).as_posix(),
                        "text": frame.text,
                    }
                    for frame in preview
                ],
                "evidence_pack": {
                    "manifest": "manifest.json",
                    "evidence": "evidence.md",
                    "schema": evidence_manifest["schema"],
                    "completeness": evidence_manifest["completeness"],
                },
                "compatibility_error": f"{type(exc).__name__}: {exc}",
            }
        report("done", 1.0, "complete")
        completed = True
        return metadata
    finally:
        try:
            cleanup_temporary(
                workdir,
                keep_source=completed and settings.keep_source_video,
            )
        except Exception:  # noqa: BLE001 - cleanup must not replace the root error
            pass
