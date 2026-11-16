#!/usr/bin/env python3
"""Build an offline, source-only annotation review package outside the checkout.

No extraction models run. Each change gets an immediate source pair and optional
wider context; each OCR reference also gets the exact frame and native-size ROI.
The CSV is blank for the project owner to complete.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.screen_evaluation import sha256, case_source_url

FIELDS = ("annotation_id", "case_id", "kind", "clip_seconds", "source_seconds",
          "description", "reference_text", "decision", "corrected_seconds", "corrected_text", "notes")
REVIEW_STATUS = "AI-assisted annotations; human review pending"


def change_description(label: dict, position: int, timestamp: float) -> str:
    for key in ("boundary_descriptions", "boundary_notes", "change_notes"):
        value = label.get(key)
        if isinstance(value, dict):
            result = value.get(str(timestamp), value.get(f"{timestamp:g}", ""))
        elif isinstance(value, list) and position < len(value):
            result = value[position]
        else:
            continue
        if isinstance(result, str) and result.strip():
            return result
    return "Source-visible information change."


def annotation_rows(corpus: dict, labels: dict) -> list[dict]:
    rows = []
    for case in corpus["cases"]:
        identity = case["case_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
            raise ValueError("unsafe case_id")
        label = labels["cases"][identity]
        duration = case["sample_end_seconds"] - case["sample_start_seconds"]
        if not math.isfinite(duration) or duration <= 0 or label["review_end"] != duration:
            raise ValueError(f"{identity}: labels must cover the complete input")
        entries = [("change", value, "", None, change_description(label, n, value))
                   for n, value in enumerate(label["slide_changes"])]
        entries += [(ref.get("kind", "ocr"), ref["timestamp"], ref["text"], ref["roi"], ref.get("note", ""))
                    for ref in label["ocr"]]
        for index, (kind, timestamp, text, roi, description) in enumerate(entries, 1):
            if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or not 0 <= timestamp < duration:
                raise ValueError(f"{identity}: invalid annotation timestamp")
            if roi is not None and (len(roi) != 4 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in roi)
                                    or min(roi) < 0 or min(roi[2:]) <= 0
                                    or roi[0] + roi[2] > 1.000001 or roi[1] + roi[3] > 1.000001):
                raise ValueError(f"{identity}: invalid annotation ROI")
            rows.append({"annotation_id": f"{identity}-{index:04d}", "case_id": identity,
                         "kind": kind, "clip_seconds": timestamp,
                         "source_seconds": case["sample_start_seconds"] + timestamp,
                         "description": description, "reference_text": text, "roi": roi, "duration": duration,
                         "decision": "", "corrected_seconds": "", "corrected_text": "", "notes": "",
                         "title": case.get("title", identity), "source_url": case_source_url(case),
                         "license": case.get("license", corpus.get("license", "See corpus source attribution"))})
    return rows


def extract_frame(source: Path, timestamp: float, output: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{timestamp:.6f}",
                    "-i", str(source), "-frames:v", "1", "-q:v", "2", "-threads", "1", str(output)],
                   check=True, capture_output=True, timeout=60)


def render_html(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        escape = lambda value: html.escape(str(value), quote=True)
        def figures(context=False):
            return "".join(f'<figure><img loading="lazy" src="{escape(path)}"><figcaption>{escape(name)}</figcaption></figure>'
                           for name, path in row["images"].items() if name.startswith("context_") == context)
        context = ('<details><summary>Show wider context (two seconds before/after)</summary>'
                   f'<div class="images">{figures(True)}</div></details>'
                   if any(name.startswith("context_") for name in row["images"]) else "")
        cards.append(f'<section id="{escape(row["annotation_id"])}"><h2>{escape(row["annotation_id"])} · {escape(row["kind"])}</h2>'
                     f'<p>{escape(row["title"])} · clip {row["clip_seconds"]:g}s · source {row["source_seconds"]:g}s</p>'
                     f'<p>Source: {escape(row["source_url"])} · {escape(row["license"])}</p>'
                     f'<p>{escape(row.get("description", ""))}</p>'
                     f'<div class="images">{figures()}</div>{context}<pre>{escape(row["reference_text"])}</pre>'
                     '<label>Decision <select><option value=""></option><option>accept</option><option>edit</option>'
                     '<option>reject</option><option>unsure</option></select></label> '
                     '<label>Corrected clip seconds <input type="number" step="0.01"></label>'
                     '<label>Corrected reference text<textarea></textarea></label><label>Notes<textarea></textarea></label></section>')
    # JSON data is protected against closing the script element, including text from source slides.
    serialized = json.dumps([{key: row.get(key, "") for key in FIELDS} for row in rows], ensure_ascii=False).replace("<", "\\u003c")
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Screen annotation review</title>'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><style>'
            'body{font:16px system-ui;margin:2rem auto;max-width:1400px;padding:0 1rem;color:#20242a}'
            'header{position:sticky;top:0;background:white;padding:1rem 0;border-bottom:1px solid #ccc}'
            'section{padding:1rem 0 2rem;border-bottom:1px solid #ccc}.images{display:flex;gap:1rem;flex-wrap:wrap}'
            'figure{flex:1 1 450px;margin:0}img{width:100%;height:auto}pre{white-space:pre-wrap}'
            'textarea{display:block;width:98%;min-height:4rem}label{display:block;margin:.6rem 0}button{padding:.6rem}</style>'
            f'<header><strong>{REVIEW_STATUS}</strong><p>{len(rows)} annotations. Compare the source pair, then accept, edit, reject or mark unsure. '
            'The first pair is half a second before/after; expand wider context when a change is ambiguous. '
            'Times are relative to the prepared input. Use Download CSV to save your decisions; reloading clears unsaved edits.</p>'
            '<button id="download">Download review CSV</button></header>' + "".join(cards) +
            f'<script type="application/json" id="rows">{serialized}</script><script>'
            'const rows=JSON.parse(document.querySelector("#rows").textContent);'
            'document.querySelector("#download").onclick=()=>{document.querySelectorAll("section").forEach((s,i)=>{'
            'const inputs=s.querySelectorAll("select,input,textarea");["decision","corrected_seconds","corrected_text","notes"].forEach((k,j)=>rows[i][k]=inputs[j].value);});'
            'const keys=Object.keys(rows[0]||{}),quote=String.fromCharCode(34),q=v=>quote+String(v??"").replaceAll(quote,quote+quote)+quote;'
            'const text=[keys.map(q).join(","),...rows.map(r=>keys.map(k=>q(r[k])).join(","))].join("\\r\\n");'
            'const url=URL.createObjectURL(new Blob(["\\ufeff",text],{type:"text/csv;charset=utf-8"}));'
            'const a=document.createElement("a");a.href=url;a.download="review-completed.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};'
            '</script></html>')


def build_package(corpus_file: Path, labels_file: Path, workdir: Path, output: Path) -> dict:
    if output.resolve().is_relative_to(REPO) or workdir.resolve().is_relative_to(REPO):
        raise ValueError("review media and inputs must be outside the checkout")
    corpus = json.loads(corpus_file.read_text(encoding="utf-8"))
    labels = json.loads(labels_file.read_text(encoding="utf-8"))
    rows = annotation_rows(corpus, labels)
    inputs = {}
    for case in corpus["cases"]:
        identity = case["case_id"]
        source = workdir / identity / "input.mp4"
        if not source.resolve().is_relative_to(workdir.resolve()):
            raise ValueError("source must be inside --workdir")
        digest = sha256(source)
        if digest != labels["cases"][identity]["input_sha256"]:
            raise ValueError(f"{identity}: source does not match frozen annotation bytes")
        inputs[identity] = digest
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "review_package_version": 2, "annotation_status": REVIEW_STATUS,
                "generator_sha256": sha256(Path(__file__)),
                "corpus_sha256": sha256(corpus_file), "labels_sha256": sha256(labels_file),
                "input_sha256": inputs, "annotation_count": len(rows),
                "estimated_review_minutes": round(sum(1 if row["roi"] else .5 for row in rows)),
                "status": "building", "annotations": rows}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        from PIL import Image
        images = output / "images"
        images.mkdir()
        for row in rows:
            source = workdir / row["case_id"] / "input.mp4"
            timestamp = row["clip_seconds"]
            times = {"before": max(0, timestamp - .5), "after": min(row["duration"] - .001, timestamp + .5),
                     "context_before": max(0, timestamp - 2),
                     "context_after": min(row["duration"] - .001, timestamp + 2)}
            if row["roi"]:
                times["reference"] = timestamp
            row["images"] = {}
            for name, seconds in times.items():
                relative = f"images/{row['annotation_id']}-{name}.jpg"
                extract_frame(source, seconds, output / relative)
                row["images"][name] = relative
            if row["roi"]:
                with Image.open(output / row["images"]["reference"]) as image:
                    x, y, width, height = row["roi"]
                    box = (round(x * image.width), round(y * image.height),
                           round((x + width) * image.width), round((y + height) * image.height))
                    relative = f"images/{row['annotation_id']}-roi.png"
                    image.crop(box).save(output / relative)
                    row["images"]["roi"] = relative
        with (output / "review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        (output / "index.html").write_text(render_html(rows), encoding="utf-8")
        manifest["status"] = "ready"
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: review package generation failed")
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "labels", "workdir", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_package(args.corpus, args.labels, args.workdir, args.output)
    print(f"{result['annotation_count']} source annotations; estimated {result['estimated_review_minutes']} minutes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
