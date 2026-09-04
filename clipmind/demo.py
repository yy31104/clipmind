"""A generated sample video, so a first run needs no network and no account.

The first thing a new install should prove is that extraction works at all.
Reaching for a real link makes that depend on a live site, a signed-in browser
and a URL that has not expired - none of which say anything about whether
ClipMind is working. This builds a short silent slideshow locally instead.

`scripts/evaluate_silent_slides.py` uses the same builder, so the evaluation
fixture and the demo can never drift apart.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SLIDES: tuple[tuple[str, str], ...] = (
    ("ALPHA PLAN", "#28536b"),
    ("BETA SYSTEM", "#7b2d26"),
    ("GAMMA DATA", "#355834"),
    ("DELTA REVIEW", "#654f6f"),
)

# Tried in order; the demo must not depend on one platform's font layout.
_FONT_CANDIDATES = (
    "Arial.ttf",
    "DejaVuSans.ttf",
    "LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
)


def _font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 cannot size the bitmap default
        return ImageFont.load_default()


def build_sample_video(destination: Path) -> Path:
    """Render the four-slide silent fixture and return the encoded video."""
    from PIL import Image, ImageDraw

    destination.mkdir(parents=True, exist_ok=True)
    font = _font(68)
    slides = []
    for index, (label, color) in enumerate(SLIDES):
        image = Image.new("RGB", (1280, 720), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 170 + index * 90, 720), fill=color)
        draw.text((260, 280), label, fill="black", font=font)
        path = destination / f"slide-{index}.png"
        image.save(path)
        slides.append(path)

    concat = destination / "slides.txt"
    concat.write_text(
        "".join(f"file '{path}'\nduration 1.5\n" for path in slides)
        + f"file '{slides[-1]}'\n",
        encoding="utf-8",
    )
    video = destination / "clipmind-demo.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", str(video),
        ],
        check=True,
    )
    return video
