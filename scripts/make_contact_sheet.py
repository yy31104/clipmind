#!/usr/bin/env python3
"""Render a visual-state directory as a timestamped contact sheet for review."""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--width", type=int, default=240)
    args = parser.parse_args()

    files = sorted(args.directory.glob("*.jpg"))
    if not files:
        raise SystemExit(f"no JPEG frames in {args.directory}")
    label_height = 22
    with Image.open(files[0]) as first:
        thumb_height = round(args.width * first.height / first.width)
    cell_height = thumb_height + label_height
    rows = math.ceil(len(files) / args.columns)
    sheet = Image.new("RGB", (args.columns * args.width, rows * cell_height), "white")
    draw = ImageDraw.Draw(sheet)

    for position, path in enumerate(files):
        with Image.open(path) as image:
            thumb = ImageOps.fit(
                image.convert("RGB"), (args.width, thumb_height), method=Image.Resampling.LANCZOS
            )
        x = position % args.columns * args.width
        y = position // args.columns * cell_height
        sheet.paste(thumb, (x, y))
        draw.text((x + 5, y + thumb_height + 4), path.stem, fill="black")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, quality=88)


if __name__ == "__main__":
    main()
