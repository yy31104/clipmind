"""Per-video orchestration.

Speech and vision are independent, so they run concurrently and only rejoin at
summarisation. Each stage reports progress so the UI can show real movement
rather than a fake spinner.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import asr, keyframes, media, render, summarize
from .config import settings
from .fetch import FetchError, fetch

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
    if keep_source:
        return
    for path in [workdir / "audio.wav", *workdir.glob("source.*")]:
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
            try:
                item = await fetch(
                    url, workdir,
                    on_note=lambda n: report("fetching", base, n),
                )
            except FetchError as exc:
                raise RuntimeError(str(exc)) from exc
        base += STAGES[0][1]
        report("sampling", base, item.title)

        # --- cheap prep ----------------------------------------------------
        audio_path = await media.extract_audio(item.video_path, workdir / "audio.wav")
        candidates = await media.sample_frames(item.video_path, workdir / "samples")
        unique = media.dedupe(candidates)
        base += STAGES[1][1]
        report("analysing", base,
               f"{len(candidates)} frames -> {len(unique)} unique")

        # --- speech and vision, concurrently ------------------------------
        async def speech():
            async with pools.asr:
                report("analysing", base + 0.05, "transcribing speech")
                return await asr.transcribe(audio_path)

        async def vision():
            problem = await keyframes.annotate(unique, pools.ocr)
            report("analysing", base + 0.05, problem or "reading on-screen text")
            return keyframes.select(unique), problem

        transcript, (chosen, ocr_error) = await asyncio.gather(speech(), vision())
        chosen = await keyframes.promote(
            item.video_path, chosen, workdir / "keyframes"
        )
        base += STAGES[2][1]
        report("summarising", base, f"{len(chosen)} keyframes, "
               f"{len(transcript.segments)} speech segments")

        # --- fuse ----------------------------------------------------------
        summary = await summarize.summarize(
            transcript, chosen, item.title, item.duration, pools.llm)
        base += STAGES[3][1]
        report("writing", base, "writing note")

        metadata = render.write_all(
            workdir, item, transcript, chosen, summary, ocr_error=ocr_error
        )
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
