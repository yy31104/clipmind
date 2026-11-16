#!/usr/bin/env python3
"""Extract the complete licensed talk used by the real ClipMind demo.

This is an actual SDK run, not a precomputed or simulated Evidence Pack.
The temporary video is owned by the production acquisition workspace and
removed by the normal pipeline. The resulting transcript/images are retained.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipmind import acquisition  # noqa: E402
from clipmind.config import Settings  # noqa: E402
from clipmind.sdk import ClipMind  # noqa: E402


async def run(workdir: Path) -> dict:
    corpus = json.loads((ROOT / "docs/data/real-talk-cases.json").read_text())
    source = next(case for case in corpus["cases"] if case["case_id"] == corpus["demo_case_id"])
    config = replace(
        Settings(), asr_language="en", cookie_sources=("-",), cookie_file=None,
        fetch_format="eng-h264-hd/best[language=eng]/best",
        keep_source_video=False, knowledge_base_inbox=None,
        max_videos=1, max_fetch=1, max_asr=1, max_ocr=2,
    )
    started = time.perf_counter()
    client = ClipMind(workdir / "packs", config=config)

    def progress(event: dict) -> None:
        print(json.dumps({key: event.get(key) for key in ("id", "status", "stage", "progress", "note")}), flush=True)

    packs = await client.analyze(source["page_url"], force=True, progress=progress)
    if len(packs) != 1:
        raise RuntimeError("The single-talk demo must produce exactly one pack")
    pack = packs[0]
    if pack.source["title"] != source["title"]:
        raise RuntimeError("The acquired talk title does not match the declared source")
    if abs(float(pack.source["duration"]) - source["duration_seconds"]) > 2:
        raise RuntimeError("The acquired talk duration does not match the declared source")
    leftovers = acquisition.leftovers(pack.root)
    result = {
        "source": {key: source[key] for key in ("case_id", "title", "authors", "page_url", "license", "license_url", "duration_seconds")},
        "scope": "complete original talk, not the three-minute benchmark excerpt",
        "pack": pack.summary(), "manifest": pack.manifest,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "acquisition_leftovers": leftovers,
        "passed": not leftovers and all(pack.manifest["completeness"][kind] == "complete" for kind in ("transcript", "ocr", "visual_states")),
    }
    target = workdir / "demo-run.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--full-talk", required=True, action="store_true", help="Explicitly authorize complete local ASR/OCR despite the preflight estimate")
    args = parser.parse_args()
    workdir = args.workdir.expanduser().resolve()
    if workdir.is_relative_to(ROOT):
        parser.error("Keep downloaded media and packs outside the source checkout")
    workdir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(workdir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
