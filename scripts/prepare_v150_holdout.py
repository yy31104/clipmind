#!/usr/bin/env python3
"""Prepare the v1.5.0 full-talk screen holdout without running ClipMind.

Downloads each declared recording into an external work directory, checks the
recorded checksums, normalizes it like the v1.4.0 holdout, and can draw
one-second source contact sheets for annotation. Nothing here imports the
extraction pipeline. Encoder versions can change the normalized bytes; the
release input archive is the scoring copy when they differ.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

USER_AGENT = "ClipMindScreenBenchmark/1.5"


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def download(url: str, target: Path) -> None:
    temporary = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
        shutil.copyfileobj(response, stream, 1024 * 1024)
    temporary.replace(target)


def acquire(case: dict, directory: Path) -> Path:
    acquisition = case["acquisition"]
    source = directory / acquisition["original_name"]
    if source.exists():
        return source
    if acquisition["method"] == "http":
        download(case["source_url"], source)
    elif acquisition["method"] == "yt-dlp":
        if shutil.which("yt-dlp") is None:
            raise RuntimeError("yt-dlp is required for this source")
        stem = source.with_suffix("")
        subprocess.run(["yt-dlp", "--no-progress", "-f", acquisition["format"],
                        "--merge-output-format", source.suffix.lstrip("."),
                        "-o", f"{stem}.%(ext)s", case["source_page"]], check=True)
    else:
        raise ValueError(f"unknown acquisition method: {acquisition['method']}")
    return source


def check(case: dict, source: Path) -> dict:
    found = {"original_bytes": source.stat().st_size, "original_sha256": digest(source)}
    if case.get("original_sha1") is not None:
        found["original_sha1"] = digest(source, "sha1")
    for key, value in found.items():
        expected = case.get(key)
        if expected is not None and expected != value:
            raise ValueError(f"{case['case_id']}: {key} differs from the manifest")
    return found


def normalize(source: Path, target: Path) -> None:
    if target.exists():
        return
    temporary = target.with_name("input.part.mp4")
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-threads", "2", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(temporary),
    ], check=True)
    temporary.replace(target)


def contact_sheets(video: Path, output: Path, duration: float) -> int:
    """One uniform source cell per second, 60 cells per sheet, no model output."""
    from PIL import Image, ImageDraw

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cells-", dir=output) as temporary:
        cells = Path(temporary)
        subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            "-vf", "setpts=PTS-STARTPTS,fps=1:start_time=0,scale=320:180",
            "-q:v", "3", str(cells / "%05d.jpg")], check=True)
        frames = sorted(cells.glob("*.jpg"))
        count = 0
        for sheet_index in range(0, len(frames), 60):
            sheet = Image.new("RGB", (6 * 320, 10 * 198), "white")
            draw = ImageDraw.Draw(sheet)
            for cell, frame in enumerate(frames[sheet_index:sheet_index + 60]):
                second = sheet_index + cell
                x, y = (cell % 6) * 320, (cell // 6) * 198
                with Image.open(frame) as image:
                    sheet.paste(image, (x, y))
                draw.text((x + 4, y + 182), f"{second // 60:02d}:{second % 60:02d}  ({second} s)", fill="black")
            sheet.save(output / f"contact-{sheet_index // 60 + 1:03d}.jpg", quality=90)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("docs/data/v150-holdout-cases.json"))
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--contact-sheets", action="store_true", help="Write one-second annotation sheets")
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
        directory = workdir / identity
        directory.mkdir(parents=True, exist_ok=True)
        found = check(case, acquire(case, directory))
        captions = case.get("captions_url")
        if captions and not (directory / "captions.vtt").exists():
            download(captions, directory / "captions.vtt")
        if captions and case.get("captions_sha256") not in (None, digest(directory / "captions.vtt")):
            raise ValueError(f"{identity}: publisher captions differ from the manifest")
        target = directory / "input.mp4"
        normalize(directory / case["acquisition"]["original_name"], target)
        found["input_sha256"] = digest(target)
        if case.get("input_sha256") not in (None, found["input_sha256"]):
            raise ValueError(f"{identity}: normalized input differs; use the release input archive for scoring")
        (directory / "ATTRIBUTION.txt").write_text(
            "\n".join([case["attribution"], case["source_page"], case["license_url"],
                       case["transformation"]]) + "\n", encoding="utf-8")
        if args.contact_sheets:
            found["contact_sheets"] = contact_sheets(target, directory / "contact", case["sample_end_seconds"])
        print(json.dumps({"case_id": identity, **found}), flush=True)


if __name__ == "__main__":
    main()
