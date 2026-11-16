#!/usr/bin/env python3
"""Prepare declared public-talk excerpts and independent source contact sheets.

Nothing calls ClipMind's extraction pipeline. All downloaded material stays in
the required external work directory. Missing HTTP seeking is an error, never
permission to fetch the entire original talk. Publisher captions are preserved
unmodified, with their original full-talk timestamps and provenance.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "docs/data/real-talk-cases.json"
USER_AGENT = "ClipMind-Real-Talk-Evaluation/1.0"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Response exceeds {limit} bytes: {url}")
    return data


def verify_http_seeking(url: str) -> dict:
    request = urllib.request.Request(url, headers={
        "Range": "bytes=0-0", "Accept-Encoding": "identity", "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        content_range = response.headers.get("Content-Range", "")
        if response.status != 206 or not re.fullmatch(r"bytes 0-0/\d+", content_range):
            raise ValueError("Source does not honor HTTP byte seeking; refusing full-download fallback")
        if len(response.read(2)) != 1:
            raise ValueError("Invalid one-byte HTTP range response")
        return {"status": response.status, "content_range": content_range}


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as error:
        diagnostic = (error.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{command[0]} exited {error.returncode}: {diagnostic[-8000:]}") from error
    except subprocess.TimeoutExpired as error:
        diagnostic = (error.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{command[0]} exceeded {timeout} seconds: {diagnostic[-8000:]}") from error


def validate_clip(path: Path, expected_duration: float) -> dict:
    result = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height", "-of", "json", str(path),
    ], 30)
    info = json.loads(result.stdout)
    duration = float(info["format"]["duration"])
    if not math.isfinite(duration) or abs(duration - expected_duration) > 0.2:
        raise ValueError(f"Clip duration {duration} differs from {expected_duration} by more than 0.2 s")
    kinds = {stream["codec_type"] for stream in info["streams"]}
    if not {"audio", "video"} <= kinds:
        raise ValueError("Prepared clip must contain both video and speech audio streams")
    return info


def download_clip(case: dict, target: Path) -> dict:
    start = float(case["sample_start_seconds"])
    duration = float(case["sample_end_seconds"]) - start
    if duration != 180 or start < 0 or start + duration > float(case["duration_seconds"]):
        raise ValueError("Manifest must declare a valid fixed 180-second source excerpt")
    seeking = verify_http_seeking(case["video_url"])
    temporary = target.with_name("input.tmp.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-rw_timeout", "15000000", "-seekable", "1", "-ss", str(start),
        "-i", case["video_url"], "-t", str(duration),
        "-map", "0:v:0", "-map", "0:a:0", "-vf", "scale=1280:-2",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(temporary),
    ]
    try:
        run(command, 600)
        info = validate_clip(temporary, duration)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"http_seek_check": seeking, "ffmpeg_command": command, "ffprobe": info}


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def make_contact_sheets(clip: Path, output: Path, source_start: float) -> list[str]:
    """Uniform source sampling; no production frame selection or OCR involved."""
    sheets = []
    with tempfile.TemporaryDirectory(prefix=".source-frames-", dir=output) as temp:
        frame_dir = Path(temp)
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(clip), "-vf",
            "fps=1:start_time=0,scale=240:136:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=240:136:(ow-iw)/2:(oh-ih)/2",
            "-frames:v", "180", "-q:v", "2", str(frame_dir / "%03d.jpg"),
        ], 180)
        frames = sorted(frame_dir.glob("*.jpg"))
        if len(frames) != 180:
            raise ValueError(f"Expected 180 uniform source frames, found {len(frames)}")
        for sheet_index in range(3):
            sheet = Image.new("RGB", (6 * 240, 10 * 156), "white")
            draw = ImageDraw.Draw(sheet)
            for cell in range(60):
                index = sheet_index * 60 + cell
                x, y = (cell % 6) * 240, (cell // 6) * 156
                with Image.open(frames[index]) as frame:
                    sheet.paste(frame, (x, y))
                label = f"clip {timestamp(index)} | source {timestamp(source_start + index)}"
                draw.text((x + 3, y + 138), label, fill="black")
            name = f"contact-{sheet_index + 1:02}.jpg"
            sheet.save(output / name, quality=94)
            sheets.append(name)
    return sheets


def prepare_case(case: dict, workdir: Path) -> dict:
    case_id = case["case_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", case_id):
        raise ValueError("Unsafe case ID")
    destination = workdir / case_id
    if destination.exists():
        record = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
        if record["case"] != case or any(
            sha256(destination / name) != digest for name, digest in record["sha256"].items()
        ):
            raise ValueError(f"Existing prepared case differs or is damaged: {destination}")
        validate_clip(destination / "input.mp4", 180)
        return {"case_id": case_id, "status": "already_prepared", "directory": str(destination)}

    # Publish the entire directory only after every artifact and hash is ready.
    with tempfile.TemporaryDirectory(prefix=f".{case_id}-preparing-", dir=workdir) as temporary:
        stage = Path(temporary) / "case"
        stage.mkdir()
        publisher_bytes = fetch(case["metadata_url"], 4 * 1024 * 1024)
        publisher = json.loads(publisher_bytes)
        if publisher.get("guid") != case["guid"] or case["license_proof_text"] not in publisher.get("description", ""):
            raise ValueError("Publisher identity/license proof no longer matches manifest")
        if case["video_url"] not in {r["recording_url"] for r in publisher["recordings"]}:
            raise ValueError("Selected video is absent from the publisher's current recording list")
        (stage / "publisher-metadata.json").write_bytes(publisher_bytes)
        captions = fetch(case["captions_url"], 8 * 1024 * 1024)
        if not captions.decode("utf-8-sig").startswith("WEBVTT"):
            raise ValueError("Publisher caption response is not UTF-8 WebVTT")
        (stage / "captions.vtt").write_bytes(captions)
        preparation = download_clip(case, stage / "input.mp4")
        preparation["contact_sheets"] = make_contact_sheets(
            stage / "input.mp4", stage, float(case["sample_start_seconds"]),
        )
        preparation["contact_sampling"] = {
            "fps": 1, "thumbnail_size": [240, 136], "grid": [6, 10],
            "timestamp_precision": "Nominal one-second source samples, not frame-exact boundary labels",
            "source": "Prepared source clip, independent of ClipMind output",
        }
        record = {
            "case": case, "prepared_at": datetime.now(timezone.utc).isoformat(),
            "preparation": preparation,
            "sha256": {p.name: sha256(p) for p in sorted(stage.iterdir()) if p.is_file()},
        }
        (stage / "metadata.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        stage.replace(destination)
    return {"case_id": case_id, "status": "prepared", "directory": str(destination)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--workdir", required=True, type=Path, help="External media/annotation work directory")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", nargs="+", metavar="CASE_ID")
    selection.add_argument("--all", action="store_true", help="Prepare every declared case (also the default)")
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    workdir = args.workdir.expanduser().resolve()
    if workdir.is_relative_to(REPO_ROOT):
        parser.error("--workdir must be outside the checkout; media must not enter the repository")
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            parser.error(f"Required executable is unavailable: {executable}")
    cases = json.loads(args.corpus.read_text(encoding="utf-8"))["cases"]
    requested = set(args.case or [case["case_id"] for case in cases])
    unknown = requested - {case["case_id"] for case in cases}
    if unknown:
        parser.error(f"Unknown cases: {sorted(unknown)}")
    workdir.mkdir(parents=True, exist_ok=True)
    failed = False
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(prepare_case, case, workdir): case["case_id"] for case in cases if case["case_id"] in requested}
        for future in as_completed(futures):
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as error:
                failed = True
                print(json.dumps({"case_id": futures[future], "status": "failed", "error": str(error)}), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
