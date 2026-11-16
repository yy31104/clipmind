#!/usr/bin/env python3
"""Inventory every baseline boundary error with source-frame evidence.

The original report is read-only. Error causes are filled after inspecting the
source pairs, not inferred from the predictions or a speaker's subject matter.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.review_screen_labels import extract_frame
from scripts.screen_evaluation import PROTOCOL, boundary_inventory, resolve_pack_path, sha256


def create_inventory(report: dict, labels: dict, workdir: Path) -> list[dict]:
    cases = []
    for case in report["cases"]:
        identity = case["case_id"]
        label = labels["cases"][identity]
        if case.get("input_sha256") != label["input_sha256"]:
            raise ValueError(f"{identity}: baseline source does not match annotation bytes")
        if label.get("slide_metric_applicable") is False:
            continue
        if not case.get("pack_path"):
            cases.append({"case_id": identity, "status": "failed", "errors": [],
                          "reason": "baseline produced no pack; extraction failure remains in its report"})
            continue
        pack = resolve_pack_path(case["pack_path"], workdir)
        states = [json.loads(line) for line in (pack / "visual_timeline.jsonl").read_text().splitlines() if line.strip()]
        inventory = boundary_inventory(label["slide_changes"], states,
                                       start=label["review_start"], end=label["review_end"])
        if inventory["score"] != case["canonical_boundary_score"]:
            raise ValueError(f"{identity}: current pack boundaries differ from immutable baseline report")
        for row in inventory["errors"]:
            row["event_id"] = identity + "-" + row["event_id"]
        cases.append({"case_id": identity, "status": "inventoried", "input_sha256": label["input_sha256"],
                      **inventory})
    return cases


def build_package(report_file: Path, labels_file: Path, workdir: Path, output: Path) -> dict:
    if output.resolve().is_relative_to(REPO):
        raise ValueError("source evidence images must be outside the checkout")
    report = json.loads(report_file.read_text())
    labels = json.loads(labels_file.read_text())
    if report.get("labels_sha256") != sha256(labels_file):
        raise ValueError("baseline report requires its original frozen annotation file")
    cases = create_inventory(report, labels, workdir)
    for case in cases:
        source = workdir / case["case_id"] / "input.mp4"
        if case.get("input_sha256") and sha256(source) != case["input_sha256"]:
            raise ValueError(f"{case['case_id']}: source bytes differ from annotations")
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": 1, "metric_protocol": PROTOCOL,
              "annotation_status": "AI-assisted annotations; human review pending",
              "baseline_report_sha256": sha256(report_file), "labels_sha256": sha256(labels_file),
              "status": "building", "cases": cases}
    path = output / "inventory.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    try:
        from PIL import Image, ImageDraw
        for case in cases:
            identity = case["case_id"]
            directory = output / identity
            directory.mkdir()
            duration = labels["cases"][identity]["review_end"]
            for row in case["errors"]:
                paths = []
                for name, seconds in (("before", max(0, row["timestamp"] - 1)),
                                      ("after", min(duration - .001, row["timestamp"] + 1))):
                    relative = f"{identity}/{row['event_id']}-{name}.jpg"
                    extract_frame(workdir / identity / "input.mp4", seconds, output / relative)
                    paths.append(relative)
                row["source_frames"] = paths
            # Four pairs per sheet; full-size individual frames remain available.
            for offset in range(0, len(case["errors"]), 4):
                rows = case["errors"][offset:offset + 4]
                sheet = Image.new("RGB", (1280, len(rows) * 390), "white")
                draw = ImageDraw.Draw(sheet)
                for n, row in enumerate(rows):
                    draw.text((8, n * 390 + 3), f"{row['event_id']}  {row['timestamp']:g}s", fill="black")
                    for side, filename in enumerate(row["source_frames"]):
                        with Image.open(output / filename) as image:
                            image.thumbnail((636, 358))
                            sheet.paste(image, (side * 640, n * 390 + 27))
                sheet.save(directory / f"pairs-{offset // 4 + 1:02d}.jpg", quality=92)
        result["status"] = "ready"
    except BaseException as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: source evidence generation failed")
        raise
    finally:
        path.write_text(json.dumps(result, indent=2) + "\n")
    with (output / "attribution.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("case_id", "event_id", "event_kind", "timestamp", "cause", "explanation", "status"))
        writer.writeheader()
        for case in cases:
            for row in case["errors"]:
                writer.writerow({"case_id": case["case_id"], **{key: row[key] for key in writer.fieldnames if key != "case_id"}})
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("report", "labels", "workdir", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_package(args.report, args.labels, args.workdir, args.output)
    print(f"{sum(len(case['errors']) for case in result['cases'])} events with source evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
