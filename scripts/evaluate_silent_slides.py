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
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import evidence, pipeline  # noqa: E402
from clipmind.fetch import Media  # noqa: E402
from clipmind.jobs import JobStore  # noqa: E402


def make_video(root: Path) -> Path:
    font = ImageFont.truetype("Arial.ttf", 68)
    labels = ("ALPHA PLAN", "BETA SYSTEM", "GAMMA DATA", "DELTA REVIEW")
    colors = ("#28536b", "#7b2d26", "#355834", "#654f6f")
    slides = []
    for index, (label, color) in enumerate(zip(labels, colors)):
        image = Image.new("RGB", (1280, 720), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 170 + index * 90, 720), fill=color)
        draw.text((260, 280), label, fill="black", font=font)
        path = root / f"slide-{index}.png"
        image.save(path)
        slides.append(path)
    concat = root / "slides.txt"
    concat.write_text(
        "".join(f"file '{path}'\nduration 1.5\n" for path in slides)
        + f"file '{slides[-1]}'\n",
        encoding="utf-8",
    )
    video = root / "silent-slides.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", str(video),
        ],
        check=True,
    )
    return video


async def evaluate() -> dict:
    with tempfile.TemporaryDirectory(prefix="clipmind-silent-eval-") as tempdir:
        root = Path(tempdir)
        video = make_video(root)
        out_dir = root / "out"

        async def local_fetch(url, destination, on_note=None):
            return Media(
                video,
                {
                    "id": "synthetic-silent-slides",
                    "title": "Synthetic silent slides",
                    "duration": 6.0,
                    "webpage_url": "local://synthetic-silent-slides",
                    "_clipmind_strategy": "generated evaluation fixture",
                },
            )

        with patch.object(pipeline, "fetch", new=local_fetch):
            store = JobStore(out_dir)
            job = store.submit(
                "local://synthetic-silent-slides", "Synthetic silent slides"
            )
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
