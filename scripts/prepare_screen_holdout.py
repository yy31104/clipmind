#!/usr/bin/env python3
"""Prepare the licensed full-talk screen holdout without running ClipMind."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def prepare(case: dict, workdir: Path) -> None:
    directory = workdir / case["case_id"]
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "original.webm"
    if not source.exists():
        temporary = directory / "original.webm.part"
        request = urllib.request.Request(
            case["source_url"], headers={"User-Agent": "ClipMindScreenBenchmark/1.4"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            with temporary.open("xb") as stream:
                while block := response.read(1024 * 1024):
                    stream.write(block)
        temporary.replace(source)
    if (source.stat().st_size != case["original_bytes"] or
            digest(source, "sha1") != case["original_sha1"] or
            digest(source) != case["original_sha256"]):
        raise ValueError(f"{case['case_id']}: publisher source bytes differ")
    target = directory / "input.mp4"
    if not target.exists():
        subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-threads", "2", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-threads", "2",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target),
        ], check=True)
    if digest(target) != case["input_sha256"]:
        raise ValueError(
            f"{case['case_id']}: transcode differs from the frozen input; "
            "keep this file for inspection and use the release input archive "
            "for exact scoring. Encoder versions can change output bytes."
        )
    (directory / "ATTRIBUTION.txt").write_text(
        case["attribution"] + "\n" + case["source_page"] + "\n" +
        case["license_url"] + "\n" + case["transformation"] + "\n",
        encoding="utf-8",
    )
    print(f"{case['case_id']}: original and frozen input checksums match")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("docs/data/v140-holdout-cases.json"))
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    workdir = args.workdir.resolve()
    if workdir.is_relative_to(repository):
        parser.error("--workdir must be outside the checkout")
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    for case in corpus["cases"]:
        identity = case["case_id"]
        if not identity.replace("-", "").replace("_", "").isalnum():
            parser.error("invalid case identity")
        prepare(case, workdir)


if __name__ == "__main__":
    main()
