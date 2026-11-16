#!/usr/bin/env python3
"""Run source-annotated real-talk excerpts through the production SDK.

Labels must be frozen from source frames before extraction. This measures
canonical state boundaries, end-to-end ROI OCR, and agreement with publisher
captions, not a semantic page classifier or human transcription accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import hashlib
import html
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.screen_evaluation import (PROTOCOL, boundary_inventory, reserve_record,
                                       resolve_pack_path, safe_report, tesseract_metadata,
                                       OCRDiagnosticProxy, case_source_url)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_code_fingerprint(checkout: Path | None = None) -> dict:
    """Hash actual runtime bytes, including new and modified local files."""
    checkout = checkout or REPO
    names = [path.relative_to(checkout).as_posix() for path in (checkout / "clipmind").rglob("*")
             if path.is_file() and "__pycache__" not in path.parts]
    files = {name: file_digest(checkout / name) for name in sorted(names)
             if Path(name).suffix == ".py" or name.startswith("clipmind/web/")}
    serialized = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"sha256": hashlib.sha256(serialized).hexdigest(), "files": files}


def verify_installed_runtime(package_root: Path, expected: dict) -> dict:
    """Compare every installed Python/web byte with the candidate manifest."""
    if not isinstance(expected.get("files"), dict) or not expected["files"]:
        raise ValueError("expected runtime fingerprint needs Python/web file hashes")
    actual = {}
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(package_root)
        if path.suffix == ".py" or relative.parts[0] == "web":
            actual["clipmind/" + relative.as_posix()] = file_digest(path)
    serialized = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result = {"sha256": hashlib.sha256(serialized).hexdigest(), "files": actual}
    if result != {key: expected.get(key) for key in ("sha256", "files")}:
        raise ValueError("installed runtime Python/web bytes do not match the expected checkout")
    return result


def dependency_versions() -> dict:
    return dict(sorted((distribution.metadata["Name"], distribution.version)
                       for distribution in importlib.metadata.distributions()
                       if distribution.metadata.get("Name")))


def tool_versions() -> dict:
    versions = {}
    for executable in ("ffmpeg", "ffprobe"):
        result = subprocess.run([executable, "-version"], check=True, capture_output=True,
                                text=True, timeout=10)
        versions[executable] = result.stdout.splitlines()[0]
    versions["tesseract"] = tesseract_metadata()
    return versions


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def validate_inputs(corpus: dict, labels: dict, workdir: Path) -> None:
    """Check every source/annotation binding before any production extraction."""
    if workdir.resolve().is_relative_to(REPO):
        raise ValueError("--workdir must be outside the checkout")
    if labels.get("schema_version") != 1 or not labels.get("annotation_protocol"):
        raise ValueError("labels need schema_version=1 and an annotation_protocol")
    if not isinstance(corpus.get("cases"), list) or not corpus["cases"]:
        raise ValueError("corpus needs at least one case")
    seen = set()
    for case in corpus["cases"]:
        if not isinstance(case, dict):
            raise ValueError("each source case must be an object")
        identity = case.get("case_id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identity) or identity in seen:
            raise ValueError(f"invalid or duplicate case_id: {identity}")
        seen.add(identity)
        if not isinstance(case.get("title"), str) or not case["title"].strip():
            raise ValueError(f"{identity}: a source title is required")
        if not re.match(r"^https?://[^/\s]+", case_source_url(case)):
            raise ValueError(f"{identity}: a public source URL is required")
        if (not valid_seconds(case.get("sample_start_seconds"), 0, float("inf")) or
                not valid_seconds(case.get("sample_end_seconds"), 0, float("inf")) or
                case["sample_end_seconds"] <= case["sample_start_seconds"]):
            raise ValueError(f"{identity}: invalid source interval")
        if "ocr_languages" in case and (not isinstance(case["ocr_languages"], list) or not case["ocr_languages"]
                or any(not isinstance(value, str) or not value.strip() for value in case["ocr_languages"])):
            raise ValueError(f"{identity}: invalid OCR language list")
        if "tesseract_languages" in case and (not isinstance(case["tesseract_languages"], str)
                or not case["tesseract_languages"].strip()):
            raise ValueError(f"{identity}: invalid Tesseract language list")
        label = labels.get("cases", {}).get(identity, {})
        if not isinstance(label, dict) or label.get("reviewed") is not True:
            raise ValueError(f"{identity}: source annotations are pending")
        source = workdir / identity / "input.mp4"
        if (not source.resolve().is_relative_to(workdir.resolve()) or not source.is_file() or
                label.get("input_sha256") != file_digest(source)):
            raise ValueError(f"{identity}: labels are not bound to these input bytes")
        captions = workdir / identity / "captions.vtt"
        if case.get("captions_available") is not False and not captions.is_file():
            raise ValueError(f"{identity}: publisher captions are missing")
        preparation = workdir / identity / "metadata.json"
        if preparation.is_file() and captions.is_file():
            expected = json.loads(preparation.read_text(encoding="utf-8")).get("sha256", {}).get("captions.vtt")
            if not isinstance(expected, str) or expected != file_digest(captions):
                raise ValueError(f"{identity}: publisher captions differ from prepared source bytes")
        duration = case["sample_end_seconds"] - case["sample_start_seconds"]
        if not valid_seconds(duration, 0, float("inf"), strict=True) or label.get("review_start") != 0 or label.get("review_end") != duration:
            raise ValueError(f"{identity}: review the entire prepared excerpt")
        boundaries = label.get("slide_changes")
        if not isinstance(boundaries, list) or any(not valid_seconds(t, 0, duration, strict=True) for t in boundaries):
            raise ValueError(f"{identity}: invalid source boundary labels")
        if sorted(set(boundaries)) != boundaries:
            raise ValueError(f"{identity}: boundaries must be unique and sorted")
        if not isinstance(label.get("ocr"), list):
            raise ValueError(f"{identity}: provide OCR references or an explicit empty list")
        for reference in label["ocr"]:
            if not isinstance(reference, dict):
                raise ValueError(f"{identity}: OCR references must be objects")
            roi = reference.get("roi", [])
            valid_roi = (isinstance(roi, list) and len(roi) == 4 and
                         all(valid_seconds(n, 0, 1) for n in roi) and
                         roi[2] > 0 and roi[3] > 0 and
                         roi[0] + roi[2] <= 1.000001 and roi[1] + roi[3] <= 1.000001)
            if (not valid_roi or not valid_seconds(reference.get("timestamp"), 0, duration) or
                    reference["timestamp"] >= duration):
                raise ValueError(f"{identity}: OCR timestamp/normalized ROI is invalid")
            if not isinstance(reference.get("text"), str) or not reference["text"].strip():
                raise ValueError(f"{identity}: OCR reference text must not be empty")
        if not isinstance(label.get("build_groups", []), list):
            raise ValueError(f"{identity}: build groups must be a list")
        for group in label.get("build_groups", []):
            if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"].strip():
                raise ValueError(f"{identity}: source build groups need an id")
            points = [group.get(key) for key in ("start", "complete_start", "reference_timestamp", "complete_end")]
            if (not all(valid_seconds(point, 0, duration) for point in points) or
                    not points[0] <= points[1] <= points[2] < points[3]):
                raise ValueError(f"{identity}: invalid source complete-build interval")


def valid_seconds(value: object, start: float, end: float, strict: bool = False) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and
            math.isfinite(value) and (start < value < end if strict else start <= value <= end))


def boundary_score(reference: list[float], predicted: list[float], tolerance: float = 1.0) -> dict:
    """Sorted one-to-one interval matching; repeated predictions cannot inflate recall."""
    truth, guesses = sorted(reference), sorted(predicted)
    i = j = 0
    matches = []
    while i < len(truth) and j < len(guesses):
        if abs(truth[i] - guesses[j]) <= tolerance:
            matches.append([truth[i], guesses[j]])
            i += 1
            j += 1
        elif guesses[j] < truth[i]:
            j += 1
        else:
            i += 1
    return {"reference_count": len(truth), "prediction_count": len(guesses),
            "matched": len(matches), "matches": matches, "tolerance_seconds": tolerance,
            "recall": len(matches) / len(truth) if truth else None,
            "precision": len(matches) / len(guesses) if guesses else None}


def normalized_text(text: str, *, speech: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text)
    if speech:
        text = "".join(c for c in text.lower() if not unicodedata.category(c).startswith("P"))
        return " ".join(text.split())
    return "".join(text.split())


def edit_score(reference: str, hypothesis: str, *, speech: bool = False) -> dict:
    reference = normalized_text(reference, speech=speech)
    hypothesis = normalized_text(hypothesis, speech=speech)
    if not reference:
        return {"status": "not_applicable", "value": None, "reference_units": 0}
    try:
        import jiwer
    except ImportError as exc:
        raise RuntimeError("Install scripts/requirements-eval.txt to calculate WER/CER") from exc
    result = (jiwer.process_words if speech else jiwer.process_characters)(reference, hypothesis)
    errors = result.substitutions + result.deletions + result.insertions
    units = result.hits + result.substitutions + result.deletions
    return {"status": "measured", "value": errors / units,
            "reference_units": units, "errors": errors,
            "substitutions": result.substitutions, "deletions": result.deletions,
            "insertions": result.insertions, "reference": reference, "hypothesis": hypothesis}


def vtt_cues(text: str) -> list[dict]:
    def seconds(token: str) -> float:
        values = [float(part) for part in token.replace(",", ".").split(":")]
        return sum(value * 60 ** position for position, value in enumerate(reversed(values)))

    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = block.strip().splitlines()
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        start, end = lines[timing].split("-->", 1)
        value = html.unescape(re.sub(r"<[^>]+>", "", " ".join(lines[timing + 1:])))
        cues.append({"start": seconds(start.strip()), "end": seconds(end.strip().split()[0]), "text": value})
    return cues


def caption_score(cues: list[dict], transcript: list[dict], start: float, end: float,
                  completeness: str) -> dict:
    selected = [cue for cue in cues if start <= cue["start"] and cue["end"] <= end]
    selected.sort(key=lambda cue: cue["start"])
    if not selected:
        return {"status": "not_applicable", "value": None, "reason": "no fully-contained publisher cues"}
    # Decimal VTT milliseconds translated by a large source offset otherwise
    # become e.g. 2.200000000000003 and reject a word starting at exactly 2.2.
    lower = round(selected[0]["start"] - start, 6)
    upper = round(selected[-1]["end"] - start, 6)
    if completeness != "complete":
        return {"status": "failed", "value": None, "reason": "transcript unavailable/incomplete"}
    speech = []
    for segment in transcript:
        units = segment.get("words") or [segment]
        speech.extend(str(unit.get("text") or "") for unit in units
                      if lower <= float(unit["start"]) and float(unit["end"]) <= upper)
    result = edit_score(" ".join(cue["text"] for cue in selected), " ".join(speech), speech=True)
    return result | {"clip_interval": [lower, upper], "publisher_cue_count": len(selected),
                     "word_timed_segment_count": sum(bool(s.get("words")) for s in transcript),
                     "metric": "published_caption_agreement_wer"}


def roi_text(layout: list[dict], roi: list[float]) -> str:
    x, y, width, height = roi
    selected = []
    for block in layout:
        box = block.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        center_x, center_y = box[0] + box[2] / 2, box[1] + box[3] / 2
        if x <= center_x < x + width and y <= center_y < y + height:
            selected.append(str(block.get("text") or ""))
    return " ".join(selected)


def cleanup_inventory(library: Path) -> dict:
    from clipmind import acquisition

    found = {}
    for directory in library.iterdir() if library.exists() else []:
        if directory.is_dir():
            paths = acquisition.leftovers(directory) + [name for name in
                    ("samples", "evidence_samples", "audio.wav") if (directory / name).exists()]
            paths.extend(path.name for path in directory.glob("source.*") if path.name != "source.json")
            if paths:
                found[directory.name] = paths
    return found


def loaded_asr_runtime(config) -> dict:
    """Read the model actually loaded by the production portable provider."""
    from clipmind import asr

    model = asr._faster_models.get(config.faster_whisper_model)
    if model is None:
        return {"provider": "faster-whisper", "loaded": False}
    runtime = model.model
    return {"provider": "faster-whisper", "model": config.faster_whisper_model,
            "loaded": True, "requested_device": "auto", "requested_compute_type": "auto",
            "device": str(runtime.device), "compute_type": str(runtime.compute_type)}


def native_ocr_worker_provenance(expected: dict) -> dict:
    """Record the last successful real worker's module without exposing paths."""
    from clipmind import ocr

    path = getattr(ocr, "_worker_module_path", None)
    if not isinstance(path, str) or not Path(path).is_file():
        return {"status": "not_reported"}
    digest = file_digest(Path(path))
    return {"status": "reported", "last_successful_worker_ocr_sha256": digest,
            "matches_runtime_ocr": digest == expected["files"].get("clipmind/ocr.py")}


