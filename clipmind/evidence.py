"""Versioned, deterministic Evidence Pack serialization."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .asr import Segment, Transcript
from .config import settings
from .fetch import Media
from .media import Frame
from .visual_states import PREVIEW_ALGORITHM, BuildGroup

SCHEMA_NAME = "clipmind-evidence-pack"
SCHEMA_VERSION = "1.0.0"
PACK_ARTIFACTS = (
    "source.json",
    "job.json",
    "transcript.jsonl",
    "transcript.md",
    "ocr.jsonl",
    "visual_timeline.jsonl",
    "evidence.md",
    "visual_states/all/",
    "visual_states/preview/",
)


class EvidencePackError(RuntimeError):
    pass


def load_complete_pack(dest: Path) -> dict:
    """Return a validated completion manifest or reject a partial pack."""
    manifest_path = dest / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidencePackError("missing or invalid manifest.json") from exc
    if (
        manifest.get("schema")
        != {"name": SCHEMA_NAME, "version": SCHEMA_VERSION}
        or manifest.get("status") != "complete"
    ):
        raise EvidencePackError("unsupported or incomplete Evidence Pack")
    missing = [artifact for artifact in PACK_ARTIFACTS if not (dest / artifact).exists()]
    if missing:
        raise EvidencePackError(f"Evidence Pack is missing: {', '.join(missing)}")
    return manifest


def _timestamp(seconds: float) -> str:
    milliseconds = max(round(seconds * 1000), 0)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Iterable[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _transcript_records(transcript: Transcript) -> list[dict]:
    return [
        {
            "id": f"transcript-{position:05d}",
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
        }
        for position, segment in enumerate(transcript.segments, start=1)
    ]


def _ocr_records(frames: list[Frame]) -> list[dict]:
    records = []
    for position, frame in enumerate(frames, start=1):
        record = {
            "id": f"ocr-{position:05d}",
            "visual_state_ref": f"visual-{position:05d}",
            "timestamp": frame.timestamp,
            "lines": list(frame.lines),
            "text": frame.text,
        }
        if frame.ocr_warning:
            record["error"] = frame.ocr_warning
        records.append(record)
    return records


def _overlapping_transcript_refs(
    segments: list[Segment],
    records: list[dict],
    start: float,
    end: float,
) -> list[str]:
    return [
        record["id"]
        for segment, record in zip(segments, records)
        if segment.end > start and segment.start < end
    ]


def _timeline_records(
    frames: list[Frame],
    preview: list[Frame],
    transcript: Transcript,
    transcript_records: list[dict],
    duration: float,
    dest: Path,
) -> list[dict]:
    preview_by_index = {frame.index: frame for frame in preview}
    records = []
    for position, frame in enumerate(frames, start=1):
        end = (
            frames[position].timestamp
            if position < len(frames)
            else max(duration, frame.timestamp)
        )
        preview_frame = preview_by_index.get(frame.index)
        record = {
            "id": f"visual-{position:05d}",
            "start": frame.timestamp,
            "end": end,
            "file": frame.path.relative_to(dest).as_posix(),
            "ocr_ref": f"ocr-{position:05d}",
            "transcript_refs": _overlapping_transcript_refs(
                transcript.segments,
                transcript_records,
                frame.timestamp,
                end,
            ),
            "in_preview": preview_frame is not None,
        }
        if preview_frame is not None:
            record["preview_file"] = preview_frame.path.relative_to(dest).as_posix()
        if frame.build_group_id:
            record.update(
                {
                    "build_group_id": frame.build_group_id,
                    "build_position": frame.build_position,
                    "build_size": frame.build_size,
                }
            )
        if frame.dedupe_warning:
            record["dedupe_warning"] = frame.dedupe_warning
        records.append(record)
    return records


def _transcript_markdown(item: Media, transcript: Transcript) -> str:
    lines = [f"# Transcript — {item.title}", ""]
    if transcript.error:
        lines.extend([f"> Transcript unavailable or incomplete: {transcript.error}", ""])
    if transcript.segments:
        lines.extend(
            f"**{_timestamp(segment.start)}–{_timestamp(segment.end)}** {segment.text}"
            for segment in transcript.segments
        )
    else:
        lines.append("_No transcript segments._")
    return "\n".join(lines) + "\n"


def _evidence_markdown(
    item: Media,
    frames: list[Frame],
    transcript: Transcript,
    dest: Path,
) -> str:
    lines = [
        f"# Evidence — {item.title}",
        "",
        f"- Source: {item.webpage_url or '-'}",
        f"- Uploader: {item.uploader or '-'}",
        f"- Duration: {_timestamp(item.duration)}",
        f"- Schema: `{SCHEMA_NAME}@{SCHEMA_VERSION}`",
        "",
        "> This view preserves source evidence in time order. It does not decide what is important.",
        "",
    ]
    events = [
        (frame.timestamp, 0, position, "visual", frame)
        for position, frame in enumerate(frames)
    ] + [
        (segment.start, 1, position, "transcript", segment)
        for position, segment in enumerate(transcript.segments)
    ]
    for _time, _kind_order, _position, kind, value in sorted(events):
        if kind == "visual":
            frame = value
            lines.extend(
                [
                    f"## {_timestamp(frame.timestamp)} — Visual state",
                    "",
                    f"![Visual state]({frame.path.relative_to(dest).as_posix()})",
                    "",
                ]
            )
            if frame.build_group_id:
                lines.extend(
                    [
                        f"Build: `{frame.build_group_id}` "
                        f"({frame.build_position + 1}/{frame.build_size})",
                        "",
                    ]
                )
            if frame.lines:
                lines.extend(["### OCR", "", "```text", *frame.lines, "```", ""])
            elif frame.ocr_warning:
                lines.extend([f"> OCR unavailable: {frame.ocr_warning}", ""])
        else:
            segment = value
            lines.extend(
                [
                    f"## {_timestamp(segment.start)}–{_timestamp(segment.end)} — Transcript",
                    "",
                    segment.text,
                    "",
                ]
            )
    if not events:
        lines.extend(["_No visual or speech evidence was extracted._", ""])
    return "\n".join(lines)


def write_pack(
    dest: Path,
    item: Media,
    transcript: Transcript,
    visual_states: list[Frame],
    visual_preview: list[Frame],
    build_groups: list[BuildGroup],
    *,
    candidate_frame_count: int,
    ocr_error: str | None = None,
) -> dict:
    """Write every canonical artifact, with manifest.json written last."""
    dest.mkdir(parents=True, exist_ok=True)
    canonical = sorted(
        visual_states, key=lambda frame: (frame.timestamp, frame.index)
    )
    preview = sorted(
        visual_preview, key=lambda frame: (frame.timestamp, frame.index)
    )
    transcript_records = _transcript_records(transcript)
    ocr_records = _ocr_records(canonical)
    timeline_records = _timeline_records(
        canonical,
        preview,
        transcript,
        transcript_records,
        item.duration,
        dest,
    )

    source = {
        "platform": "douyin",
        "source_id": item.video_id,
        "url": item.webpage_url,
        "title": item.title,
        "uploader": item.uploader,
        "duration": item.duration,
        "acquisition_strategy": item.info.get("_clipmind_strategy"),
    }
    _write_json(dest / "source.json", source)
    _write_jsonl(dest / "transcript.jsonl", transcript_records)
    (dest / "transcript.md").write_text(
        _transcript_markdown(item, transcript), encoding="utf-8"
    )
    _write_jsonl(dest / "ocr.jsonl", ocr_records)
    _write_jsonl(dest / "visual_timeline.jsonl", timeline_records)
    (dest / "evidence.md").write_text(
        _evidence_markdown(item, canonical, transcript, dest), encoding="utf-8"
    )

    ocr_failures = sum(frame.ocr_warning is not None for frame in canonical)
    manifest = {
        "schema": {"name": SCHEMA_NAME, "version": SCHEMA_VERSION},
        "source": {"platform": "douyin", "id": item.video_id},
        "status": "complete",
        "artifacts": list(PACK_ARTIFACTS),
        "counts": {
            "candidate_frames": candidate_frame_count,
            "canonical_visual_states": len(canonical),
            "preview_visual_states": len(preview),
            "progressive_build_groups": len(build_groups),
            "transcript_segments": len(transcript_records),
            "ocr_records": len(ocr_records),
        },
        "completeness": {
            "transcript": "unavailable" if transcript.error else "complete",
            "ocr": (
                "unavailable"
                if canonical and ocr_failures == len(canonical)
                else "partial"
                if ocr_failures or ocr_error
                else "complete"
            ),
            "visual_states": (
                "complete_with_warnings"
                if any(frame.dedupe_warning for frame in canonical)
                else "complete"
            ),
        },
        "diagnostics": {
            "asr_error": transcript.error,
            "ocr_error": ocr_error,
            "ocr_failure_count": ocr_failures,
            "dedupe_failure_count": sum(
                frame.dedupe_warning is not None for frame in canonical
            ),
        },
        "configuration": {
            "sample_fps": settings.sample_fps,
            "change_detection_width": settings.sample_width,
            "evidence_width": settings.evidence_width,
            "dedupe_threshold": settings.dedupe_threshold,
            "preview_algorithm": PREVIEW_ALGORITHM,
        },
    }
    _write_json(dest / "manifest.json", manifest)
    return manifest
