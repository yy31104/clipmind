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

### Scroll capture

The original Kotlin timeline attached the screenshot from 1068.50 s to
the interval starting at 1066.50 s. The editor had already scrolled past
the code shown at 1066.78 s.

The capture fix keeps the pre-scroll image from 1067.00 s and assigns
the original 1068.50 s image to its own state starting at 1067.50 s.
A capture-only replay of the full published file retained the original
970 images and added the missing viewport. This replay does not replace
the one-time holdout scores.

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

## Second software holdout

- [µcad | Parametric Bricks](https://fair.tube/w/bEnaFxqjtWGYYDmRzSMyTj),
  Micha, CC BY 4.0. Screen-only CAD and code editing, 4:00.
- [How to use Inkscape](https://commons.wikimedia.org/wiki/File:How_to_use_Inkscape.webm),
  Ocdp, CC0 1.0. Screen-only vector editing, 5:21.

Both recordings ran in full once per backend on the
[experimental ranker](https://github.com/yy31104/clipmind/commit/52f4ec8c94f56cd463b359807a9af105c69b83f1).
The [corpus](data/software-screen-reserve-corpus.json) and
[labels](data/software-screen-reserve-labels.json) were frozen before the
candidate was locked. Labels are AI-assisted. Human review is pending.
The code score covers 29 literal on-screen regions from the µcad recording.

| Backend and recording | Recall | Precision | Code CER |
| --- | ---: | ---: | ---: |
| Mac, µcad | 36/61 (59.02%) | 36/63 (57.14%) | 187/3,150 (5.94%) |
| Mac, Inkscape | 46/62 (74.19%) | 46/89 (51.69%) | n/a |
| **Mac, both** | **82/123 (66.67%)** | **82/152 (53.95%)** | **5.94%** |
| Docker, µcad | 27/61 (44.26%) | 27/48 (56.25%) | 571/3,150 (18.13%) |
| Docker, Inkscape | 43/62 (69.35%) | 43/80 (53.75%) | n/a |
| **Docker, both** | **70/123 (56.91%)** | **70/128 (54.69%)** | **18.13%** |

Both backends passed 50% precision and missed 85% recall. Docker also
missed the 15% code error target. Each full timeline matched 119 of 123
changes. Most misses had a saved image within one second, but the ranker
left out the corresponding boundary.

Replaying the simpler ranker on the same saved images matched the same
82 Mac and 70 Docker changes. It added one unmatched Docker prediction.
The experimental ranker remains separate from the capture fix.

The full runs took 102 and 66 seconds on Mac and 324 and 173 seconds in
Docker, in source order above. All four completed OCR and visual extraction
and cleared their temporary media. Run receipts, metrics and matched
timestamps are in the [evidence data](data/software-screen-reserve-evidence.json).
