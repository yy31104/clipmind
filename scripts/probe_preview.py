#!/usr/bin/env python3
"""Probe content-driven scene thresholds against a completed Evidence Pack."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import media  # noqa: E402


def chars(text: str) -> set[str]:
    return {character for character in text if not character.isspace()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pack", type=Path)
    parser.add_argument("--thresholds", type=int, nargs="+", default=[16, 20, 24, 28, 32])
    parser.add_argument("--detector-dir", type=Path)
    args = parser.parse_args()

    timeline = [
        json.loads(line)
        for line in (args.pack / "visual_timeline.jsonl").read_text().splitlines()
    ]
    ocr = {
        row["id"]: row
        for row in (
            json.loads(line)
            for line in (args.pack / "ocr.jsonl").read_text().splitlines()
        )
    }
    candidates = [
        row
        for row in timeline
        if "build_group_id" not in row
        or row["build_position"] == row["build_size"] - 1
    ]
    detector_hashes = None
    if args.detector_dir:
        detector_frames = [
            media.Frame(index, index / 2.0, path)
            for index, path in enumerate(sorted(args.detector_dir.glob("*.jpg")))
        ]
        detector_hashes = [frame.phash for frame in media.dedupe(detector_frames)]
        if len(detector_hashes) != len(timeline):
            raise SystemExit("detector/canonical frame counts do not match")
    timeline_hashes = {
        row["id"]: detector_hashes[position]
        if detector_hashes is not None
        else media.dhash(args.pack / row["file"])
        for position, row in enumerate(timeline)
    }
    for row in candidates:
        row["_hash"] = timeline_hashes[row["id"]]
        row["_chars"] = chars(ocr[row["ocr_ref"]]["text"])
    all_chars = set().union(*(chars(row["text"]) for row in ocr.values())) if ocr else set()

    results = []
    for threshold in args.thresholds:
        scenes: list[list[dict]] = []
        for row in candidates:
            if not scenes or media.hamming(scenes[-1][0]["_hash"], row["_hash"]) >= threshold:
                scenes.append([row])
            else:
                scenes[-1].append(row)
        representatives = []
        for scene in scenes:
            richest = max(scene, key=lambda row: (len(row["_chars"]), row["start"]))
            latest = scene[-1]
            representatives.append(
                latest
                if len(latest["_chars"]) >= 0.6 * len(richest["_chars"])
                else richest
            )
        kept_chars = set().union(*(row["_chars"] for row in representatives)) if representatives else set()
        results.append(
            {
                "threshold": threshold,
                "preview_count": len(representatives),
                "ocr_character_coverage": round(
                    len(kept_chars) / max(len(all_chars), 1), 4
                ),
                "timestamps": [row["start"] for row in representatives],
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
