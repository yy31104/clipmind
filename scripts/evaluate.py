#!/usr/bin/env python3
"""Evaluate existing or freshly extracted Evidence Packs against real cases."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import evidence, media, pipeline  # noqa: E402
from clipmind.jobs import JobStore  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalise(text: str) -> set[str]:
    return {character for character in text if not character.isspace()}


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[round((len(ordered) - 1) * fraction)])


def runtime_context() -> dict:
    """Record the local tools that can affect a fresh extraction run."""
    try:
        completed = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=False,
            text=True,
        )
        ffmpeg_version = completed.stdout.splitlines()[0] if completed.stdout else None
    except OSError:
        ffmpeg_version = None
    return {
        "ffmpeg_version": ffmpeg_version,
        "providers": {
            "asr": {
                "id": "mlx-whisper",
                "available": importlib.util.find_spec("mlx_whisper") is not None,
            },
            "ocr": {
                "id": "apple-vision",
                "available": importlib.util.find_spec("Vision") is not None,
            },
        },
    }


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


def evaluate_case(
    case: dict,
    workdir: Path,
    *,
    mode: str = "existing",
    observed_source_sha256: str | None = None,
) -> dict:
    manifest = evidence.load_complete_pack(workdir)
    transcript = read_jsonl(workdir / "transcript.jsonl")
    ocr = read_jsonl(workdir / "ocr.jsonl")
    timeline = read_jsonl(workdir / "visual_timeline.jsonl")
    job = json.loads((workdir / "job.json").read_text(encoding="utf-8"))["job"]
    expected = case["expected"]
    baseline_sha = expected.get("source_content_sha256")
    observed_sha = observed_source_sha256 or manifest["source"].get(
        "content_sha256"
    )
    if mode == "existing":
        source_identity = "existing_pack_not_reextracted"
        exact_count_enforced = True
    elif baseline_sha and observed_sha:
        source_identity = "match" if baseline_sha == observed_sha else "changed"
        exact_count_enforced = source_identity == "match"
    else:
        source_identity = "unavailable"
        exact_count_enforced = False

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
    referenced_files = [workdir / row["file"] for row in timeline]
    temp_leftovers = [
        name
        for name in ("samples", "evidence_samples", "audio.wav", "source.mp4", "source.webm")
        if (workdir / name).exists()
    ]
    exact_count_check = (
        len(timeline) == expected["canonical_visual_state_count"]
        if exact_count_enforced
        else None
    )
    checks: dict[str, bool | None] = {
        "transcript_minimum": len(transcript) >= expected["minimum_transcript_segments"],
        "visual_state_minimum": len(timeline) >= expected["minimum_visual_states"],
        "canonical_visual_state_count_exact": exact_count_check,
        "ocr_available": not expected["requires_ocr"] or manifest["completeness"]["ocr"] == "complete",
        "preview_target": (
            len(preview_refs) / max(len(timeline), 1)
            <= expected["maximum_preview_ratio"]
        ),
        "cleanup": not temp_leftovers,
    }
    started = job.get("started_at")
    finished = job.get("finished_at")
    return {
        "id": case["id"],
        "source_id": case["source_id"],
        "content_type": case["content_type"],
        "pack": workdir.name,
        "passed": all(value is not False for value in checks.values()),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if passed is False],
        "not_applicable_checks": [
            name for name, passed in checks.items() if passed is None
        ],
        "source_identity": {
            "status": source_identity,
            "baseline_sha256": baseline_sha,
            "observed_sha256": observed_sha,
            "exact_count_enforced": exact_count_enforced,
        },
        "review_warnings": (
            ["source bytes changed; exact canonical count was not compared"]
            if source_identity == "changed"
            else ["source identity unavailable; exact canonical count was not compared"]
            if source_identity == "unavailable"
            else []
        ),
        "metrics": {
            "duration_seconds": job.get("result", {}).get("duration"),
            "end_to_end_seconds": round(float(finished) - float(started), 3)
            if started and finished
            else None,
            "source_duration_processing_ratio": round(
                float(job.get("result", {}).get("duration") or 0)
                / max(float(finished) - float(started), 0.001),
                4,
            ) if started and finished else None,
            "ocr_seconds": manifest.get("timings", {}).get("ocr_seconds"),
            "transcript_segment_count": len(transcript),
            "transcript_character_count": sum(len(row["text"]) for row in transcript),
            "transcript_completeness": manifest["completeness"]["transcript"],
            "asr_realtime_factor": round(
                float(manifest.get("timings", {}).get("asr_seconds"))
                / max(float(job.get("result", {}).get("duration") or 0), 0.001),
                4,
            ) if manifest.get("timings", {}).get("asr_seconds") is not None else None,
            "candidate_frame_count": manifest.get("counts", {}).get(
                "candidate_frames"
            ),
            "canonical_visual_state_count": len(timeline),
            "expected_canonical_visual_state_count": expected[
                "canonical_visual_state_count"
            ],
            "source_content_sha256": observed_sha,
            "dedupe_policy": {
                "algorithm": manifest["configuration"].get(
                    "dedupe_algorithm", "unrecorded"
                ),
                "threshold": manifest["configuration"].get("dedupe_threshold"),
                "detail_hash_size": manifest["configuration"].get(
                    "dedupe_detail_hash_size"
                ),
                "detail_threshold": manifest["configuration"].get(
                    "dedupe_detail_threshold"
                ),
            },
            "extraction_configuration": manifest["configuration"],
            "preview_visual_state_count": len(preview_refs),
            "preview_ratio": round(len(preview_refs) / max(len(timeline), 1), 4),
            "ocr_unique_character_count": len(all_chars),
            "ocr_runtime_per_frame": round(
                float(manifest.get("timings", {}).get("ocr_seconds"))
                / max(len(ocr), 1),
                4,
            ) if manifest.get("timings", {}).get("ocr_seconds") is not None else None,
            "visual_information_coverage": expected.get(
                "measured_stable_state_coverage"
            ),
            "canonical_artifact_coverage": round(
                sum(path.is_file() for path in referenced_files)
                / max(len(referenced_files), 1),
                4,
            ),
            "preview_ocr_character_coverage": round(
                len(preview_chars) / max(len(all_chars), 1), 4
            ),
            "duplicate_visual_state_rate": round(
                duplicate_count / max(len(distances), 1), 4
            ),
            "duplicate_pair_count": duplicate_count,
            "adjacent_pair_count": len(distances),
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


async def reextract_cases(
    cases: list[dict], out_dir: Path
) -> tuple[list[dict], dict[str, str]]:
    """Run every case into an empty library so no completed pack can be reused."""
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"re-extraction output must be empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    config = replace(pipeline.settings, keep_source_video=True)
    store = JobStore(out_dir, config=config)
    jobs = []
    source_hashes: dict[str, str] = {}
    try:
        jobs = [
            (
                case,
                store.submit(
                    case["url"],
                    f"Real-world evaluation: {case['id']}",
                ),
            )
            for case in cases
        ]
        await asyncio.gather(*list(store._tasks))
        for case, job in jobs:
            sources = sorted(
                path
                for path in store.workdir(job.id).glob("source.*")
                if path.name != "source.json" and path.is_file()
            )
            if job.status == "done" and len(sources) == 1:
                source_hashes[case["source_id"]] = sha256(sources[0])
    finally:
        await store.close()
        for _, job in jobs:
            pipeline.cleanup_temporary(store.workdir(job.id), keep_source=False)

    return (
        [
            {
                "id": case["id"],
                "source_id": case["source_id"],
                "job_id": job.id,
                "status": job.status,
                "error": job.error,
                "error_code": job.error_code,
                "error_action": job.error_action,
            }
            for case, job in jobs
            if job.status != "done"
        ],
        source_hashes,
    )


def evaluate_suite(
    suite: dict,
    out_dir: Path,
    *,
    mode: str,
    extraction_failures: list[dict] | None = None,
    source_hashes: dict[str, str] | None = None,
) -> dict:
    """Build one report and state whether packs came from disk or this run."""
    index = pack_index(out_dir)
    results = []
    missing = []
    for case in suite["cases"]:
        workdir = index.get(case["source_id"])
        if workdir is None:
            missing.append(case["id"])
            continue
        results.append(
            evaluate_case(
                case,
                workdir,
                mode=mode,
                observed_source_sha256=(source_hashes or {}).get(case["source_id"]),
            )
        )
    duplicate_pairs = sum(
        result["metrics"]["duplicate_pair_count"] for result in results
    )
    adjacent_pairs = sum(
        result["metrics"]["adjacent_pair_count"] for result in results
    )
    failures = list(extraction_failures or [])
    return {
        "suite_version": suite["version"],
        "mode": mode,
        "pack_origin": (
            "fresh_pipeline_run" if mode == "reextract" else "existing_completed_pack"
        ),
        "runtime": runtime_context(),
        "case_count": len(suite["cases"]),
        "completed_pack_count": len(results),
        "ingestion_success_rate": round(len(results) / max(len(suite["cases"]), 1), 4),
        "passed": (
            not missing
            and not failures
            and all(result["passed"] for result in results)
        ),
        "missing": missing,
        "extraction_failures": failures,
        "aggregate": {
            "duplicate_visual_state_rate": round(
                duplicate_pairs / max(adjacent_pairs, 1), 4
            ),
            "disk_cleanup_success_rate": round(
                sum(result["checks"]["cleanup"] for result in results)
                / max(len(results), 1),
                4,
            ),
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "eval" / "cases.json")
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--mode",
        choices=("existing", "reextract"),
        default="existing",
        help="audit completed packs or run every source through the current pipeline",
    )
    parser.add_argument(
        "--run-out",
        type=Path,
        help="retain fresh re-extraction packs here; the directory must be empty",
    )
    args = parser.parse_args()
    if args.run_out is not None and args.mode != "reextract":
        parser.error("--run-out requires --mode reextract")

    suite = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.mode == "reextract" and args.run_out is not None:
        failures, source_hashes = asyncio.run(
            reextract_cases(suite["cases"], args.run_out)
        )
        report = evaluate_suite(
            suite,
            args.run_out,
            mode="reextract",
            extraction_failures=failures,
            source_hashes=source_hashes,
        )
    elif args.mode == "reextract":
        with tempfile.TemporaryDirectory(prefix="clipmind-real-eval-") as tempdir:
            run_out = Path(tempdir) / "out"
            failures, source_hashes = asyncio.run(
                reextract_cases(suite["cases"], run_out)
            )
            report = evaluate_suite(
                suite,
                run_out,
                mode="reextract",
                extraction_failures=failures,
                source_hashes=source_hashes,
            )
    else:
        report = evaluate_suite(suite, args.out, mode="existing")
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
