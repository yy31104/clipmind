#!/usr/bin/env python3
"""Measure pre-adjudication agreement between independent source annotations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import math

from evaluate_real_talks import edit_score
from screen_evaluation import boundary_inventory, reserve_record


def _times(rows: list[dict]) -> list[float]:
    times = sorted(float(row["timestamp"]) for row in rows)
    if any(not 0 < t < float("inf") for t in times) or len(times) != len(set(times)):
        raise ValueError("Boundary times must be positive, finite and unique")
    return times


def _by_crop(rows: list[dict]) -> dict[str, str]:
    crops = {}
    for row in rows:
        key = row["crop_id"]
        if key in crops or not isinstance(row["text"], str):
            raise ValueError("Every shared crop needs a unique ID and text string")
        crops[key] = row["text"]
    return crops


def _region_iou(first: list[float], second: list[float]) -> float:
    for box in (first, second):
        x, y, width, height = box
        if not all(math.isfinite(v) for v in box) or not (
            0 <= x < x + width <= 1 and 0 <= y < y + height <= 1
        ):
            raise ValueError("Code regions require positive boxes within the frame")
    x, y, width, height = first
    u, v, w, h = second
    overlap = max(0.0, min(x + width, u + w) - max(x, u)) * max(
        0.0, min(y + height, v + h) - max(y, v)
    )
    return overlap / (width * height + w * h - overlap)


def _independent_code(first: list[dict], second: list[dict]) -> dict:
    """Match regions by time and geometry before comparing either transcription."""
    neighbors = []
    for left in first:
        nearby = []
        for index, right in enumerate(second):
            distance = abs(float(left["timestamp"]) - float(right["timestamp"]))
            if distance <= 1:
                iou = _region_iou(left["roi"], right["roi"])
                if iou >= 0.5:
                    nearby.append((-iou, distance, index))
        neighbors.append([index for _, _, index in sorted(nearby)])
    assignments = {}

    def place(left: int, visited: set[int]) -> bool:
        for right in neighbors[left]:
            if right in visited:
                continue
            visited.add(right)
            if right not in assignments or place(assignments[right], visited):
                assignments[right] = left
                return True
        return False

    for left in range(len(first)):
        place(left, set())
    pairs = []
    for right, left in sorted(assignments.items(), key=lambda pair: pair[1]):
        a, b = first[left], second[right]
        pairs.append({"first_index": left, "second_index": right,
                      "region_iou": _region_iou(a["roi"], b["roi"]),
                      "first_as_reference": edit_score(a["text"], b["text"]),
                      "second_as_reference": edit_score(b["text"], a["text"])})
    return {"pairs": pairs, "first_unmatched": len(first) - len(pairs),
            "second_unmatched": len(second) - len(pairs)}


def agreement(first: dict, second: dict) -> dict:
    """Use the same boundary tolerance and text normalization as extraction scoring."""
    if set(first["cases"]) != set(second["cases"]):
        raise ValueError("Independent annotations must cover the same recordings")
    per_case = {}
    for case, left in sorted(first["cases"].items()):
        right = second["cases"][case]
        if left.get("complete") is not True or right.get("complete") is not True:
            raise ValueError(f"{case}: both source passes must be complete")
        if left["source_sha256"] != right["source_sha256"]:
            raise ValueError(f"{case}: source fingerprints differ")
        first_times = _times(left["slide_changes"])
        second_times = _times(right["slide_changes"])
        boundaries = boundary_inventory(
            first_times, [{"start": t} for t in second_times], start=0, end=float("inf")
        )["score"]
        n = len(first_times) + len(second_times)
        boundaries["f1"] = 2 * boundaries["matched"] / n if n else None
        first_crops, second_crops = _by_crop(left.get("shared_crops", [])), _by_crop(right.get("shared_crops", []))
        if first_crops.keys() != second_crops.keys():
            raise ValueError(f"{case}: shared crop IDs differ")
        crops = []
        for crop_id, text in sorted(first_crops.items()):
            other = second_crops[crop_id]
            if not text.strip() or not other.strip():
                crops.append({"crop_id": crop_id, "status": "unreadable",
                              "first_abstained": not text.strip(), "second_abstained": not other.strip()})
                continue
            crops.append({"crop_id": crop_id, "status": "paired",
                          "first_as_reference": edit_score(text, other, speech=False),
                          "second_as_reference": edit_score(other, text, speech=False)})
        per_case[case] = {
            "source_sha256": left["source_sha256"], "boundaries": boundaries,
            "shared_code": crops,
            "independent_regions": _independent_code(left.get("code_regions", []), right.get("code_regions", [])),
        }
    num_first = sum(c["boundaries"]["reference_count"] for c in per_case.values())
    num_second = sum(c["boundaries"]["prediction_count"] for c in per_case.values())
    matches = sum(c["boundaries"]["matched"] for c in per_case.values())
    code = {}
    for direction in ("first_as_reference", "second_as_reference"):
        scores = [crop[direction] for row in per_case.values() for crop in row["shared_code"]
                  if crop["status"] == "paired"]
        units = sum(row["reference_units"] for row in scores)
        errors = sum(row["errors"] for row in scores)
        code[direction] = {"paired_crops": len(scores), "errors": errors,
                           "reference_units": units, "cer": errors / units if units else None}
    return {
        "cases": per_case,
        "boundary_totals": {"first_count": num_first, "second_count": num_second, "matched": matches,
                            "f1": 2 * matches / (num_first + num_second) if num_first + num_second else None},
        "shared_code_totals": code,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("first", "second", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    first, second = args.first.read_bytes(), args.second.read_bytes()
    report = agreement(json.loads(first), json.loads(second))
    report["input_sha256"] = {"first": hashlib.sha256(first).hexdigest(),
                              "second": hashlib.sha256(second).hexdigest()}
    reserve_record(args.output, report)


if __name__ == "__main__":
    main()
