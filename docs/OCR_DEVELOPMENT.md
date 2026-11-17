# OCR source-frame development diagnostics

These measurements use exact source frames, not production-selected representatives. They isolate OCR from retention; they are not the end-to-end benchmark or the held-out result. All ten frozen development references were tested: seven code ROIs, one prose ROI and two diagram-label ROIs. Annotations are AI-assisted; human review is pending.

Apple Vision ran locally. Tesseract 5.5.4 ran natively on the Mac with English language data. This is not a Linux/Docker quality result. Source images remained unchanged. Timings include background development activity and are not isolated performance measurements.

| Candidate | Vision code CER | Tesseract code CER | Outcome |
| --- | --- | --- | --- |
| attempt-01 | 22.60% (134/593) | 56.32% (334/593) | Rejected: absolute soft contrast erased mixed-panel syntax and left gray text too faint. |
| attempt-02 | 15.18% (90/593) | 33.73% (200/593) | Rejected: local-contrast halos damaged larger glyphs and panel-edge text. |
| attempt-03 | 7.59% (45/593) | 34.06% (202/593) | Rejected: sparse fragments were read in the wrong horizontal order. |
| attempt-04 | 5.90% (35/593) | 29.34% (174/593) | Rejected: same-line fragments still followed small vertical offsets. |
| attempt-05 | 5.23% (31/593) | 18.55% (110/593) | Selected image/layout behavior; later confirmed through the final worker. |
| attempt-06 | 5.23% (31/593) | 17.37% (103/593) | Rejected: minor code gain regressed diagram-label CER to 96.15%. |
| attempt-07-final | 5.23% (31/593) | 18.55% (110/593) | Final selected behavior through the JSON-stdio worker. |

Before: Vision **182/593, 30.69%**; native Mac Tesseract **244/593, 41.15%**. Final: Vision **31/593, 5.23%**; Tesseract **110/593, 18.55%**. The portable code result still misses 15%; no target was changed. Tesseract prose improves 60/213 to 1/213; diagram labels improve 52/52 to 0/52. Vision prose/diagram controls are unchanged.

Remaining errors include dim small syntax inside a mixed light/dark slide, assignment signs split into horizontal fragments, and punctuation substitutions. The sparse portable mode is a tradeoff: some clean large-code references regress while foreground text over a faint terminal background improves. Raw per-ROI text, boxes and errors are included for every candidate.

## Native Vision failures

A fresh original-Vision loop without MLX timed out after 54 retained Xous images. The last image passed alone; subsequent full ON/OFF language-correction controls also passed, so neither an image-specific nor a correction-setting cause was established. The existing correction setting is unchanged.

A recycled multiprocessing prototype recognized 664/666 requests across three 222-frame cycles, with two 10-second deadline errors. It was replaced because library multiprocessing bootstrap breaks unguarded stdin/notebook SDK calls. Its results are retained.

The final fresh-interpreter JSON-stdio bridge recognized **665/666** requests across three cycles; one native request timed out in 10.027 seconds, the owned worker was reaped, and following frames continued. This bounds native failures; it does not establish failure-free recognition. Each worker handles at most 32 requests, the request deadline is 10 seconds, and idle workers exit after 30 seconds. A failed image is retained and reported rather than silently retried.

The final worker also passed an unguarded SDK call from stdin with the parent working directory pointing at a different checkout. Parent and child loaded the same package. Actual-child tests cover timeout/restart, recycling, idle exit, closed pipes, reader-thread exit, preserved error diagnostics, and parent-owned derivative cleanup. The complete worktree suite passed 440 tests with one skip.

## Files

- `attempt-*/results.json`: all source-frame hypotheses, spatial boxes, scores and fingerprints.
- `vision-native-loop-attempt*/`: raw original-loop progress, failures and ON/OFF controls. The first attempt failed due to a harness relative-path error and is labeled separately.
- `vision-worker-three-cycles.json`: earlier multiprocessing prototype, including both failures.
- `vision-worker-stdio-three-cycles.json`: final stdio bridge, including the remaining native failure.
- `native-failure-images/`: the exact retained Xous JPEGs referenced by failures.

All source frames and derived images are CC BY 4.0, credited to the talk authors and CCC in the attached development source manifest. No held-out image or prediction was used to select this candidate.

[Download the complete records and images](https://github.com/yy31104/clipmind/releases/download/v1.4.0/clipmind-v1.4.0-ocr-development.zip). [Release checksums](https://github.com/yy31104/clipmind/releases/download/v1.4.0/SCREEN_SHA256SUMS).
