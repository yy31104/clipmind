#!/usr/bin/env python3
"""Run source-annotated real-talk excerpts through the production SDK.

Labels must be frozen from source frames before extraction. This measures
canonical state boundaries, end-to-end ROI OCR, and agreement with publisher
captions, not a semantic page classifier or human transcription accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
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


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_code_fingerprint() -> dict:
    """Hash actual tracked runtime bytes, including local uncommitted edits."""
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "clipmind"], cwd=REPO)
    names = [name.decode("utf-8") for name in tracked.split(b"\0") if name]
    files = {name: file_digest(REPO / name) for name in sorted(names)
             if Path(name).suffix == ".py" or name.startswith("clipmind/web/")}
    serialized = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"sha256": hashlib.sha256(serialized).hexdigest(), "files": files}


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
        identity = case["case_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity) or identity in seen:
            raise ValueError(f"invalid or duplicate case_id: {identity}")
        seen.add(identity)
        label = labels.get("cases", {}).get(identity, {})
        if not isinstance(label, dict) or label.get("reviewed") is not True:
            raise ValueError(f"{identity}: source annotations are pending")
        source = workdir / identity / "input.mp4"
        if (not source.resolve().is_relative_to(workdir.resolve()) or not source.is_file() or
                label.get("input_sha256") != file_digest(source)):
            raise ValueError(f"{identity}: labels are not bound to these input bytes")
        captions = workdir / identity / "captions.vtt"
        if not captions.is_file():
            raise ValueError(f"{identity}: publisher captions are missing")
        preparation = workdir / identity / "metadata.json"
        if preparation.is_file():
            expected = json.loads(preparation.read_text(encoding="utf-8")).get("sha256", {}).get("captions.vtt")
            if not isinstance(expected, str) or expected != file_digest(captions):
                raise ValueError(f"{identity}: publisher captions differ from prepared source bytes")
        duration = case["sample_end_seconds"] - case["sample_start_seconds"]
        if not 0 < duration <= 180 or label.get("review_start") != 0 or label.get("review_end") != duration:
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


def score_pack(pack, case: dict, label: dict, captions: Path) -> dict:
    completeness = pack.manifest.get("completeness", {})
    changes = [float(state["start"]) for state in pack.visual_timeline
               if label["review_start"] < float(state["start"]) < label["review_end"]]
    ocr = []
    records = {record["visual_state_ref"]: record for record in pack.ocr}
    for reference in label["ocr"]:
        _, state = pack.frame(timestamp=reference["timestamp"])
        record = records.get(state["id"], {})
        if record.get("error") or completeness.get("ocr") == "unavailable":
            measured = {"status": "failed", "value": None, "reason": record.get("error") or "OCR unavailable"}
        else:
            measured = edit_score(reference["text"], roi_text(record.get("layout", []), reference["roi"]))
        ocr.append(reference | measured | {"visual_state_id": state["id"],
                   "frame_interval": [state["start"], state["end"]]})
    speech = caption_score(vtt_cues(captions.read_text(encoding="utf-8-sig")), pack.transcript,
                           case["sample_start_seconds"], case["sample_end_seconds"],
                           completeness.get("transcript", "unavailable"))
    ocr_measured = [row for row in ocr if row["status"] == "measured"]
    ocr_units = sum(row["reference_units"] for row in ocr_measured)
    ocr_errors = sum(row["errors"] for row in ocr_measured)
    boundaries = boundary_score(label["slide_changes"], changes)
    if label.get("slide_metric_applicable") is False:
        boundaries = {"status": "not_applicable", "reason": label.get("slide_metric_reason") or label.get("notes") or "source has no slide sequence",
                      "prediction_count": len(changes), "recall": None, "precision": None}
    return {"completeness": completeness, "diagnostics": pack.manifest.get("diagnostics", {}),
            "timings": pack.manifest.get("timings", {}), "counts": pack.manifest.get("counts", {}),
            "canonical_boundary_score": boundaries,
            "ocr_references": ocr, "roi_ocr_cer": ocr_errors / ocr_units if ocr_units else None,
            "roi_ocr_reference_units": ocr_units, "roi_ocr_errors": ocr_errors,
            "roi_ocr_failed_references": sum(row["status"] == "failed" for row in ocr),
            "publisher_caption_agreement": speech}


def benchmark(args) -> dict:
    from clipmind import __version__
    from clipmind.config import Settings
    from clipmind.sdk import ClipMind, EvidencePack

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    validate_inputs(corpus, labels, args.workdir)
    edit_score("dependency check", "dependency check", speech=True)
    config = Settings(cookie_sources=("-",), cookie_file=None, asr_language="en",
                      asr_provider=args.asr_provider, ocr_provider=args.ocr_provider,
                      ocr_languages=("en-US",), tesseract_languages="eng",
                      max_videos=1, max_fetch=1, max_asr=1, max_ocr=2, keep_source_video=False,
                      knowledge_base_inbox=None, douyin_browser_enabled=False)
    previous = json.loads(args.output.read_text()) if args.mode == "score" else {}
    if previous and (previous.get("labels_sha256") != file_digest(args.labels) or
                     previous.get("corpus_sha256") != file_digest(args.corpus)):
        raise ValueError("rescore requires the original frozen labels and corpus")
    previous_cases = {row["case_id"]: row for row in previous.get("cases", [])}
    if args.mode == "score":
        for case in corpus["cases"]:
            identity = case["case_id"]
            expected = previous_cases.get(identity, {}).get("captions_sha256")
            if expected is not None and expected != file_digest(args.workdir / identity / "captions.vtt"):
                raise ValueError(f"{identity}: rescore requires unchanged publisher captions")
    report = {"schema_version": 1, "mode": args.mode, "clipmind_version": __version__,
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
              "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True)),
              "runtime_code_fingerprint": runtime_code_fingerprint(),
              "dependency_versions": dependency_versions(), "tool_versions": tool_versions(),
              "evaluator_sha256": file_digest(Path(__file__)), "corpus_sha256": file_digest(args.corpus),
              "labels_sha256": file_digest(args.labels),
              "configuration": previous.get("configuration", asdict(config)),
              "status": "running", "planned_case_count": len(corpus["cases"]),
              "platform": platform.platform(), "python": sys.version,
              "metric_notes": {"boundaries": "all canonical state starts versus independently annotated informational page changes; not a semantic slide classifier",
                               "ocr": "source ROI versus retained-frame layout; NFKC, whitespace removed, case-sensitive CER; end-to-end retention plus OCR",
                               "speech": "NFKC/lowercase/punctuation-removed WER against publisher-generated captions; not human ground truth"},
              "cases": []}
    provenance_keys = ("code_revision", "working_tree_dirty", "runtime_code_fingerprint",
                       "dependency_versions", "tool_versions", "platform", "python", "configuration")
    report["extraction_provenance"] = (
        previous.get("extraction_provenance") or {key: previous.get(key) for key in provenance_keys}
        if args.mode == "score" else {key: report[key] for key in provenance_keys}
    )
    for case in corpus["cases"]:
        identity = case["case_id"]
        started = time.perf_counter()
        row = {"case_id": identity, "title": case["title"], "source_url": case["page_url"],
               "input_sha256": labels["cases"][identity]["input_sha256"],
               "captions_sha256": file_digest(args.workdir / identity / "captions.vtt"),
               "source_interval": [case["sample_start_seconds"], case["sample_end_seconds"]]}
        preparation = args.workdir / identity / "metadata.json"
        row["preparation_sha256"] = file_digest(preparation) if preparation.is_file() else None
        library = args.workdir / identity / "library"
        try:
            if args.mode == "run":
                packs = asyncio.run(ClipMind(library, config=config).analyze(
                    str(args.workdir / identity / "input.mp4"), reprocess=True))
                if len(packs) != 1:
                    raise ValueError(f"expected one complete pack, got {len(packs)}")
                pack = packs[0]
            else:
                path = Path(previous_cases[identity]["pack_path"]).resolve()
                if not path.is_relative_to(args.workdir.resolve()):
                    raise ValueError("rescore pack must be inside --workdir")
                pack = EvidencePack.open(path)
            row["pack_path"] = str(pack.root)
            # The production local adapter's source ID is the SHA-256 of its
            # acquired copy, not a separate content_sha256 manifest field.
            provenance = pack.manifest.get("source", {})
            if provenance.get("platform") != "local" or provenance.get("id") != row["input_sha256"]:
                raise ValueError("pack source does not match annotated input")
            row.update(score_pack(pack, case, labels["cases"][identity], args.workdir / identity / "captions.vtt"))
            healthy = all(row["completeness"].get(k) == "complete" for k in ("ocr", "transcript", "visual_states"))
            row["status"] = "measured" if healthy and not row["roi_ocr_failed_references"] else "incomplete"
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}", error_code=getattr(exc, "code", None))
        row["wall_seconds" if args.mode == "run" else "scoring_wall_seconds"] = round(time.perf_counter() - started, 3)
        if args.mode == "score":
            row["wall_seconds"] = previous_cases.get(identity, {}).get("wall_seconds")
        row["cleanup_leftovers"] = cleanup_inventory(library)
        if row["cleanup_leftovers"]:
            row["status"] = "failed"
        report["cases"].append(row)
        if len(report["cases"]) == report["planned_case_count"]:
            report["status"] = "measured" if all(r["status"] == "measured" for r in report["cases"]) else "has_failures"
        atomic_json(args.output, report)
        print(f"{identity}: {row['status']}", flush=True)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("run", "score"), default="run")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
