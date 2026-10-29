"""Per-video orchestration.

Speech and vision are independent, so they run concurrently and only rejoin
when the Evidence Pack is written. Each stage reports progress so the UI can
show real movement rather than a fake spinner.
"""
from __future__ import annotations

import asyncio
import math
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote

from . import acquisition, evidence, image_media, media, preflight, render, visual_content, visual_states
from .asr import Transcript
from .async_utils import gather_joined
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


def _apply_uploaded_filename(item, options: dict) -> None:
    """Replace randomized upload storage names with safe user-facing provenance."""
    uploaded = Path(str(options.get("uploaded_filename") or "")).name
    if not uploaded:
        return
    title = Path(uploaded).stem.strip()
    if title:
        item.info["title"] = title
    item.info["webpage_url"] = f"local:///{quote(uploaded)}"


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
    """Remove pipeline-owned temporary files without touching final artifacts.

    This is the single cleanup contract. Completion, failure, cancellation and
    restart recovery all arrive here, and only the first three still hold the
    ``MediaAsset``, so what acquisition owns is read back from disk rather than
    from the caller.
    """
    shutil.rmtree(workdir / "samples", ignore_errors=True)
    shutil.rmtree(workdir / "evidence_samples", ignore_errors=True)
    if keep_source:
        return
    acquisition.purge(workdir)
    # Job directories written before acquisition owned its own directory still
    # hold media at the top level, next to the Evidence Pack's ``source.json``.
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
        _apply_uploaded_filename(item, options)
        timings["acquisition_seconds"] = round(time.perf_counter() - stage_started, 3)
        base += STAGES[0][1]
        report("sampling", base, item.title)

        # --- cheap prep ----------------------------------------------------
        stage_started = time.perf_counter()
        image_paths = tuple(getattr(item, "image_paths", ()))
        image_motion_paths = tuple(getattr(item, "image_motion_paths", ()))
        if image_motion_paths and len(image_motion_paths) != len(image_paths):
            raise media.MediaError("Image motion pages must match the original image order.")
        has_image_motion = any(path is not None for path in image_motion_paths)
        audio_only = getattr(item, "media_type", "video") == "audio"
        if image_paths:
            candidates = await image_media.sample_collection(
                image_paths, image_motion_paths, workdir / "samples" / "image_motion",
                fps=config.sample_fps, width=config.sample_width,
            ) if has_image_motion else image_media.candidates(image_paths)
            unique = candidates
        elif audio_only:
            candidates = []
            unique = []
        else:
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
        if image_paths:
            # Unlike sampled JPEGs, original photo pages can be very large.
            # Count both canonical and preview copies and round upward, while
            # retaining the same complete-or-refuse/explicit-force contract.
            original_pack_mb = math.ceil(
                2 * sum(path.stat().st_size for path in image_paths) / 100_000
            ) / 10
            motion_states = sum(frame.source_motion_timestamp is not None for frame in candidates)
            motion_pack_mb = motion_states * config.estimated_pack_mb_per_state
            pack_mb = max(estimate.estimated_pack_mb, round(original_pack_mb + motion_pack_mb, 3))
            exceeded = estimate.exceeded
            if pack_mb > config.max_estimated_pack_mb and "pack_mb" not in exceeded:
                exceeded = (*exceeded, "pack_mb")
            estimate = replace(estimate, estimated_pack_mb=pack_mb, exceeded=exceeded)
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

        if image_paths:
            audio_path = None
            if has_image_motion:
                high_resolution = await image_media.sample_collection(
                    image_paths, image_motion_paths,
                    workdir / "evidence_samples" / "image_motion",
                    fps=config.sample_fps, width=config.evidence_width,
                )
                identity = lambda frames: [
                    (frame.index, frame.source_image_index, frame.source_motion_timestamp)
                    for frame in frames
                ]
                if identity(unique) != identity(high_resolution):
                    raise media.MediaError("Motion evidence samples did not match the preflight frame order; no partial pack was published.")
                unique = high_resolution
            canonical = image_media.retain_all(unique, workdir / "visual_states" / "all")
        elif audio_only:
            audio_path = await media.extract_audio(item.media_path, workdir / "audio.wav")
            (workdir / "visual_states" / "all").mkdir(parents=True, exist_ok=True)
            canonical = []
        else:
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
        # An absent track is a source modality, not a model failure. In
        # particular, do not import/load Whisper for an image post or silent
        # video, and do not fabricate words/times from its OCR text.
        if image_paths:
            item.info["_clipmind_media_type"] = "image_collection"
            item.info["_clipmind_time_basis"] = "image_order"
            item.info["_clipmind_source_image_count"] = len(image_paths)
            item.info["_clipmind_source_motion_count"] = sum(
                path is not None for path in image_motion_paths
            )
            if has_image_motion:
                item.info["_clipmind_motion_sample_fps"] = config.sample_fps
                item.info["_clipmind_motion_audio_status"] = "not_acquired"
                item.info["_clipmind_motion_time_basis"] = "clip_relative_seconds"
            item.info["_clipmind_audio_status"] = (
                "not_applicable" if item.platform == "local" else "not_acquired"
            )
        else:
            item.info["_clipmind_audio_status"] = "present" if audio_path else "absent"
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
            if audio_path is None:
                report("analysing", base + 0.05,
                       "image collection; extracting visual evidence without acquired speech audio"
                       if image_paths else "no audio track; extracting visual evidence")
                return Transcript([], engine="none"), 0.0
            async with pools.asr:
                report("analysing", base + 0.05, "transcribing speech")
                started = time.perf_counter()
                value = await providers.transcript.transcribe(audio_path)
                diarizer = getattr(providers, "diarization", None)
                if value.segments and diarizer is not None and diarizer.available():
                    value = await diarizer.assign(audio_path, value)
                return value, round(time.perf_counter() - started, 3)

        async def vision():
            if audio_only:
                return [], [], None, 0.0
            started = time.perf_counter()
            problem = await visual_states.annotate(
                canonical, pools.ocr, providers.text
            )
            ocr_seconds = round(time.perf_counter() - started, 3)
            report("analysing", base + 0.05, problem or "reading on-screen text")
            # Separate pages are not timed progressive builds or scrolls.
            build_groups = [] if image_paths else visual_states.group_progressive_builds(canonical)
            scroll_groups = [] if image_paths else visual_states.group_scroll_sequences(canonical)
            return build_groups, scroll_groups, problem, ocr_seconds

        (transcript, asr_seconds), (
            build_groups,
            scroll_groups,
            ocr_error,
            ocr_seconds,
        ) = await gather_joined(speech(), vision())
        stage_started = time.perf_counter()
        visual_primary = not audio_only and (
            bool(image_paths) or audio_path is None or (
                not transcript.error and not transcript.segments
            )
        )
        item.info["_clipmind_visual_primary"] = visual_primary
        item.info["_clipmind_speech_status"] = (
            "not_acquired" if image_paths else "no_audio_track" if audio_path is None
            else "unavailable" if transcript.error else "detected" if transcript.segments
            else "not_detected"
        )
        spoken = tuple(
            (segment.start, segment.end, segment.text)
            for segment in transcript.segments
        )
        # Measured on canonical states, never used to decide canonical
        # membership: an ASR or OCR slip must not be able to delete evidence.
        visual_states.annotate_transcript_alignment(canonical, spoken)
        visual_states.annotate_structure(canonical, spoken_intervals=spoken)
        # The images are the primary content of a photo post, even if a page
        # has no OCR, one heading, or a photograph. Video-only salience rules
        # must not silently turn such a post into an empty reading pack.
        selected = canonical if visual_primary else visual_content.select_content_frames(
            canonical, spoken_intervals=spoken)
        preview = visual_states.materialize_preview(
            selected,
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
            scroll_groups=scroll_groups,
            ocr_error=ocr_error,
            timings=timings,
            config=config,
            preflight_result=estimate.public(),
            preview_algorithm=(
                "visual-only-primary-v1" if visual_primary
                else visual_content.CONTENT_ALGORITHM
            ),
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
