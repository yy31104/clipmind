#!/usr/bin/env python3
"""Rebuild a derived preview from an existing complete Evidence Pack."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import evidence, media, visual_states  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def rebuild(workdir: Path) -> int:
    manifest = evidence.load_complete_pack(workdir)
    timeline = read_jsonl(workdir / "visual_timeline.jsonl")
    ocr_by_id = {row["id"]: row for row in read_jsonl(workdir / "ocr.jsonl")}

    frames = []
    for index, row in enumerate(timeline):
        ocr = ocr_by_id[row["ocr_ref"]]
        path = workdir / row["file"]
        frames.append(
            media.Frame(
                index=index,
                timestamp=float(row["start"]),
                path=path,
                phash=media.dhash(path),
                text=ocr.get("text", ""),
                lines=tuple(ocr.get("lines", ())),
                dedupe_warning=row.get("dedupe_warning"),
                ocr_warning=ocr.get("error"),
            )
        )

    groups = visual_states.group_progressive_builds(frames)
    selected = visual_states.derive_preview(frames)
    visual_root = workdir / "visual_states"
    temporary = Path(tempfile.mkdtemp(prefix="preview.next-", dir=visual_root))
    preview = visual_root / "preview"
    previous = visual_root / f"preview.previous-{os.getpid()}"
    timeline_path = workdir / "visual_timeline.jsonl"
    timeline_previous = workdir / f"visual_timeline.previous-{os.getpid()}.jsonl"
    manifest_path = workdir / "manifest.json"
    manifest_previous = workdir / f"manifest.previous-{os.getpid()}.json"
    timeline_next = workdir / "visual_timeline.jsonl.next"
    manifest_next = workdir / "manifest.json.next"
    manifest_moved = False
    timeline_moved = False
    preview_moved = False
    preview_installed = False
    complete = False
    try:
        visual_states.materialize_preview(selected, temporary)
        selected_names = {frame.path.name for frame in selected}
        by_index = {frame.index: frame for frame in frames}
        for index, row in enumerate(timeline):
            frame = by_index[index]
            row["in_preview"] = frame.path.name in selected_names
            row.pop("preview_file", None)
            for key in ("build_group_id", "build_position", "build_size"):
                row.pop(key, None)
            if row["in_preview"]:
                row["preview_file"] = f"visual_states/preview/{frame.path.name}"
            if frame.build_group_id:
                row.update(
                    build_group_id=frame.build_group_id,
                    build_position=frame.build_position,
                    build_size=frame.build_size,
                )

        manifest["counts"]["preview_visual_states"] = len(selected)
        manifest["counts"]["progressive_build_groups"] = len(groups)
        manifest["configuration"]["preview_algorithm"] = visual_states.PREVIEW_ALGORITHM
        write_jsonl(timeline_next, timeline)
        write_json(manifest_next, manifest)

        os.replace(manifest_path, manifest_previous)
        manifest_moved = True
        os.replace(timeline_path, timeline_previous)
        timeline_moved = True
        os.replace(preview, previous)
        preview_moved = True
        os.replace(temporary, preview)
        preview_installed = True
        os.replace(timeline_next, timeline_path)
        os.replace(manifest_next, manifest_path)
        complete = True
    except Exception:
        if not complete:
            if manifest_moved:
                manifest_path.unlink(missing_ok=True)
                os.replace(manifest_previous, manifest_path)
            if timeline_moved:
                timeline_path.unlink(missing_ok=True)
                os.replace(timeline_previous, timeline_path)
            if preview_installed and preview.exists():
                shutil.rmtree(preview)
            if preview_moved:
                os.replace(previous, preview)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        timeline_next.unlink(missing_ok=True)
        manifest_next.unlink(missing_ok=True)
        if complete:
            shutil.rmtree(previous, ignore_errors=True)
            manifest_previous.unlink(missing_ok=True)
            timeline_previous.unlink(missing_ok=True)
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdirs", nargs="+", type=Path)
    args = parser.parse_args()
    for workdir in args.workdirs:
        count = rebuild(workdir.resolve())
        print(f"{workdir}: {count} preview state(s)")


if __name__ == "__main__":
    main()
