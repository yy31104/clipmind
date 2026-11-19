# Software screen changes

ClipMind keeps small, settled edits in the full timeline. The information
ranker uses persistent image changes and nearby OCR to distinguish edits and
results from repeating toolbars, temporary menus, and unfinished input.
Canonical screenshots remain available even when they are not marked as
information boundaries.

The software subset of the published v1.6.0 benchmark contains an Inkscape
path-editing demo and a C/Emacs demo. This table excludes both slide talks.
A prediction can match one reference within one second.

| Backend | v1.6.0 recall | v1.6.0 precision | v1.6.0 code CER |
| --- | ---: | ---: | ---: |
| macOS Vision | 51/67 (76.12%) | 51/133 (38.35%) | 46/700 (6.57%) |
| Docker Tesseract | 50/67 (74.63%) | 50/126 (39.68%) | 83/700 (11.86%) |

The same two demos improved on the candidate:

| Backend | Recall | Precision | Code CER |
| --- | ---: | ---: | ---: |
| macOS Vision | 60/67 (89.55%) | 60/116 (51.72%) | 46/700 (6.57%) |
| Docker Tesseract | 58/67 (86.57%) | 58/115 (50.43%) | 84/700 (12.00%) |

Inkscape precision by itself was 46.51% on Mac and 48.57% in Docker.
The two Mac slide-control recordings kept their v1.6.0 match and prediction
counts. An earlier trial had one partial OCR result on AppImage, recorded in
the evidence data. The candidate control run completed all OCR.

The [v1.6.0 report](SCREEN_V160_ROUND3.md) retains the original pooled and
per-recording results.

## New software holdout

The two full recordings use different tools, languages and authors. Software
metrics are limited to the timestamp intervals fixed in the labels. A separate
whole-recording score includes presentation context. Labels are AI-assisted
and have had two source-only passes and reconciliation. Human review is pending.

- [GIMP fit the image to the canvas](https://commons.wikimedia.org/wiki/File:GIMP_fit_the_image_to_the_canvas.webm),
  Info-farmer, CC0 1.0. Tamil, 1920 × 1080.
- [Live Coding for Artists and Designers](https://media.ccc.de/v/lgm-2026-110673-live-coding-for-artists-and-designers),
  Abe Pazos Solatie, recorded by c3voc and Chaos West TV, CC BY 4.0.
  English, publisher's 576p MP4.

### Results

This holdout missed all three targets on both backends.

| Backend and recording | Recall | Precision | Code CER |
| --- | ---: | ---: | ---: |
| Mac, GIMP | 15/19 (78.95%) | 15/32 (46.88%) | n/a |
| Mac, live coding | 67/95 (70.53%) | 67/254 (26.38%) | 150/467 (32.12%) |
| **Mac, both** | **82/114 (71.93%)** | **82/286 (28.67%)** | **32.12%** |
| Docker, GIMP | 15/19 (78.95%) | 15/27 (55.56%) | n/a |
| Docker, live coding | 65/95 (68.42%) | 65/240 (27.08%) | 152/467 (32.55%) |
| **Docker, both** | **80/114 (70.18%)** | **80/267 (29.96%)** | **32.55%** |

The complete Kotlin timelines matched 90 of 95 changes on each backend.
Information ranking filtered out 23 of those matches on Mac and 25 in
Docker. The saved screenshots also include many autocomplete, typing, and
live preview states. Code OCR uses five fixed regions. Some contain visible
editor inlay hints absent from the source-code reference, and two crops
were affected by editor scroll between the reference and saved screenshot.
The frozen denominators include all five regions.

The complete runs took 70 seconds and 7 minutes 30 seconds on Mac, and
4 minutes and 27 minutes 54 seconds in Docker, in source order above.
All four runs completed extraction and cleared their temporary media.

### Reproduce

Download the original files listed in [the corpus manifest](data/software-screen-corpus.json).
Verify SHA-256 and place each file at `WORKDIR/<case_id>/input.mp4`, keeping
its bytes unchanged. The GIMP file remains WebM despite the evaluator's
fixed filename.

Use `scripts/evaluate_real_talks.py run` with the corpus and [label files](data/software-screen-labels.json).
The holdout ran once per backend. The primary score restricts both reference
and predicted changes to the recorded software interval. It uses the same
one-to-one, one-second matcher as the full-recording score. The Git
revision, run tokens, runtime hashes and input digests are in
[the evidence data](data/software-screen-evidence.json).

The native path uses Apple Vision and local MLX Whisper. The Linux path
uses Tesseract and faster-whisper in an offline ARM64 CPU container with
the same source revision mounted read-only. The Linux measurements refer to
that benchmark environment.
