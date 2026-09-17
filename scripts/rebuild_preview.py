#!/usr/bin/env python3
"""Rebuild a derived preview from an existing complete Evidence Pack."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import evidence, media, render, visual_states  # noqa: E402
from clipmind.asr import Segment, Transcript, Word  # noqa: E402
from clipmind.config import settings  # noqa: E402
from clipmind.fetch import Media  # noqa: E402
from clipmind.index import EvidenceIndex  # noqa: E402
from clipmind.providers import default_providers  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _transcript(rows: list[dict], manifest: dict) -> Transcript:
    return Transcript(
        segments=[
            Segment(
                float(row["start"]),
                float(row["end"]),
                row["text"],
                tuple(
                    Word(
                        float(word["start"]),
                        float(word["end"]),
                        word["text"],
                        word.get("probability"),
                    )
                    for word in row.get("words", ())
                ),
                row.get("speaker"),
            )
            for row in rows
        ],
        error=(manifest.get("diagnostics") or {}).get("asr_error"),
        diarization_error=(manifest.get("diagnostics") or {}).get("diarization_error"),
    )


def rebuild(workdir: Path, *, refresh_ocr: bool = False) -> int:
    manifest = evidence.load_complete_pack(workdir)
    timeline = read_jsonl(workdir / "visual_timeline.jsonl")
    ocr_by_id = {row["id"]: row for row in read_jsonl(workdir / "ocr.jsonl")}

    frames = []
    for index, row in enumerate(timeline):
        ocr = ocr_by_id[row["ocr_ref"]]
        path = workdir / row["file"]
        frames.append(
            media.Frame(
                index=index,
                timestamp=float(row["start"]),
                path=path,
                phash=media.dhash(path),
                text=ocr.get("text", ""),
                lines=tuple(ocr.get("lines", ())),
                dedupe_warning=row.get("dedupe_warning"),
                ocr_warning=ocr.get("error"),
                observed_sample_count=int(row.get("observed_sample_count") or 1),
                stable_duration=float(row.get("stable_duration_seconds") or 0),
            )
        )

    ocr_error = None
    if refresh_ocr:
        started = time.perf_counter()
        ocr_error = asyncio.run(
            visual_states.annotate(
                frames,
                asyncio.Semaphore(settings.max_ocr),
                default_providers(settings).text,
            )
        )
        if frames and all(frame.ocr_warning for frame in frames):
            raise RuntimeError(ocr_error or "OCR failed on every frame")
        manifest.setdefault("timings", {})["ocr_seconds"] = round(
            time.perf_counter() - started, 3
        )

    groups = visual_states.group_progressive_builds(frames)
    transcript_rows = read_jsonl(workdir / "transcript.jsonl")
    spoken = tuple(
        (float(row["start"]), float(row["end"]), row["text"]) for row in transcript_rows
    )
    # Packs written before schema 1.1.0 carry no alignment measurements, so a
    # rebuild is also how they gain them.
    visual_states.annotate_transcript_alignment(frames, spoken)
    selected = visual_states.derive_preview(frames, spoken_intervals=spoken)
    visual_root = workdir / "visual_states"
    temporary = Path(tempfile.mkdtemp(prefix="preview.next-", dir=visual_root))
    preview = visual_root / "preview"
    previous = visual_root / f"preview.previous-{os.getpid()}"
    timeline_path = workdir / "visual_timeline.jsonl"
    timeline_previous = workdir / f"visual_timeline.previous-{os.getpid()}.jsonl"
    manifest_path = workdir / "manifest.json"
    manifest_previous = workdir / f"manifest.previous-{os.getpid()}.json"
    job_path = workdir / "job.json"
    job_previous = workdir / f"job.previous-{os.getpid()}.json"
    metadata_path = workdir / "metadata.json"
    metadata_previous = workdir / f"metadata.previous-{os.getpid()}.json"
    timeline_next = workdir / "visual_timeline.jsonl.next"
    manifest_next = workdir / "manifest.json.next"
    job_next = workdir / "job.json.next"
    metadata_next = workdir / "metadata.json.next"
    ocr_path = workdir / "ocr.jsonl"
    ocr_previous = workdir / f"ocr.previous-{os.getpid()}.jsonl"
    ocr_next = workdir / "ocr.jsonl.next"
    evidence_path = workdir / "evidence.md"
    evidence_previous = workdir / f"evidence.previous-{os.getpid()}.md"
    evidence_next = workdir / "evidence.md.next"
    manifest_moved = False
    timeline_moved = False
    job_moved = False
    metadata_moved = False
    preview_moved = False
    preview_installed = False
    ocr_moved = False
    evidence_moved = False
    complete = False
    try:
        visual_states.materialize_preview(selected, temporary)
        selected_names = {frame.path.name for frame in selected}
        by_index = {frame.index: frame for frame in frames}
        for index, row in enumerate(timeline):
            frame = by_index[index]
            row["in_preview"] = frame.path.name in selected_names
            row["ocr_char_count"] = frame.ocr_char_count
            row["transcript_novelty_char_count"] = frame.transcript_novelty
            row["transcript_overlap_ratio"] = frame.transcript_overlap
            row.pop("preview_file", None)
            for key in ("build_group_id", "build_position", "build_size"):
                row.pop(key, None)
            if row["in_preview"]:
                row["preview_file"] = f"visual_states/preview/{frame.path.name}"
            if frame.build_group_id:
                row.update(
                    build_group_id=frame.build_group_id,
                    build_position=frame.build_position,
                    build_size=frame.build_size,
                )

        # A rebuild brings the pack up to the schema it was rebuilt with.
        manifest["schema"]["version"] = evidence.SCHEMA_VERSION
        manifest["counts"]["preview_visual_states"] = len(selected)
        manifest["counts"]["progressive_build_groups"] = len(groups)
        manifest["configuration"]["preview_algorithm"] = visual_states.PREVIEW_ALGORITHM
        preview_records = render.preview_records(selected)
        group_records = render.build_group_records(groups)
        metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.exists()
            else None
        )
        if metadata is not None:
            if refresh_ocr:
                by_name = {frame.path.name: frame for frame in frames}
                for state in metadata.get("visual_states", ()):
                    frame = by_name.get(Path(state.get("file", "")).name)
                    if frame is not None:
                        state["text"] = frame.text
                metadata["ocr_error"] = ocr_error
            metadata.update(
                visual_preview=preview_records,
                build_groups=group_records,
                preview_frame_count=len(selected),
            )
        job_payload = json.loads(job_path.read_text(encoding="utf-8"))
        result = job_payload.get("job", {}).get("result")
        if isinstance(result, dict):
            if refresh_ocr:
                by_name = {frame.path.name: frame for frame in frames}
                for state in result.get("visual_states", ()):
                    frame = by_name.get(Path(state.get("file", "")).name)
                    if frame is not None:
                        state["text"] = frame.text
                result["ocr_error"] = ocr_error
            result.update(
                visual_preview=preview_records,
                build_groups=group_records,
                preview_frame_count=len(selected),
            )
        failures = sum(frame.ocr_warning is not None for frame in frames)
        if refresh_ocr:
            manifest["completeness"]["ocr"] = "partial" if failures else "complete"
            manifest["diagnostics"]["ocr_error"] = ocr_error
            manifest["diagnostics"]["ocr_failure_count"] = failures
            for payload in (metadata, result):
                if isinstance(payload, dict) and isinstance(payload.get("evidence_pack"), dict):
                    payload["evidence_pack"]["completeness"] = dict(
                        manifest["completeness"]
                    )
            source = json.loads((workdir / "source.json").read_text(encoding="utf-8"))
            item = Media(
                workdir / "source.mp4",
                {
                    "id": source.get("source_id"),
                    "title": source.get("title"),
                    "duration": source.get("duration"),
                    "webpage_url": source.get("url"),
                    "uploader": source.get("uploader"),
                    "_clipmind_platform": source.get("platform"),
                },
            )
            write_jsonl(ocr_next, evidence._ocr_records(frames))
            evidence_next.write_text(
                evidence.evidence_markdown(
                    item, frames, _transcript(transcript_rows, manifest), workdir
                ),
                encoding="utf-8",
            )
        write_jsonl(timeline_next, timeline)
        write_json(manifest_next, manifest)
        write_json(job_next, job_payload)
        if metadata is not None:
            write_json(metadata_next, metadata)

        os.replace(manifest_path, manifest_previous)
        manifest_moved = True
        os.replace(timeline_path, timeline_previous)
        timeline_moved = True
        os.replace(job_path, job_previous)
        job_moved = True
        if metadata is not None:
            os.replace(metadata_path, metadata_previous)
            metadata_moved = True
        if refresh_ocr:
            os.replace(ocr_path, ocr_previous)
            ocr_moved = True
            os.replace(evidence_path, evidence_previous)
            evidence_moved = True
        os.replace(preview, previous)
        preview_moved = True
        os.replace(temporary, preview)
        preview_installed = True
        os.replace(timeline_next, timeline_path)
        os.replace(job_next, job_path)
        if metadata is not None:
            os.replace(metadata_next, metadata_path)
        if refresh_ocr:
            os.replace(ocr_next, ocr_path)
            os.replace(evidence_next, evidence_path)
        os.replace(manifest_next, manifest_path)
        complete = True
    except Exception:
        if not complete:
            if preview_installed and preview.exists():
                shutil.rmtree(preview)
            if preview_moved:
                os.replace(previous, preview)
            if metadata_moved:
                metadata_path.unlink(missing_ok=True)
                os.replace(metadata_previous, metadata_path)
            if evidence_moved:
                evidence_path.unlink(missing_ok=True)
                os.replace(evidence_previous, evidence_path)
            if ocr_moved:
                ocr_path.unlink(missing_ok=True)
                os.replace(ocr_previous, ocr_path)
            if job_moved:
                job_path.unlink(missing_ok=True)
                os.replace(job_previous, job_path)
            if timeline_moved:
                timeline_path.unlink(missing_ok=True)
                os.replace(timeline_previous, timeline_path)
            if manifest_moved:
                manifest_path.unlink(missing_ok=True)
                os.replace(manifest_previous, manifest_path)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        timeline_next.unlink(missing_ok=True)
        manifest_next.unlink(missing_ok=True)
        job_next.unlink(missing_ok=True)
        metadata_next.unlink(missing_ok=True)
        ocr_next.unlink(missing_ok=True)
        evidence_next.unlink(missing_ok=True)
        if complete:
            shutil.rmtree(previous, ignore_errors=True)
            manifest_previous.unlink(missing_ok=True)
            timeline_previous.unlink(missing_ok=True)
            job_previous.unlink(missing_ok=True)
            metadata_previous.unlink(missing_ok=True)
            ocr_previous.unlink(missing_ok=True)
            evidence_previous.unlink(missing_ok=True)
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdirs", nargs="+", type=Path)
    parser.add_argument(
        "--refresh-ocr",
        action="store_true",
        help="rerun OCR on retained canonical images before rebuilding the preview",
    )
    args = parser.parse_args()
    for workdir in args.workdirs:
        resolved = workdir.resolve()
        count = rebuild(resolved, refresh_ocr=args.refresh_ocr)
        index_path = resolved.parent / ".evidence-index.sqlite3"
        if index_path.exists():
            EvidenceIndex(index_path).sync(resolved.name, resolved)
        print(f"{workdir}: {count} preview state(s)")


if __name__ == "__main__":
    main()
