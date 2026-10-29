"""Deterministic labeled selection regression set, with no account or media download.

This measures the stated fixture labels, not semantic accuracy on all videos.
Run: python scripts/evaluate_visual_quality.py --output /path/to/report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipmind.media import Frame
from clipmind.visual_content import CONTENT_ALGORITHM, select_content_frames


@dataclass
class Case:
    name: str
    frames: list[Frame]
    spoken: tuple
    # Each group describes one independently required visual fact. Exact
    # duplicates can have several acceptable representatives in the group.
    required_groups: tuple[frozenset[int], ...]
    irrelevant: frozenset[int] = frozenset()


def cases(root: Path) -> list[Case]:
    root.mkdir(parents=True, exist_ok=True)
    # Use Pillow's bundled font, not a platform-dependent system lookup. The
    # fixture pixels must not change just because Linux has DejaVu installed
    # while Windows/macOS choose a fallback. All fixture text here is ASCII.
    font = ImageFont.load_default(size=15)
    result = []
    next_index = 0

    def frame(image, texts=(), boxes=(), confidence=.91, warning=None):
        nonlocal next_index
        index = next_index
        next_index += 1
        path = root / f"frame-{index:03}.png"
        image.save(path)
        return Frame(index, float(index), path, text="\n".join(texts), lines=tuple(texts),
                     ocr_warning=warning, ocr_layout=tuple(
                         {"text": text, "confidence": confidence, "bbox": box}
                         for text, box in zip(texts, boxes)))

    def document(*, confidence=.91, offset=0., changed=None, no_ocr=False, warning=None):
        texts = ["Research procedure and deployment checklist",
                 "Validate assumptions using held out observations",
                 "Maximum portfolio drawdown is 12 percent",
                 "Keep the audit trail for each decision",
                 "Risk must not exceed the approved limit",
                 "Inspect original measurements before publishing"]
        if changed:
            texts[changed[0]] = changed[1]
        image = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(image)
        boxes = []
        for i, text in enumerate(texts):
            x, y = .07 + offset, .12 + i * .105
            draw.text((int(x * 640), int(y * 400)), text, fill="black", font=font)
            boxes.append((x, y, .85 - offset, .045))
        return frame(image, () if no_ocr else texts, () if no_ocr else boxes,
                     confidence=confidence, warning=warning)

    def person(caption, shift):
        image = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((250 + shift, 60, 390 + shift, 210), fill=(168, 130, 100))
        draw.polygon([(230, 230), (400, 230), (480, 400), (150, 400)], fill=(35, 45, 50))
        draw.text((130, 340), caption, fill="black", font=font)
        return frame(image, [caption], [(.2, .85, .6, .055)])

    speech = [person(f"This is sentence number {i}", i * 2) for i in range(5)]
    result.append(Case("talking-head-subtitle-and-pose-changes", speech,
                       tuple((f.timestamp, f.timestamp + .8, f.text) for f in speech),
                       (), frozenset(f.index for f in speech)))
    blank = [frame(Image.new("RGB", (640, 400), colour)) for colour in ("white", "black")]
    result.append(Case("blank-frames", blank, (), (), frozenset(f.index for f in blank)))
    image = Image.new("RGB", (640, 400), "white")
    draw = ImageDraw.Draw(image)
    title_texts = ["Learning series: first look at research", "Episode 02 - overview",
                  "Introduction", "AI", "Episode 02", "Watch our next episode"]
    title_boxes = [(.05, .08, .82, .04), (.05, .13, .60, .04),
                   (.25, .32, .55, .04), (.29, .42, .42, .08),
                   (.30, .57, .48, .04), (.20, .85, .65, .04)]
    for text, box in zip(title_texts, title_boxes):
        draw.text((int(box[0] * 640), int(box[1] * 400)), text, fill="black", font=font)
    title = frame(image, title_texts, title_boxes)
    result.append(Case("well-recognized-title-card-is-not-a-document", [title],
                       ((title.timestamp, title.timestamp + 1, title_texts[-1]),),
                       (), frozenset([title.index])))

    for name, options in (("silent-low-confidence-document", {"confidence": .4}),
                          ("ocr-failed-document", {"no_ocr": True, "warning": "OCR unavailable"}),
                          ("empty-ocr-document", {"no_ocr": True})):
        item = document(**options)
        result.append(Case(name, [item], (), (frozenset([item.index]),)))

    narrated = document()
    result.append(Case("document-is-not-disposable-when-read-aloud", [narrated],
                       ((narrated.timestamp, narrated.timestamp + 1, narrated.text),),
                       (frozenset([narrated.index]),)))

    first, repeat = document(), document(offset=.025)
    result.append(Case("same-document-camera-shift", [first, repeat], (),
                       (frozenset([first.index, repeat.index]),)))
    for name, changed, confidence in (
        ("changed-number-below-95-percent-ocr", (2, "Maximum portfolio drawdown is 13 percent"), .91),
        ("changed-negation-below-95-percent-ocr", (4, "Risk must exceed the approved limit"), .91),
        ("changed-short-negation", (4, "Risk is approved"), .91),
        ("changed-short-operator", (4, "risk >= threshold"), .91),
        ("changed-number-at-65-percent-ocr", (2, "Maximum portfolio drawdown is 13 percent"), .65),
    ):
        a, b = document(confidence=confidence), document(confidence=confidence, offset=.025, changed=changed)
        if name == "changed-short-negation":
            # Preserve a short warning even when longer body rows dominate coverage.
            a = document(changed=(4, "Risk is not approved"), confidence=confidence)
        if name == "changed-short-operator":
            a = document(changed=(4, "risk <= threshold"), confidence=confidence)
        result.append(Case(name, [a, b], (), tuple(frozenset([f.index]) for f in (a, b))))

    plots = []
    for shift in (0, 1):
        image = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.line([(55, 30), (55, 340), (600, 340)], fill="black", width=3)
        draw.line([(60, 310), (210, 215), (400, 250), (580, 60)], fill="blue", width=3)
        draw.rectangle((400 + shift, 248, 404 + shift, 252), fill="red")
        plots.append(frame(image))
    result.append(Case("single-pixel-plot-marker-change", plots, (),
                       tuple(frozenset([f.index]) for f in plots)))

    diagrams = []
    for arrow in (1, -1):
        image = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 130, 220, 240), outline="black", width=3)
        draw.rectangle((420, 130, 590, 240), outline="black", width=3)
        draw.line((220, 185, 420, 185), fill="black", width=3)
        point = 415 if arrow == 1 else 225
        draw.line((point - 12 * arrow, 175, point, 185, point - 12 * arrow, 195), fill="black", width=3)
        diagrams.append(frame(image))
    result.append(Case("axis-free-diagram-with-reversed-arrow", diagrams, (),
                       tuple(frozenset([f.index]) for f in diagrams)))

    code = []
    for text in ("threshold = 0.01", "threshold = 0.02"):
        image = Image.new("RGB", (640, 400), "white")
        ImageDraw.Draw(image).text((40, 120), text, fill="black", font=font)
        code.append(frame(image, [text], [(.0625, .30, .65, .05)]))
    result.append(Case("changed-code-constant-read-aloud", code,
                       tuple((f.timestamp, f.timestamp + .8, f.text) for f in code),
                       tuple(frozenset([f.index]) for f in code)))

    base = document()
    captioned = []
    for caption in ("First spoken caption", "Second spoken caption"):
        with Image.open(base.path) as source:
            image = source.copy()
        ImageDraw.Draw(image).text((170, 350), caption, fill="black", font=font)
        item = frame(image, [*base.lines, caption],
                     [*[block["bbox"] for block in base.ocr_layout], (.265625, .875, .60, .055)])
        captioned.append(item)
    result.append(Case("document-with-different-spoken-subtitles", captioned,
                       tuple((f.timestamp, f.timestamp + .8, f.lines[-1]) for f in captioned),
                       (frozenset(f.index for f in captioned),)))
    return result


def evaluate(items: list[Case], selector=select_content_frames) -> dict:
    rows = []
    for case in items:
        before = {f.index: hashlib.sha256(f.path.read_bytes()).hexdigest() for f in case.frames}
        decisions = {}
        chosen = {f.index for f in selector(case.frames, case.spoken, decisions=decisions)}
        recalled = sum(bool(chosen & group) for group in case.required_groups)
        redundant = sum(max(0, len(chosen & group) - 1) for group in case.required_groups)
        unexpected = len(chosen & case.irrelevant)
        rows.append({"case": case.name, "frames": len(case.frames), "selected": sorted(chosen),
                     "required_groups": len(case.required_groups), "recalled_groups": recalled,
                     "redundant_selected": redundant, "irrelevant_selected": unexpected,
                     "source_images_unchanged": before == {
                         f.index: hashlib.sha256(f.path.read_bytes()).hexdigest() for f in case.frames},
                     "decisions": decisions})
    required = sum(row["required_groups"] for row in rows)
    recalled = sum(row["recalled_groups"] for row in rows)
    return {"algorithm": CONTENT_ALGORITHM, "scope": "labeled synthetic adversarial fixtures; not a real-world semantic guarantee",
            "cases": rows, "required_groups": required, "recalled_groups": recalled,
            "required_recall": recalled / required if required else 1.,
            "redundant_selected": sum(row["redundant_selected"] for row in rows),
            "irrelevant_selected": sum(row["irrelevant_selected"] for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="clipmind-visual-quality-") as temp:
        report = evaluate(cases(Path(temp)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}))
    return int(report["required_recall"] != 1 or report["redundant_selected"] != 0
               or report["irrelevant_selected"] != 0
               or not all(row["source_images_unchanged"] for row in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
