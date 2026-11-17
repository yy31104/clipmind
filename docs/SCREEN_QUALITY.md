# Screen quality: v1.4.0

The original ten 180-second 39C3 excerpts are development data: 30 minutes, 60 annotated changes across seven applicable excerpts, and seven code ROIs containing 593 normalized characters. Annotations are AI-assisted; human review is pending. Publisher-caption agreement measures agreement with generated captions, rather than a human transcript.

## Development results

| Path | Version | Changes captured | Information precision | Canonical control precision | Code CER | Caption WER | Sum case time |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Mac, MLX + Vision | v1.3.0 | 44/60 (73.33%) | 44/159 (27.67%) | 27.67% | 233/593 (39.29%) | 193/4572 (4.22%) | 118.897 s |
| Mac, MLX + Vision | v1.4.0 development | 59/60 (98.33%) | 59/227 (25.99%) | 59/371 (15.90%) | 37/593 (6.24%) | 193/4572 (4.22%) | 126.500 s |
| Docker, CPU + Tesseract | v1.3.0 | 44/60 (73.33%) | 44/160 (27.50%) | 27.50% | 322/593 (54.30%) | 186/4572 (4.07%) | 317.933 s |
| Docker, CPU + Tesseract | v1.4.0 development | 59/60 (98.33%) | 59/224 (26.34%) | 59/368 (16.03%) | 140/593 (23.61%) | 186/4572 (4.07%) | 409.299 s |

Code retention and recognition improve, but precision regresses slightly and portable code OCR still misses 15%. Typed speaker/layout frames remain saved. Uncertain changes and recognition failures count as information boundaries. The canonical control counts every retained state start. Matching stays one-to-one within one second; every incremental build remains a reference.

| Code-heavy case | Mac before → after | Docker before → after |
| --- | ---: | ---: |
| Variable Fonts | 0.00% → 6.90% | 0.00% → 3.45% |
| Ghidra | 6.58% → 5.26% | 9.21% → 10.53% |
| Xous | 47.50% → 1.88% | 46.88% → 1.25% |
| Proofs (Dafny) | 5.56% → 5.56% | 80.16% → 53.97% |
| Fuzzer | 71.78% → 10.40% | 68.81% → 30.20% |

All ten cases finished. Mac recognized 629 frames and Docker 626, with zero OCR exceptions or temporary-media leftovers in these runs. The remaining Docker errors concentrate in small Dafny method contracts and the second Fuzzer snippet. The Mac's sole missed boundary is a timing error: the image contains the new circles at source PTS 8.0, but its retained interval begins at 6.0. That still fails the fixed one-second boundary test.

The [baseline attribution](SCREEN_ERROR_ANALYSIS.md) covers every original miss and extra boundary. The [final attribution](data/v140-final-development-attribution.json) covers all 169 Mac typed errors: 65 speaker, 37 layout, 34 animation, 14 transition, six progressive-build, two timing and 11 unresolved. Individual hypotheses, character counts, case timings and provenance are in the [summary](data/v140-development-summary.json), [case CSV](data/v140-development-cases.csv), [ROI CSV](data/v140-development-rois.csv), and raw [Mac](data/v140-final-development-mac-results.json) / [Docker](data/v140-final-development-docker-results.json) reports.

## What each change did

- **Retention:** progressive-stroke sampling raised recall from 44/60 to 59/60, but created 1,087–1,096 canonical predictions. Code CER improved to 28.50% on Mac and 43.68% on Docker. This intermediate precision regression remains in the raw reports.
- **Settling:** choosing a later near-duplicate image removed a ghosted previous page in the prose ROI: Mac CER fell from 25.82% to 0.47%. Aggregate portable code CER did not improve. Two native frame errors made the intermediate Mac run incomplete; their underlying causes were not recorded.
- **Motion:** on identical corrected-clock source samples, stationary strokes plus recurring-pane evidence reduced 1,121 raw predictions to 228 typed predictions, with 59/60 matches. [The ablation](SCREEN_MOTION_DEVELOPMENT.md) retains every variant, setup failure, and the simultaneous-presenter-motion/small-code residual risk.
- **OCR:** exact-source-frame diagnostics isolate contrast and reading order. Vision code CER changed from 30.69% to 5.23%; native Mac Tesseract from 41.15% to 18.55%. These are diagnostic source frames, separate from the end-to-end table. [Every attempted variant and native failure](OCR_DEVELOPMENT.md) remains available.

Early native runs hung and were interrupted. The bounded worker still recorded one timeout in 666 repeated requests during development. Failures remain visible and contribute missing text; they are not retried away. The final real-video runs above recorded none.

Tesseract's previous Chinese-first multilingual default regressed an unchanged English fixture. English-first order restores all four labels on Docker Tesseract 5.3.0 and CI 5.3.4. Two generated mixed Chinese controls retain their prior scores; this does not establish broad Chinese OCR accuracy. Explicit language overrides stay unchanged. Old failures are retained.

## Frozen holdout

Three complete recordings, 40 minutes, 78 annotated changes and 15 code ROIs were frozen before extraction changes. The set includes Mandarin and publishers beyond CCC. The wide projected Chinese source supports change labels but no legible code transcription; its code CER is not applicable. See the [source protocol](HOLDOUT_SCREEN_PROTOCOL.md), [execution plan](HOLDOUT_EXECUTION.md), and [fixed metric definitions](SCREEN_EVALUATION.md).