def complete_build_coverage(pack, label: dict) -> dict:
    """Check source capture time of the actual covering frame, never a best frame.

    This is temporal coverage, not a pixel-content or readability measurement.
    Old packages without capture provenance remain unknown rather than passing
    because their state interval happens to overlap a complete build.
    """
    from clipmind.evidence import EvidencePackError

    rows = []
    for group in label.get("build_groups", []):
        try:
            _, state = pack.frame(timestamp=group["reference_timestamp"])
        except EvidencePackError:
            rows.append({"group_id": group["id"], "reference_timestamp": group["reference_timestamp"],
                         "complete_interval": [group["complete_start"], group["complete_end"]],
                         "visual_state_id": None, "source_timestamp": None,
                         "covered": False, "status": "failed", "reason": "no retained frame"})
            continue
        captured = state.get("source_timestamp")
        known = valid_seconds(captured, 0, label["review_end"])
        covered = group["complete_start"] <= captured < group["complete_end"] if known else None
        rows.append({"group_id": group["id"], "reference_timestamp": group["reference_timestamp"],
                     "complete_interval": [group["complete_start"], group["complete_end"]],
                     "visual_state_id": state["id"], "source_timestamp": captured if known else None,
                     "covered": covered, "status": "measured" if known else "unknown"})
    unknown = sum(row["covered"] is None for row in rows)
    covered = sum(row["covered"] is True for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    return {"metric": "temporal_complete_build_coverage", "group_count": len(rows),
            "covered_count": covered, "unknown_count": unknown, "failed_count": failed,
            "value": covered / len(rows) if rows and not unknown else None,
            "status": "failed" if failed else "unknown" if unknown else "measured" if rows else "not_applicable",
            "groups": rows}


def score_pack(pack, case: dict, label: dict, captions: Path) -> dict:
    from clipmind.evidence import EvidencePackError

    completeness = pack.manifest.get("completeness", {})
    changes = [float(state["start"]) for state in pack.visual_timeline
               if label["review_start"] < float(state["start"]) < label["review_end"]]
    ocr = []
    records = {record["visual_state_ref"]: record for record in pack.ocr}
    for reference in label["ocr"]:
        try:
            _, state = pack.frame(timestamp=reference["timestamp"])
            record = records.get(state["id"], {})
            reason = record.get("error") or ("OCR unavailable" if completeness.get("ocr") == "unavailable" else "")
            if not record:
                reason = reason or "OCR record missing"
            measured = edit_score(reference["text"], "" if reason else roi_text(record.get("layout", []), reference["roi"]))
            if reason:
                measured.update(status="failed", reason=reason, failure_kind="ocr_failure")
            selection = {"visual_state_id": state["id"], "retained_frame_available": True,
                         "frame_interval": [state["start"], state["end"]]}
        except EvidencePackError as exc:
            measured = edit_score(reference["text"], "")
            measured.update(status="failed", reason=str(exc), failure_kind="missing_retained_frame")
            selection = {"visual_state_id": None, "retained_frame_available": False, "frame_interval": None}
        ocr.append(reference | measured | selection)
    speech = (caption_score(vtt_cues(captions.read_text(encoding="utf-8-sig")), pack.transcript,
                            case["sample_start_seconds"], case["sample_end_seconds"],
                            completeness.get("transcript", "unavailable"))
              if case.get("captions_available") is not False else
              {"status": "not_applicable", "value": None,
               "reason": case.get("captions_reason", "publisher captions unavailable")})
    # Failed or absent frames contribute deleted characters, not a smaller
    # denominator. Their failure status is retained alongside the numeric CER.
    ocr_measured = [row for row in ocr if row.get("reference_units", 0)]
    ocr_units = sum(row["reference_units"] for row in ocr_measured)
    ocr_errors = sum(row["errors"] for row in ocr_measured)
    code_measured = [row for row in ocr_measured if row.get("kind") == "code"]
    code_units = sum(row["reference_units"] for row in code_measured)
    code_errors = sum(row["errors"] for row in code_measured)
    boundaries = boundary_score(label["slide_changes"], changes)
    inventory = boundary_inventory(label["slide_changes"], pack.visual_timeline,
                                   start=label["review_start"], end=label["review_end"])
    # OCR errors live in the OCR records, not necessarily in the timeline.
    # A failed observation cannot justify excluding a predicted boundary.
    observed_states = [state | {"ocr_warning": records.get(state.get("id"), {}).get("error")}
                       for state in pack.visual_timeline]
    information = boundary_inventory(label["slide_changes"], observed_states,
                                     start=label["review_start"], end=label["review_end"],
                                     information_only=True)
    information_score = information["score"]
    if label.get("slide_metric_applicable") is False:
        boundaries = {"status": "not_applicable", "reason": label.get("slide_metric_reason") or label.get("notes") or "source has no slide sequence",
                      "prediction_count": len(changes), "recall": None, "precision": None}
        information_score = boundaries | {"prediction_count": information_score["prediction_count"]}
    return {"completeness": completeness, "diagnostics": pack.manifest.get("diagnostics", {}),
            "timings": pack.manifest.get("timings", {}), "counts": pack.manifest.get("counts", {}),
            "canonical_boundary_score": boundaries,
            "information_boundary_score": information_score,
            "canonical_boundary_inventory": inventory,
            "information_boundary_inventory": information,
            "complete_build_coverage": complete_build_coverage(pack, label),
            "ocr_references": ocr, "roi_ocr_cer": ocr_errors / ocr_units if ocr_units else None,
            "roi_ocr_reference_units": ocr_units, "roi_ocr_errors": ocr_errors,
            "roi_ocr_failed_references": sum(row["status"] == "failed" for row in ocr),
            "code_roi_ocr_cer": code_errors / code_units if code_units else None,
            "code_roi_ocr_reference_units": code_units, "code_roi_ocr_errors": code_errors,
            "code_roi_ocr_failed_references": sum(row["status"] == "failed" and row.get("kind") == "code" for row in ocr),
            "publisher_caption_agreement": speech}


def failed_case_scores(case: dict, label: dict, captions: Path) -> dict:
    """Keep all frozen reference units when extraction produces no usable pack."""
    from types import SimpleNamespace
    from clipmind.evidence import EvidencePackError

    def unavailable_frame(**kwargs):
        raise EvidencePackError("extraction produced no usable retained frame")

    pack = SimpleNamespace(
        manifest={"completeness": {"transcript": "unavailable", "ocr": "unavailable", "visual_states": "unavailable"}},
        visual_timeline=[], ocr=[], transcript=[], frame=unavailable_frame)
    return score_pack(pack, case, label, captions)


def benchmark(args) -> dict:
    installed = bool(getattr(args, "installed_runtime", False))
    expected_path = getattr(args, "expected_runtime_fingerprint", None)
    expected = json.loads(expected_path.read_text()) if expected_path is not None else None
    if installed:
        if expected is None:
            raise ValueError("--installed-runtime needs --expected-runtime-fingerprint")
        # Helpers are already imported, but ClipMind must come from the image's
        # package, not from a mounted driver checkout or its working directory.
        sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != REPO]
        loaded = sys.modules.get("clipmind")
        if loaded is not None and Path(loaded.__file__).resolve().is_relative_to(REPO):
            raise ValueError("checkout runtime was imported before --installed-runtime")
    import clipmind
    from clipmind import __version__
    from clipmind.config import Settings
    from clipmind.sdk import ClipMind, EvidencePack

    if installed:
        fingerprint = verify_installed_runtime(Path(clipmind.__file__).resolve().parent, expected)
    else:
        fingerprint = runtime_code_fingerprint()
        if expected is not None and fingerprint != {key: expected.get(key) for key in ("sha256", "files")}:
            raise ValueError("checkout runtime bytes do not match the expected candidate")
    args.workdir = args.workdir.resolve()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    validate_inputs(corpus, labels, args.workdir)
    once_token = getattr(args, "once_token", None)
    if once_token is not None and once_token.resolve() == args.output.resolve():
        raise ValueError("--once-token and --output must be different paths")
    edit_score("dependency check", "dependency check", speech=True)
    config = Settings(cookie_sources=("-",), cookie_file=None, asr_language=getattr(args, "asr_language", None),
                      asr_provider=args.asr_provider, ocr_provider=args.ocr_provider,
                      ocr_languages=("en-US",), tesseract_languages="eng",
                      max_videos=1, max_fetch=1, max_asr=1, max_ocr=2, keep_source_video=False,
                      knowledge_base_inbox=None, douyin_browser_enabled=False)
    previous_path = getattr(args, "previous", None)
    if args.mode == "score" and previous_path is None:
        raise ValueError("score needs --previous and a new --output; reports cannot be overwritten")
    previous = json.loads(previous_path.read_text()) if args.mode == "score" else {}
    if previous and (previous.get("labels_sha256") != file_digest(args.labels) or
                     previous.get("corpus_sha256") != file_digest(args.corpus)):
        raise ValueError("rescore requires the original frozen labels and corpus")
    previous_cases = {row["case_id"]: row for row in previous.get("cases", [])}
    if args.mode == "score":
        for case in corpus["cases"]:
            identity = case["case_id"]
            expected_caption = previous_cases.get(identity, {}).get("captions_sha256")
            if expected_caption is not None and expected_caption != file_digest(args.workdir / identity / "captions.vtt"):
                raise ValueError(f"{identity}: rescore requires unchanged publisher captions")
    report = {"schema_version": 2, "metric_protocol": PROTOCOL, "mode": args.mode, "clipmind_version": __version__,
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "code_revision": expected.get("code_revision") if installed else subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
              "working_tree_dirty": expected.get("working_tree_dirty") if installed else bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True)),
              "runtime_code_fingerprint": fingerprint,
              "runtime_import": "verified_installed_package" if installed else "checkout",
              "dependency_versions": dependency_versions(), "tool_versions": tool_versions(),
              "evaluator_sha256": file_digest(Path(__file__)), "corpus_sha256": file_digest(args.corpus),
              "metric_implementation_sha256": {
                  "scripts/evaluate_real_talks.py": file_digest(Path(__file__)),
                  "scripts/screen_evaluation.py": file_digest(REPO / "scripts/screen_evaluation.py")},
              "labels_sha256": file_digest(args.labels),
              "configuration": previous.get("configuration", asdict(config)),
              "processing_options": {"force": bool(getattr(args, "force", False)), "reprocess": True,
                                     "ocr_diagnostics": bool(getattr(args, "ocr_diagnostics", False))},
              "status": "running", "planned_case_count": len(corpus["cases"]),
              "planned_primary_change_count": sum(len(labels["cases"][case["case_id"]]["slide_changes"])
                                                   for case in corpus["cases"]
                                                   if labels["cases"][case["case_id"]].get("slide_metric_applicable") is not False),
              "planned_code_reference_units": sum(len(normalized_text(reference["text"]))
                  for case in corpus["cases"] for reference in labels["cases"][case["case_id"]]["ocr"]
                  if reference.get("kind") == "code"),
              "platform": platform.platform(), "python": sys.version,
              "metric_notes": {"boundaries": "all canonical state starts versus independently annotated informational page changes; not a semantic slide classifier",
                               "ocr": "source ROI versus retained-frame layout; NFKC, whitespace removed, case-sensitive CER; end-to-end retention plus OCR",
                               "speech": "NFKC/lowercase/punctuation-removed WER against publisher-generated captions; not human ground truth"},
              "cases": []}
    if previous_path:
        report["previous_report_sha256"] = file_digest(previous_path)
    if expected_path is not None:
        report["expected_runtime_fingerprint_sha256"] = file_digest(expected_path)
    provenance_keys = ("code_revision", "working_tree_dirty", "runtime_code_fingerprint",
                       "dependency_versions", "tool_versions", "platform", "python", "configuration")
    report["extraction_provenance"] = (
        previous.get("extraction_provenance") or {key: previous.get(key) for key in provenance_keys}
        if args.mode == "score" else {key: report[key] for key in provenance_keys}
    )
    # Save every frozen denominator before the first SDK call. An interruption
    # leaves unattempted cases scored as empty predictions, never omitted.
    for case in corpus["cases"]:
        identity = case["case_id"]
        captions = args.workdir / identity / "captions.vtt"
        preparation = args.workdir / identity / "metadata.json"
        row = {"case_id": identity, "title": case["title"], "source_url": case_source_url(case),
               "input_sha256": labels["cases"][identity]["input_sha256"],
               "captions_sha256": file_digest(captions) if captions.is_file() else None,
               "source_interval": [case["sample_start_seconds"], case["sample_end_seconds"]],
               "preparation_sha256": file_digest(preparation) if preparation.is_file() else None,
               "status": "not_attempted"}
        row.update(failed_case_scores(case, labels["cases"][identity], captions))
        report["cases"].append(row)
    # Claim an output before any SDK call. Failed attempts need a new name.
    reserve_record(args.output, safe_report(report, args.workdir))
    if once_token is not None and args.mode == "run":
        try:
            reserve_record(once_token, {"metric_protocol": PROTOCOL,
                           "code_revision": report["code_revision"],
                           "runtime_code_fingerprint": report["runtime_code_fingerprint"]["sha256"],
                           "metric_implementation_sha256": report["metric_implementation_sha256"],
                           "corpus_sha256": report["corpus_sha256"], "labels_sha256": report["labels_sha256"],
                           "asr_provider": args.asr_provider, "ocr_provider": args.ocr_provider,
                           "status": "attempt_started"})
        except BaseException:
            report.update(status="blocked", error="evaluation campaign token already exists or cannot be created")
            atomic_json(args.output, safe_report(report, args.workdir))
            raise
    for case_index, case in enumerate(corpus["cases"]):
        identity = case["case_id"]
        started = time.perf_counter()
        row = report["cases"][case_index]
        library = args.workdir / identity / "library"
        diagnostic_proxy = None
        try:
            if args.mode == "run":
                case_config = replace(config, ocr_languages=tuple(case.get("ocr_languages", ("en-US",))),
                                      tesseract_languages=case.get("tesseract_languages", "eng"))
                row["case_configuration"] = {"asr_language": case_config.asr_language,
                    "ocr_languages": list(case_config.ocr_languages), "tesseract_languages": case_config.tesseract_languages}
                provider_options = {}
                if getattr(args, "ocr_diagnostics", False):
                    from clipmind.providers import default_providers
                    bundle = default_providers(case_config)
                    diagnostic_proxy = OCRDiagnosticProxy(bundle.text)
                    provider_options["providers"] = replace(bundle, text=diagnostic_proxy)
                packs = asyncio.run(ClipMind(library, config=case_config, **provider_options).analyze(
                    str(args.workdir / identity / "input.mp4"), reprocess=True,
                    force=bool(getattr(args, "force", False))))
                if len(packs) != 1:
                    raise ValueError(f"expected one complete pack, got {len(packs)}")
                pack = packs[0]
            else:
                path = resolve_pack_path(previous_cases[identity]["pack_path"], args.workdir)
                pack = EvidencePack.open(path)
            row["pack_path"] = str(pack.root.resolve())
            # The production local adapter's source ID is the SHA-256 of its
            # acquired copy, not a separate content_sha256 manifest field.
            provenance = pack.manifest.get("source", {})
            if provenance.get("platform") != "local" or provenance.get("id") != row["input_sha256"]:
                raise ValueError("pack source does not match annotated input")
            row.update(score_pack(pack, case, labels["cases"][identity], args.workdir / identity / "captions.vtt"))
            healthy = all(row["completeness"].get(k) == "complete" for k in ("ocr", "transcript", "visual_states"))
            row["status"] = "measured" if healthy and not row["roi_ocr_failed_references"] else "incomplete"
        except Exception as exc:
            row.update(failed_case_scores(case, labels["cases"][identity], args.workdir / identity / "captions.vtt"))
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}", error_code=getattr(exc, "code", None))
        except BaseException as exc:
            row.update(failed_case_scores(case, labels["cases"][identity], args.workdir / identity / "captions.vtt"))
            row.update(status="interrupted", error=f"{type(exc).__name__}: extraction interrupted",
                       wall_seconds=round(time.perf_counter() - started, 3))
            if diagnostic_proxy is not None:
                row["real_ocr_diagnostics"] = diagnostic_proxy.snapshot()
            report["status"] = "interrupted"
            atomic_json(args.output, safe_report(report, args.workdir))
            raise
        row["wall_seconds" if args.mode == "run" else "scoring_wall_seconds"] = round(time.perf_counter() - started, 3)
        if args.mode == "score":
            row["wall_seconds"] = previous_cases.get(identity, {}).get("wall_seconds")
        row["cleanup_leftovers"] = cleanup_inventory(library)
        if diagnostic_proxy is not None:
            row["real_ocr_diagnostics"] = diagnostic_proxy.snapshot()
            if args.ocr_provider == "vision":
                row["real_ocr_diagnostics"]["worker_module"] = native_ocr_worker_provenance(fingerprint)
        if args.mode == "run" and args.asr_provider == "faster-whisper":
            row["actual_asr_runtime"] = loaded_asr_runtime(config)
        if row["cleanup_leftovers"]:
            row["status"] = "failed"
        if case_index == report["planned_case_count"] - 1:
            report["status"] = "measured" if all(r["status"] == "measured" for r in report["cases"]) else "has_failures"
        atomic_json(args.output, safe_report(report, args.workdir))
        print(f"{identity}: {row['status']}", flush=True)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("run", "score"), default="run")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path, help="Original report for score mode; --output must be new")
    parser.add_argument("--once-token", type=Path, help="Exclusive campaign marker, retained even if extraction fails")
    parser.add_argument("--force", action="store_true", help="Extract the full source despite a local cost refusal; network limits remain")
    parser.add_argument("--installed-runtime", action="store_true", help="Use and verify the image-installed package instead of a mounted checkout")
    parser.add_argument("--expected-runtime-fingerprint", type=Path, help="SHA-256 file manifest and revision from the candidate checkout")
    parser.add_argument("--asr-language", default=None, type=lambda value: None if value.casefold() == "auto" else value,
                        help="Default: automatic; set en for the original development baseline")
    parser.add_argument("--ocr-diagnostics", action="store_true", help="Count real provider failure types without raw text or retries")
    apple = sys.platform == "darwin" and platform.machine() == "arm64"
    parser.add_argument("--asr-provider", choices=("mlx", "faster-whisper"), default="mlx" if apple else "faster-whisper")
    parser.add_argument("--ocr-provider", choices=("vision", "tesseract"), default="vision" if apple else "tesseract")
    args = parser.parse_args(argv)
    try:
        return 0 if benchmark(args)["status"] == "measured" else 1
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
