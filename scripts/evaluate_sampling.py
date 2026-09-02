#!/usr/bin/env python3
"""Measure whether the production sample rate misses stable visual states."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import media  # noqa: E402


@dataclass(frozen=True)
class Sample:
    timestamp: float
    phash: int


def sample(video: Path, directory: Path, *, fps: float, width: int) -> list[Sample]:
    directory.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-vf", f"fps={fps},scale={width}:-2",
            "-q:v", "4", str(directory / "s_%06d.jpg"),
        ],
        check=True,
    )
    return [
        Sample(index / fps, media.dhash(path))
        for index, path in enumerate(sorted(directory.glob("*.jpg")))
    ]


def stable_runs(
    samples: list[Sample], *, fps: float, threshold: int, minimum_seconds: float
) -> list[list[Sample]]:
    if not samples:
        return []
    runs: list[list[Sample]] = []
    current = [samples[0]]
    for frame in samples[1:]:
        if media.hamming(current[-1].phash, frame.phash) <= threshold:
            current.append(frame)
        else:
            if len(current) >= math.ceil(minimum_seconds * fps) + 1:
                runs.append(current)
            current = [frame]
    if len(current) >= math.ceil(minimum_seconds * fps) + 1:
        runs.append(current)
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--baseline-fps", type=float, default=2.0)
    parser.add_argument("--probe-fps", type=float, default=4.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--threshold", type=int, default=6)
    parser.add_argument("--minimum-stable-seconds", type=float, default=0.5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.probe_fps <= args.baseline_fps:
        parser.error("--probe-fps must exceed --baseline-fps")

    with tempfile.TemporaryDirectory(prefix="clipmind-sampling-") as tempdir:
        root = Path(tempdir)
        baseline = sample(
            args.video.resolve(), root / "baseline", fps=args.baseline_fps, width=args.width
        )
        probe = sample(
            args.video.resolve(), root / "probe", fps=args.probe_fps, width=args.width
        )
        runs = stable_runs(
            probe,
            fps=args.probe_fps,
            threshold=args.threshold,
            minimum_seconds=args.minimum_stable_seconds,
        )

    covered = []
    missed = []
    for run in runs:
        matches = [
            candidate
            for candidate in baseline
            if run[0].timestamp <= candidate.timestamp <= run[-1].timestamp
            and any(
                media.hamming(candidate.phash, probe_frame.phash) <= args.threshold
                for probe_frame in run
            )
        ]
        record = {
            "start": run[0].timestamp,
            "end": run[-1].timestamp,
            "duration": round(run[-1].timestamp - run[0].timestamp, 3),
            "probe_frames": len(run),
        }
        (covered if matches else missed).append(record)

    report = {
        "video": args.video.name,
        "configuration": {
            "baseline_fps": args.baseline_fps,
            "probe_fps": args.probe_fps,
            "width": args.width,
            "dhash_threshold": args.threshold,
            "minimum_stable_seconds": args.minimum_stable_seconds,
        },
        "baseline_frame_count": len(baseline),
        "probe_frame_count": len(probe),
        "stable_state_count": len(runs),
        "covered_stable_state_count": len(covered),
        "missed_stable_state_count": len(missed),
        "stable_state_coverage": round(len(covered) / max(len(runs), 1), 4),
        "missed": missed,
        "decision": "keep uniform sampling" if not missed else "investigate adaptive sampling",
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not missed else 1


if __name__ == "__main__":
    raise SystemExit(main())
