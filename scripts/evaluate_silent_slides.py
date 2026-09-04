#!/usr/bin/env python3
"""Run the real visual pipeline on a generated silent four-slide video."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import evidence, pipeline  # noqa: E402
from clipmind.demo import build_sample_video  # noqa: E402
from clipmind.jobs import JobStore  # noqa: E402



async def evaluate() -> dict:
    with tempfile.TemporaryDirectory(prefix="clipmind-silent-eval-") as tempdir:
        root = Path(tempdir)
        video = build_sample_video(root)
        out_dir = root / "out"

        # Submit the real file. A `local://` pseudo-URI is not a source the
        # adapter registry recognizes, so stubbing the fetch never ran: the
        # job was rejected before reaching it, and this gate stayed red.
        store = JobStore(out_dir)
        job = store.submit(str(video), "Synthetic silent slides")
        await asyncio.gather(*list(store._tasks))
        await store.close()
        if job.status != "done":
            raise RuntimeError(job.error or "silent slide fixture failed")
        workdir = store.workdir(job.id)
        result = job.result or {}
        manifest = evidence.load_complete_pack(workdir)
        ocr = [
            json.loads(line)
            for line in (workdir / "ocr.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        recognized = " ".join(record["text"] for record in ocr).upper()
        checks = {
            "transcript_explicitly_unavailable": (
                manifest["completeness"]["transcript"] == "unavailable"
                and manifest["diagnostics"]["asr_error"] == "no audio track"
            ),
            "ocr_complete": manifest["completeness"]["ocr"] == "complete",
            "all_four_slides_recognized": all(
                word in recognized for word in ("ALPHA", "BETA", "GAMMA", "DELTA")
            ),
            "visual_evidence_present": manifest["counts"]["canonical_visual_states"] >= 4,
            "evidence_pack_complete": manifest["status"] == "complete",
        }
        return {
            "fixture": "generated-silent-four-slide-video",
            "passed": all(checks.values()),
            "checks": checks,
            "counts": manifest["counts"],
            "completeness": manifest["completeness"],
            "diagnostics": manifest["diagnostics"],
            "recognized_text": recognized,
            "timings": manifest.get("timings", {}),
            "result_title": result["title"],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
