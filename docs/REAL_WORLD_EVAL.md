# Real-world extraction evaluation

The v1 visual pipeline was checked against the three public Douyin videos that
originally exposed the product gaps. They cover a 227-second code/UI recording,
a 64-second talking-head explanation with burned captions and inserted documents,
and a 52-second talking-head/slide presentation.

Run the checked-in evaluation against completed packs with:

```bash
.venv/bin/python scripts/evaluate.py
```

The machine-readable reports are
[`eval-results-before-preview.json`](eval-results-before-preview.json) and
[`eval-results.json`](eval-results.json). The production preview changed as
follows while the canonical evidence sets remained unchanged:

| Case | Canonical states | Old preview | v1 preview | Preview OCR coverage |
| --- | ---: | ---: | ---: | ---: |
| code/UI, 227 s | 243 | 205 | 69 | 79.98% |
| talking head + documents, 64 s | 29 | 27 | 6 | 85.26% |
| talking head + slides, 52 s | 31 | 23 | 4 | 59.74% |

The preview has no fixed count or per-duration budget. Progressive builds are
collapsed first; remaining frames are clustered by content change, and each
cluster contributes a readable representative. OCR replacement that is not
explained by nearby speech captions also breaks a scene, preserving same-layout
slides and code panes whose words change. The relatively lower OCR union
for the short slide video is expected: preview keeps the completed four-item
slide, while `visual_states/all/` still retains every intermediate text state.

One video completed a fresh end-to-end run with the v1 preview. The other two
previews were rebuilt from their already-complete canonical evidence with
`scripts/rebuild_preview.py` after Douyin began rejecting fresh downloads during
the evaluation session. This replay does not rerun acquisition, ASR, or OCR; it
tests the derived preview against the exact preserved evidence.

## Sampling decision

The long code/UI source was sampled at both the production 2 fps and a 4 fps
probe rate. All 105 states that remained dHash-stable for at least 0.5 seconds
at 4 fps were represented at 2 fps. The machine-readable result is
[`sampling-coverage.json`](sampling-coverage.json).

This evidence supports keeping uniform 2 fps sampling for v1. It does not claim
that sub-0.5-second flashes are readable evidence, and it does not rule out
adaptive sampling for a future corpus that demonstrates a stable-state miss.

Across all three packs, 9 of 300 adjacent canonical pairs were within the
configured dHash duplicate threshold, an aggregate duplicate visual-state rate
of 3.0%. Every timeline reference resolved to a canonical file and all three
workdirs passed the temporary-file cleanup check.

## What this does and does not prove

The current suite directly exercises long code/UI changes, scrolling, dense
small text, talking-head caption redundancy, inserted documents, and progressive
slides. Unit fixtures additionally cover disappearing/replaced text, progressive
builds, hash failure, and more than ten independent visual scenes.

It is not a broad platform benchmark. Silent infographic videos and arbitrary
whiteboard capture remain explicit real-world corpus gaps; they are listed as
limitations rather than inferred from unrelated material. A separate generated
four-slide video does exercise the real no-audio, FFmpeg, Vision OCR, preview,
and Evidence Pack path reproducibly; see `silent-slides-eval.json`.
