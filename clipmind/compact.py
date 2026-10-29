"""Create a smaller, self-contained derivative without changing its source pack."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from . import evidence, handoff, media, render, visual_content, visual_states
from .asr import Segment, Transcript, Word
from .sources.base import MediaAsset


PROFILE = "compact-reading-v1"
EXTRA_ARTIFACTS = ("omitted_visual_text.jsonl", "metadata.json", "transcript.json")


def _json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise evidence.EvidencePackError(f"invalid or unreadable {path.name}") from exc


def _rows(path: Path) -> list[dict]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("objects required")
        return rows
    except (OSError, UnicodeError, ValueError) as exc:
        raise evidence.EvidencePackError(f"invalid or unreadable {path.name}") from exc


def _parent_manifest(pack: Path) -> dict:
    try:
        manifest = evidence.load_complete_pack(pack)
        if not isinstance(manifest.get("configuration", {}), dict):
            raise ValueError("configuration object required")
        if "compact" in manifest.get("configuration", {}):
            raise evidence.EvidencePackError("This pack is already compact; export from the original complete pack.")
        return manifest
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise evidence.EvidencePackError("invalid parent Evidence Pack data") from exc


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def _image(pack: Path, value: str) -> Path:
    relative = Path(value)
    if (relative.is_absolute() or len(relative.parts) != 3
            or relative.parts[:2] != ("visual_states", "all")
            or relative.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif"}):
        raise evidence.EvidencePackError("invalid canonical image reference")
    path = pack / relative
    if path.is_symlink() or not path.resolve().is_relative_to(pack) or not path.is_file():
        raise evidence.EvidencePackError("missing or unsafe canonical image")
    return path


def _load_frames(pack: Path, timeline: list[dict], records: list[dict], *, hash_images: bool = True) -> list[media.Frame]:
    by_id = {row["id"]: row for row in records}
    if len(by_id) != len(records) or len(timeline) != len(records):
        raise evidence.EvidencePackError("inconsistent visual and OCR records")
    frames, names = [], set()
    for index, row in enumerate(timeline):
        text = by_id.get(row["ocr_ref"])
        if (text is None or text.get("visual_state_ref") != row["id"]
                or text.get("timestamp") != row["start"]):
            raise evidence.EvidencePackError("inconsistent visual/OCR reference")
        path = _image(pack, row["file"])
        if path.name.casefold() in names:
            raise evidence.EvidencePackError("duplicate canonical image name")
        names.add(path.name.casefold())
        source_index = row.get("source_image_index")
        if source_index is not None and (type(source_index) is not int or source_index < 0):
            raise evidence.EvidencePackError("invalid source image order")
        motion = row.get("source_motion_timestamp")
        if motion is not None and (type(motion) not in {int, float} or not math.isfinite(motion)
                                   or motion < 0 or source_index is None):
            raise evidence.EvidencePackError("invalid source motion timestamp")
        frame = media.Frame(index, row["start"], path,
            text=text["text"], lines=tuple(text.get("lines", ())),
            ocr_layout=tuple(text.get("layout", ())), ocr_warning=text.get("error"),
            dedupe_warning=row.get("dedupe_warning"),
            observed_sample_count=row.get("observed_sample_count", 1),
            stable_duration=row.get("stable_duration_seconds", 0),
            source_image_index=source_index, source_motion_timestamp=motion)
        if hash_images:
            try:
                frame.phash = media.dhash(path)
            except Exception:
                frame.dedupe_warning = frame.dedupe_warning or "Perceptual hash unavailable"
        frames.append(frame)
    return frames


def _transcript(records: list[dict], manifest: dict, job: dict) -> Transcript:
    diagnostics = manifest.get("diagnostics", {})
    return Transcript([
        Segment(row["start"], row["end"], row["text"], tuple(
            Word(word["start"], word["end"], word["text"], word.get("probability"))
            for word in row.get("words", ())), row.get("speaker"))
        for row in records
    ], engine=(job.get("result") or {}).get("asr_engine", "unknown"),
       error=diagnostics.get("asr_error"),
       diarization_error=diagnostics.get("diarization_error"))


def _build(pack: Path, dest: Path, *, image_policy: str) -> dict:
    parent = _parent_manifest(pack)
    source = _json(pack / "source.json")
    old_job = _json(pack / "job.json").get("job", {})
    speech_rows = _rows(pack / "transcript.jsonl")
    timeline, text_rows = _rows(pack / "visual_timeline.jsonl"), _rows(pack / "ocr.jsonl")
    frames = _load_frames(pack, timeline, text_rows, hash_images=image_policy != "text")
    transcript = _transcript(speech_rows, parent, old_job)
    spoken = tuple((segment.start, segment.end, segment.text) for segment in transcript.segments)
    chosen = []
    selection: dict[int, dict] = {}
    visual_primary = bool(source.get("visual_primary") or source.get("media_type") == "image_collection")
    if image_policy != "text":
        if visual_primary:
            chosen = frames
            selection = {frame.index: {"reason": "primary_visual_source"} for frame in frames}
        else:
            visual_states.annotate_transcript_alignment(frames, spoken)
            chosen = visual_content.select_content_frames(frames, spoken_intervals=spoken,
                                                           decisions=selection)
    completeness, diagnostics = parent.get("completeness", {}), parent.get("diagnostics", {})
    conservative = []
    if (not transcript.text.strip() or transcript.error
            or completeness.get("transcript") != "complete"):
        conservative.append("speech_unavailable")
    if (completeness.get("ocr") != "complete" or diagnostics.get("ocr_error")
            or any(frame.ocr_warning for frame in frames)):
        conservative.append("ocr_uncertain")
    if any(frame.dedupe_warning for frame in frames):
        conservative.append("hash_uncertain")
    # Recognition errors do not turn photographs into useful reading material.
    # Keep modality diagnostics and the original pack, without forcing images.
    selected = chosen
    indexes = {frame.index for frame in selected}
    if not indexes.issubset({frame.index for frame in frames}):
        raise evidence.EvidencePackError("selector returned an unknown visual state")
    retained = [frame for frame in frames if frame.index in indexes]
    all_dir = dest / "visual_states" / "all"
    all_dir.mkdir(parents=True)
    (dest / "visual_states" / "preview").mkdir()
    copied = []
    for frame in retained:
        target = all_dir / frame.path.name
        shutil.copyfile(frame.path, target)
        copied.append(replace(frame, path=target))
    # Rebuild membership after selection, so group sizes cannot name dropped states.
    images = source.get("media_type") == "image_collection"
    groups = [] if images else visual_states.group_progressive_builds(copied)
    scrolls = [] if images else visual_states.group_scroll_sequences(copied)
    visual_states.annotate_structure(copied, spoken_intervals=spoken)
    item = MediaAsset(dest / "no-source-media", {
        "id": source["source_id"], "title": source["title"], "duration": source["duration"],
        "uploader": source.get("uploader"), "webpage_url": source.get("url"),
        "_clipmind_platform": source["platform"],
        "_clipmind_strategy": source.get("acquisition_strategy"),
        "_clipmind_source_adapter": source.get("source_adapter"),
        "_clipmind_media_type": source.get("media_type", "video"),
        "_clipmind_audio_status": source.get("audio_status"),
        "_clipmind_speech_status": source.get("speech_status"),
        "_clipmind_time_basis": source.get("time_basis"),
        "_clipmind_visual_primary": visual_primary,
        **{f"_clipmind_{field}": source[field] for field in (
            "source_image_count", "source_motion_count", "motion_sample_fps", "motion_audio_status", "motion_time_basis", "image_page_transform") if field in source}},
        image_paths=tuple(frame.path for frame in copied) if images else ())
    algorithm = ("visual-only-primary-v1" if visual_primary else visual_content.CONTENT_ALGORITHM) if image_policy == "content" else "text-only-v1"
    manifest = evidence.write_pack(dest, item, transcript, copied, copied, groups,
        candidate_frame_count=parent.get("counts", {}).get("candidate_frames", len(frames)),
        scroll_groups=scrolls, ocr_error=diagnostics.get("ocr_error"),
        timings=parent.get("timings", {}), preview_algorithm=algorithm)
    # ASR and source evidence remain byte-identical, including extra future fields.
    for name in ("source.json", "transcript.jsonl", "transcript.md"):
        shutil.copyfile(pack / name, dest / name)
    manifest["configuration"] = {
        **parent.get("configuration", {}),
        "preview_algorithm": algorithm,
        "compact": {"profile": PROFILE,
            "scope": "derived_selection_not_full_visual_evidence",
            "parent_manifest_sha256": hashlib.sha256((pack / "manifest.json").read_bytes()).hexdigest(),
            "selection_algorithm": algorithm, "image_policy": image_policy,
            "original_visual_states": len(frames), "retained_visual_states": len(copied),
            "omitted_visual_states": len(frames) - len(copied),
            "duplicate_visual_states": sum(value.get("reason") == "duplicate_visual_content"
                                            for value in selection.values()),
            "covered_reading_views": sum(value.get("reason") == "repeated_reading_view"
                                          for value in selection.values()),
            "conservative_retention": conservative, "source_completeness": completeness,
            "preview_reuses_canonical_images": True}}
    manifest["artifacts"] = list(dict.fromkeys([*manifest["artifacts"], *EXTRA_ARTIFACTS]))
    # Preserve upstream ASR modality/diagnostic conclusions, not fresh model claims.
    for modality in ("transcript", "word_timing", "speaker_diarization", "ocr"):
        if modality in completeness:
            manifest["completeness"][modality] = completeness[modality]
    for key in ("asr_error", "diarization_error", "audio_status", "speech_status", "visual_status",
                "ocr_error", "ocr_failure_count", "time_basis"):
        if key in diagnostics:
            manifest["diagnostics"][key] = diagnostics[key]
    metadata = render.build_metadata(dest, item, transcript, diagnostics.get("ocr_error"),
        visual_states=copied, visual_preview=copied, build_groups=groups,
        candidate_frame_count=manifest["counts"]["candidate_frames"],
        evidence_manifest=manifest, stage_timings=parent.get("timings", {}),
        preflight_result=parent.get("configuration", {}).get("preflight"))
    # The preview is a view of the same JPEGs; no second copy is needed in ZIP.
    for record in metadata.get("visual_preview", []):
        record["file"] = record["canonical_file"]
    job = {**old_job, "id": dest.name, "url": source.get("url") or "",
        "title": source["title"], "status": "done", "stage": "done", "progress": 1.0,
        "options": {}, "result": metadata, "transcript": speech_rows,
        "error": None, "error_code": None, "error_action": None, "error_details": None}
    _write_json(dest / "job.json", {"version": 1, "job": job})
    _write_json(dest / "metadata.json", metadata)
    _write_json(dest / "transcript.json", speech_rows)
    omitted = []
    text_by_id = {row["id"]: row for row in text_rows}
    source_visual_ids = {frame.index: row["id"] for frame, row in zip(frames, timeline)}
    retained_ids = {frame.index: f"visual-{number:05d}"
                    for number, frame in enumerate(retained, 1)}
    for frame, row in zip(frames, timeline):
        text = text_by_id[row["ocr_ref"]]
        if frame.index not in indexes:
            omitted.append({"id": row["id"], "timestamp": row["start"],
                "text": text["text"], "lines": text.get("lines", []),
                **({key: row[key] for key in ("visual_state_index", "source_image_index", "source_motion_timestamp", "time_basis", "timestamp_available") if key in row}),
                **({"ocr_error": text["error"]} if text.get("error") else {}),
                "selection_reason": "text_only_requested" if image_policy == "text"
                    else selection.get(frame.index, {}).get("reason", "no_independent_visual_content_selected")})
            representative = selection.get(frame.index, {}).get("representative_index")
            if representative in retained_ids:
                omitted[-1].update(
                    representative_source_visual_id=source_visual_ids[representative],
                    representative_visual_ref=retained_ids[representative])
            representatives = selection.get(frame.index, {}).get("representative_indexes", ())
            if representatives:
                omitted[-1]["reading_representatives"] = [
                    {"source_visual_id": source_visual_ids[index],
                     "visual_ref": retained_ids[index]}
                    for index in representatives if index in retained_ids]
                omitted[-1]["reading_phrase_coverage"] = selection[frame.index]["reading_phrase_coverage"]
    # A portable audit trail explains selection without claiming to recognize
    # every semantically important moment. The source pack remains untouched.
    decision_rows = []
    for frame, row in zip(frames, timeline):
        decision = selection.get(frame.index, {})
        decision_rows.append({"source_visual_id": row["id"], "timestamp": row["start"],
            "retained": frame.index in indexes,
            "reason": "text_only_requested" if image_policy == "text" else decision.get("reason", "unclassified"),
            **({"visual_ref": retained_ids[frame.index]} if frame.index in retained_ids else {}),
            **({"representative_source_visual_id": source_visual_ids[decision["representative_index"]]}
               if decision.get("representative_index") in source_visual_ids else {})})
        if decision.get("representative_indexes"):
            decision_rows[-1].update(
                representative_source_visual_ids=[source_visual_ids[index]
                    for index in decision["representative_indexes"] if index in source_visual_ids],
                reading_phrase_coverage=decision.get("reading_phrase_coverage"))
    _write_json(dest / "selection_report.json", {"version": 1,
        "scope": "structural_selection_not_semantic_guarantee", "algorithm": algorithm,
        "source_count": len(frames), "retained_count": len(retained),
        "transcript_preserved_verbatim": True,
        "omitted_ocr_preserved": True, "decisions": decision_rows})
    manifest["artifacts"].append("selection_report.json")
    (dest / "omitted_visual_text.jsonl").write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in omitted), encoding="utf-8")
    readable = dest / "evidence.md"
    readable.write_text(
        f"> Compact reading derivative: {len(copied)} of {len(frames)} visual states selected. "
        "The complete transcript is retained; omitted OCR text is in omitted_visual_text.jsonl. "
        "The original pack retains the full visual evidence.\n\n"
        + readable.read_text(encoding="utf-8"), encoding="utf-8")
    _write_json(dest / "manifest.json", manifest)
    return {"manifest": manifest, "original_count": len(frames),
        "retained_count": len(copied), "omitted_count": len(omitted)}


def build_compact_pack(pack_dir: Path, destination: Path, *, image_policy: str = "content") -> dict:
    """Publish a new complete derivative; never overwrite or alter the original."""
    if image_policy not in {"content", "text"}:
        raise evidence.EvidencePackError("image_policy must be content or text")
    pack, destination = Path(pack_dir).resolve(), Path(destination).resolve()
    if destination == pack or destination.is_relative_to(pack):
        raise evidence.EvidencePackError("compact destination must be outside the source pack")
    if destination.exists():
        raise FileExistsError(destination)
    _parent_manifest(pack)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".clipmind-compact-", dir=destination.parent) as temporary:
        # Same final basename keeps job IDs and archive contents deterministic.
        stage = Path(temporary) / destination.name
        stage.mkdir()
        try:
            report = _build(pack, stage, image_policy=image_policy)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise evidence.EvidencePackError("invalid source Evidence Pack data") from exc
        stage.rename(destination)
    return {**report, "path": str(destination)}


def export_compact_zip(pack_dir: Path, destination: Path | None = None, *, image_policy: str = "content") -> Path:
    """Export a deterministic compact derivative to a separate, atomic ZIP."""
    pack = Path(pack_dir).resolve()
    suffix = ".text.evidence.zip" if image_policy == "text" else ".compact.evidence.zip"
    destination = Path(destination or pack.with_suffix(suffix)).resolve()
    if (destination == pack.with_suffix(".evidence.zip")
            or destination == pack or destination.is_relative_to(pack)):
        raise evidence.EvidencePackError("compact export cannot overwrite the original pack or full ZIP")
    _parent_manifest(pack)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".clipmind-compact-", dir=destination.parent) as temporary:
        derived = Path(temporary) / pack.name
        build_compact_pack(pack, derived, image_policy=image_policy)
        archive_path = Path(temporary) / "compact.zip"
        handoff.export_zip(derived, archive_path)
        os.replace(archive_path, destination)
    return destination
