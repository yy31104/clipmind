#!/usr/bin/env python3
"""Evaluate completed local Evidence Packs against the checked-in real cases."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import evidence, media  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def normalise(text: str) -> set[str]:
    return {character for character in text if not character.isspace()}


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[round((len(ordered) - 1) * fraction)])


def pack_index(out_dir: Path) -> dict[str, Path]:
    newest: dict[str, tuple[float, Path]] = {}
    if not out_dir.exists():
        return {}
    for workdir in out_dir.iterdir():
        if not workdir.is_dir():
            continue
        try:
            manifest = evidence.load_complete_pack(workdir)
            source_id = str(manifest["source"]["id"])
            job = json.loads((workdir / "job.json").read_text(encoding="utf-8"))["job"]
            finished = float(job.get("finished_at") or 0)
        except (evidence.EvidencePackError, OSError, ValueError, KeyError, TypeError):
            continue
        if source_id not in newest or finished > newest[source_id][0]:
            newest[source_id] = (finished, workdir)
    return {source_id: item[1] for source_id, item in newest.items()}


def evaluate_case(case: dict, workdir: Path) -> dict:
    manifest = evidence.load_complete_pack(workdir)
    transcript = read_jsonl(workdir / "transcript.jsonl")
    ocr = read_jsonl(workdir / "ocr.jsonl")
    timeline = read_jsonl(workdir / "visual_timeline.jsonl")
    job = json.loads((workdir / "job.json").read_text(encoding="utf-8"))["job"]
    expected = case["expected"]

    all_chars = set().union(*(normalise(row["text"]) for row in ocr)) if ocr else set()
    preview_refs = {row["ocr_ref"] for row in timeline if row["in_preview"]}
    preview_chars = set().union(
        *(normalise(row["text"]) for row in ocr if row["id"] in preview_refs)
    ) if preview_refs else set()
    files = sorted((workdir / "visual_states" / "all").glob("*.jpg"))
    hashes = [media.dhash(path) for path in files]
    distances = [media.hamming(first, second) for first, second in zip(hashes, hashes[1:])]
    dedupe_threshold = int(manifest["configuration"]["dedupe_threshold"])
    duplicate_count = sum(distance <= dedupe_threshold for distance in distances)
    temp_leftovers = [
        name
        for name in ("samples", "evidence_samples", "audio.wav", "source.mp4", "source.webm")
        if (workdir / name).exists()
    ]
    checks = {
        "transcript_minimum": len(transcript) >= expected["minimum_transcript_segments"],
        "visual_state_minimum": len(timeline) >= expected["minimum_visual_states"],
        "ocr_available": not expected["requires_ocr"] or manifest["completeness"]["ocr"] == "complete",
        "preview_target": len(preview_refs) <= expected["maximum_preview_states"],
        "cleanup": not temp_leftovers,
    }
    started = job.get("started_at")
    finished = job.get("finished_at")
    return {
        "id": case["id"],
        "source_id": case["source_id"],
        "content_type": case["content_type"],
        "pack": workdir.name,
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": {
            "duration_seconds": job.get("result", {}).get("duration"),
            "end_to_end_seconds": round(float(finished) - float(started), 3)
            if started and finished
            else None,
            "transcript_segment_count": len(transcript),
            "transcript_character_count": sum(len(row["text"]) for row in transcript),
            "canonical_visual_state_count": len(timeline),
            "preview_visual_state_count": len(preview_refs),
            "preview_ratio": round(len(preview_refs) / max(len(timeline), 1), 4),
            "ocr_unique_character_count": len(all_chars),
            "preview_ocr_character_coverage": round(
                len(preview_chars) / max(len(all_chars), 1), 4
            ),
            "duplicate_visual_state_rate": round(
                duplicate_count / max(len(distances), 1), 4
            ),
            "adjacent_dhash_distance": {
                "p25": percentile(distances, 0.25),
                "median": statistics.median(distances) if distances else None,
                "p75": percentile(distances, 0.75),
            },
            "pack_bytes": sum(
                path.stat().st_size for path in workdir.rglob("*") if path.is_file()
            ),
            "temporary_leftovers": temp_leftovers,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "eval" / "cases.json")
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    suite = json.loads(args.cases.read_text(encoding="utf-8"))
    index = pack_index(args.out)
    results = []
    missing = []
    for case in suite["cases"]:
        workdir = index.get(case["source_id"])
        if workdir is None:
            missing.append(case["id"])
            continue
        results.append(evaluate_case(case, workdir))
    report = {
        "suite_version": suite["version"],
        "case_count": len(suite["cases"]),
        "completed_pack_count": len(results),
        "ingestion_success_rate": round(len(results) / max(len(suite["cases"]), 1), 4),
        "passed": not missing and all(result["passed"] for result in results),
        "missing": missing,
        "results": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
