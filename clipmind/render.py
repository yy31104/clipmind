"""Compatibility metadata beside the Evidence Pack, for the existing UI."""
from __future__ import annotations

import json
from pathlib import Path

from .asr import Transcript
from .fetch import Media
from .media import Frame
from .visual_states import BuildGroup


def clock(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def preview_records(frames: list[Frame]) -> list[dict]:
    """Serialize the UI preview once for pipeline, job state and rebuilds."""
    return [
        {
            "timestamp": frame.timestamp,
            "clock": clock(frame.timestamp),
            "file": (Path("visual_states") / "preview" / frame.path.name).as_posix(),
            "canonical_file": (
                Path("visual_states") / "all" / frame.path.name
            ).as_posix(),
            "text": frame.text,
            "build_group_id": frame.build_group_id,
            "transcript_novelty_char_count": frame.transcript_novelty,
            "ocr_char_count": frame.ocr_char_count,
        }
        for frame in sorted(frames, key=lambda frame: (frame.timestamp, frame.index))
    ]


def build_group_records(groups: list[BuildGroup]) -> list[dict]:
    """Serialize progressive-build membership using canonical pack paths."""
    return [
        {
            "id": group.id,
            "members": [
                (Path("visual_states") / "all" / frame.path.name).as_posix()
                for frame in group.frames
            ],
            "representative": (
                Path("visual_states") / "all" / group.representative.path.name
            ).as_posix(),
        }
        for group in groups
    ]


def build_metadata(
    dest: Path,
    item: Media,
    transcript: Transcript,
    ocr_error: str | None = None,
    *,
    visual_states: list[Frame] | None = None,
    visual_preview: list[Frame] | None = None,
    build_groups: list[BuildGroup] | None = None,
    candidate_frame_count: int | None = None,
    evidence_manifest: dict | None = None,
    stage_timings: dict[str, float] | None = None,
) -> dict:
    canonical = sorted(
        visual_states or [], key=lambda frame: (frame.timestamp, frame.index)
    )

    metadata = {
        "id": item.video_id,
        "title": item.title,
        "uploader": item.uploader,
        "duration": item.duration,
        "url": item.webpage_url,
        "strategy": item.info.get("_clipmind_strategy"),
        "asr_engine": transcript.engine,
        "asr_error": transcript.error,
        "ocr_error": ocr_error,
        "stage_timings": dict(sorted((stage_timings or {}).items())),
    }
    if visual_states is not None:
        states = []
        for frame in canonical:
            state = {
                "timestamp": frame.timestamp,
                "clock": clock(frame.timestamp),
                "file": frame.path.relative_to(dest).as_posix(),
                "text": frame.text,
            }
            if frame.dedupe_warning:
                state["dedupe_warning"] = frame.dedupe_warning
            if frame.build_group_id:
                state.update(
                    {
                        "build_group_id": frame.build_group_id,
                        "build_position": frame.build_position,
                        "build_size": frame.build_size,
                    }
                )
            states.append(state)
        preview = sorted(
            visual_preview or [], key=lambda frame: (frame.timestamp, frame.index)
        )
        metadata.update(
            {
                "visual_states": states,
                "visual_preview": preview_records(preview),
                "build_groups": build_group_records(build_groups or []),
                "candidate_frame_count": candidate_frame_count,
                "canonical_visual_state_count": len(canonical),
                "preview_frame_count": len(preview),
                "dedupe_failure_count": sum(
                    frame.dedupe_warning is not None for frame in canonical
                ),
            }
        )
    if evidence_manifest is not None:
        metadata["evidence_pack"] = {
            "manifest": "manifest.json",
            "evidence": "evidence.md",
            "schema": evidence_manifest["schema"],
            "completeness": evidence_manifest["completeness"],
        }
    return metadata


def write_all(
    dest: Path,
    item: Media,
    transcript: Transcript,
    ocr_error: str | None = None,
    *,
    visual_states: list[Frame] | None = None,
    visual_preview: list[Frame] | None = None,
    build_groups: list[BuildGroup] | None = None,
    candidate_frame_count: int | None = None,
    evidence_manifest: dict | None = None,
    stage_timings: dict[str, float] | None = None,
) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    metadata = build_metadata(
        dest,
        item,
        transcript,
        ocr_error=ocr_error,
        visual_states=visual_states,
        visual_preview=visual_preview,
        build_groups=build_groups,
        candidate_frame_count=candidate_frame_count,
        evidence_manifest=evidence_manifest,
        stage_timings=stage_timings,
    )
    (dest / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dest / "transcript.json").write_text(
        json.dumps(
            [{"start": s.start, "end": s.end, "text": s.text} for s in transcript.segments],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return metadata