The single campaign used locked candidate `47c5334` on both paths. All six complete-talk runs finished; all 78 changes and 15 ROIs remain in the score denominators. Independent arithmetic and hash checks found no accounting discrepancy. No parameters, source labels, scoring rules or outcomes were changed after the campaign.

| Path | Changes captured | Information precision | Code CER | Complete-build coverage | Case time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mac, MLX + Vision | 73/78 (93.59%) | 73/358 (20.39%) | 436/2295 (19.00%) | 4/5 | 135.106 s |
| Docker, CPU + Tesseract | 73/78 (93.59%) | 73/360 (20.28%) | 369/2295 (16.08%) | 4/5 | 419.568 s |

**Recall meets 85%; precision misses 50% and code CER misses 15% on both paths.** No held-out frame was excluded as speaker/layout/animation, so the information and canonical control scores are identical. There were zero OCR exceptions and zero temporary-media leftovers. This establishes execution, not perfect extraction.

| Full source | Mac / Docker captured changes | Mac / Docker predictions | Code references |
| --- | ---: | ---: | --- |
| COSCUP Python, Mandarin, 16 min | 25/25, both | 220 / 221 | N/A: projected code is not legible enough to transcribe |
| EnthusiastiCon Shell, English, 17 min | 32/32, both | 105 / 106 | All 15 code ROIs, 2,295 characters |
| Wikimania WikiPie, English, 7 min | 16/21, both | 33 / 33 | N/A: no code ROI |

The five frozen misses are WikiPie labels at 124, 140, 152, 347 and 393 seconds. Post-result source inspection found the additions in nearby retained states, with source reveal brackets at 125.0–125.515, 142.0–142.524, 149.5–150.019, 344.5–345.010 and 394.0–394.524 seconds. These differ from the integer reference labels. At 124, 140 and 393 seconds, neither the source nor the SDK-selected image contains the later addition; at 152 and 347 seconds both already contain it. The findings image at 345 seconds is already complete; the frozen 4/5 temporal score does not prove lost findings content. A Shell comment also differs between the frozen transcription and the source. These are reference-quality caveats requiring owner review; no label, score or outcome was replaced. Code errors are measured from one English code talk; this set does not establish Chinese OCR accuracy or broad code coverage. These sources have no publisher captions, so held-out WER is not applicable. Development improvements did not establish the held-out precision or OCR targets.

The [post-result inspection](data/v140-holdout-error-inspection.json) compares saved images with source crops. Shell code is retained; the struct OCR splits aligned comments into a later column, while the parser has small, softened glyphs. A deterministic sample of eight Coscup extra boundaries shows projection setup, foreground people, presenter motion and repeated content. This is a sample, not exhaustive attribution; some triggers remain uncertain. The [owner review package](https://github.com/yy31104/clipmind/releases/download/v1.4.0/clipmind-v1.4.0-owner-review.zip) includes wider source context for the known reference questions.

Raw [Mac](data/v140-holdout-mac-results.json) and [Docker](data/v140-holdout-docker-results.json) reports retain all hypotheses, boxes, predictions and misses. The [summary](data/v140-holdout-summary.json), [independent audit](data/v140-holdout-independent-audit.json), [campaign lock](data/v140-holdout-campaign-lock.json) and [runtime byte manifest](data/v140-holdout-runtime.json) bind the results to their source, evaluator and implementation.

Development extraction used `13616c8`. The final candidate changes only the multilingual OCR default in `config.py`; the per-source development language overrides remain identical. Native development had stale 1.3.0 editable-install metadata while importing the verified 1.4.0 checkout bytes. The final campaign uses matching 1.4.0 package metadata and a clean candidate tree. Reports preserve the original provenance.

## CPU timing and comparisons

The published v1.3.0 image processed the complete 39:24 Xous talk in **450.892 seconds**, plus **92.120 seconds** for the separate first model download. ASR took 382.773 s, OCR 66.159 s and sampling 61.794 s; overlapping stages should not be summed. The [record](data/v140-full-xous-docker.json) includes the image digest, input checksum and empty cleanup inventory.

Docker measurements use Linux ARM64, six CPUs, 4 GiB and a warm offline model cache on an M4 Pro / 24 GB Mac. Development processes can overlap, so these timings are not isolated hardware comparisons. The CPU model resolves to int8_float32. Both Docker development runs explicitly use English; the new Mac run and final holdout use automatic speech language. Source preparation and downloads are separate from case times. Windows, x86 quality and CUDA were not measured.

Three runnable open-source tools use the same development inputs and their defaults in the [comparison table](TOOL_COMPARISON.md). Slidegeist and slides-extractor have higher boundary precision than ClipMind. Some tools retain later Xous content that v1.3.0 missed. Unavailable spatial OCR or speech measures stay not applicable.

## Review and reuse

The editable development and holdout review packs contain all 163 annotations, with before/after images and code crops, for an estimated 94-minute owner review. Until the completed table is returned, the annotation caveat remains. Downloads, source licenses and checksums are listed with the release evidence. Glama listing and community posts remain owner actions; the facts sheet contains measured numbers, links and a saved-frame GIF.
