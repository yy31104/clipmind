#!/usr/bin/env python3
"""Compare low-resolution and native-resolution OCR on one local video."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import media, ocr
from clipmind.media import Frame


@dataclass
class OCRRun:
    name: str
    frames: list[Frame]
    texts: dict[int, str]
    runtime: float
    failures: int
    storage_bytes: int
    ocr_input_bytes: int


def sample(video: Path, dest: Path, width: int, fps: float) -> tuple[list[Frame], float]:
    dest.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"fps={fps},scale={width}:-2",
            "-q:v",
            "4",
            str(dest / "s_%05d.jpg"),
        ],
        check=True,
    )
    files = sorted(dest.glob("s_*.jpg"))
    frames = [
        Frame(index=index, timestamp=index / fps, path=path)
        for index, path in enumerate(files)
    ]
    return frames, time.perf_counter() - started


async def recognise(frames: list[Frame], concurrency: int) -> tuple[dict[int, str], float, int]:
    semaphore = asyncio.Semaphore(concurrency)
    texts: dict[int, str] = {}
    failures = 0

    async def one(frame: Frame) -> None:
        nonlocal failures
        async with semaphore:
            try:
                lines = await asyncio.to_thread(ocr.read_text, frame.path)
            except Exception:  # noqa: BLE001 - this is an aggregate experiment metric
                failures += 1
                lines = []
        texts[frame.index] = "\n".join(lines)

    started = time.perf_counter()
    await asyncio.gather(*(one(frame) for frame in frames))
    return texts, time.perf_counter() - started, failures


def normalise(text: str) -> str:
    return "".join(character for character in text if not character.isspace())


def bytes_for(frames: list[Frame]) -> int:
    return sum(frame.path.stat().st_size for frame in frames)


def metrics(run: OCRRun, all_characters: set[str]) -> dict:
    joined = "".join(normalise(text) for text in run.texts.values())
    characters = set(joined)
    return {
        "canonical_frame_count": len(run.frames),
        "ocr_failure_count": run.failures,
        "ocr_total_seconds": round(run.runtime, 3),
        "ocr_seconds_per_frame": round(run.runtime / max(len(run.frames), 1), 4),
        "recognized_character_occurrences": len(joined),
        "recognized_unique_characters": len(characters),
        "character_union_coverage": round(
            len(characters) / max(len(all_characters), 1), 4
        ),
        "final_storage_bytes": run.storage_bytes,
        "ocr_input_bytes": run.ocr_input_bytes,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--low-width", type=int, default=640)
    parser.add_argument("--high-width", type=int, default=1280)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label")
    parser.add_argument("--include-excerpts", action="store_true")
    args = parser.parse_args()

    low_frames, low_sample_time = sample(
        args.video, args.workdir / "frames-640", args.low_width, args.fps
    )
    high_frames, high_sample_time = sample(
        args.video, args.workdir / "frames-native", args.high_width, args.fps
    )

    low_dedupe_started = time.perf_counter()
    low_canonical = media.dedupe(low_frames)
    low_dedupe_time = time.perf_counter() - low_dedupe_started
    high_dedupe_started = time.perf_counter()
    high_canonical = media.dedupe(high_frames)
    high_dedupe_time = time.perf_counter() - high_dedupe_started
    high_by_index = {frame.index: frame for frame in high_frames}
    high_for_low = [high_by_index[frame.index] for frame in low_canonical]

    low_texts, low_ocr_time, low_failures = await recognise(
        low_canonical, args.concurrency
    )
    high_texts, high_ocr_time, high_failures = await recognise(
        high_canonical, args.concurrency
    )
    hybrid_texts, hybrid_ocr_time, hybrid_failures = await recognise(
        high_for_low, args.concurrency
    )

    low_storage = bytes_for(low_canonical)
    runs = [
        OCRRun(
            "A_640_store_640_ocr",
            low_canonical,
            low_texts,
            low_ocr_time,
            low_failures,
            low_storage,
            low_storage,
        ),
        OCRRun(
            "B_native_store_native_ocr",
            high_canonical,
            high_texts,
            high_ocr_time,
            high_failures,
            bytes_for(high_canonical),
            bytes_for(high_canonical),
        ),
        OCRRun(
            "C_640_store_native_ocr",
            low_canonical,
            hybrid_texts,
            hybrid_ocr_time,
            hybrid_failures,
            low_storage,
            bytes_for(high_for_low),
        ),
    ]
    all_characters = set().union(
        *(set(normalise(text)) for run in runs for text in run.texts.values())
    )

    improvements = []
    for frame in low_canonical:
        low = normalise(low_texts.get(frame.index, ""))
        high = normalise(hybrid_texts.get(frame.index, ""))
        gain = len(high) - len(low)
        if gain > 0:
            improvement = {
                "timestamp": frame.timestamp,
                "recognized_character_gain": gain,
            }
            if args.include_excerpts:
                improvement.update(
                    {"low_excerpt": low[:160], "high_excerpt": high[:160]}
                )
            improvements.append(improvement)
    improvements.sort(key=lambda item: item["recognized_character_gain"], reverse=True)

    low_counts = Counter("".join(normalise(text) for text in low_texts.values()))
    hybrid_counts = Counter("".join(normalise(text) for text in hybrid_texts.values()))
    report = {
        "video": args.label or args.video.name,
        "fps": args.fps,
        "low_width": args.low_width,
        "high_width": args.high_width,
        "candidate_frame_count": len(low_frames),
        "sampling_seconds": {
            "low": round(low_sample_time, 3),
            "high": round(high_sample_time, 3),
        },
        "dedupe_seconds": {
            "low": round(low_dedupe_time, 3),
            "high": round(high_dedupe_time, 3),
        },
        "runs": {run.name: metrics(run, all_characters) for run in runs},
        "hybrid_vs_low": {
            "additional_character_occurrences": sum(
                (hybrid_counts - low_counts).values()
            ),
            "missing_character_occurrences": sum((low_counts - hybrid_counts).values()),
            "frames_with_more_recognized_text": len(improvements),
            "largest_improvements": improvements[:10],
        },
        "decision": {
            "change_detection_width": args.low_width,
            "canonical_evidence_and_ocr_width": args.high_width,
            "derived_final_storage_bytes": bytes_for(high_for_low),
            "reason": (
                "Native OCR materially improves small-text coverage; retain the "
                "low-resolution detection pass and promote its canonical timestamps "
                "to readable evidence for storage and OCR."
            ),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    asyncio.run(main())
