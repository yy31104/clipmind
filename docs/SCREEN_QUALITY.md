# Screen quality: v1.5.0

Targets, unchanged since v1.4.0: change recall at least 85%, information-boundary precision at least 50%, code character error rate (CER) at most 15%, on both paths. Annotations are AI-assisted; human review is pending.

| Set | Path | Version | Changes captured | Information precision | Code CER |
| --- | --- | --- | ---: | ---: | ---: |
| Development | Mac, MLX + Vision | v1.4.0 | 137/138 (99.28%) | 137/585 (23.42%) | 461/2891 (15.95%) |
| Development | Mac, MLX + Vision | v1.5.0 | 130/138 (94.20%) | 130/315 (41.27%) | 295/2891 (10.20%) |
| Development | Docker, CPU + Tesseract | v1.4.0 | 137/138 (99.28%) | 137/584 (23.46%) | 503/2891 (17.40%) |
| Development | Docker, CPU + Tesseract | v1.5.0 | 130/138 (94.20%) | 130/377 (34.48%) | 367/2891 (12.69%) |
| New holdout, one run | Mac, MLX + Vision | v1.5.0 | 169/182 (92.86%) | 169/499 (33.87%) | 935/4165 (22.45%) |
| New holdout, one run | Docker, CPU + Tesseract | v1.5.0 | 173/182 (95.05%) | 173/693 (24.96%) | 549/4165 (13.18%) |

On development data, recall and code CER meet their targets on both paths and precision does not. **On the new holdout, recall meets the target on both paths and Docker code CER meets it. Precision misses 50% on both paths, and Mac code CER misses 15%.** The canonical control, which counts every retained state, is 176/1119 (15.73%) on Mac and 176/1118 (15.74%) on Docker. Typing states as speaker, layout or animation costs 7 matches on Mac and 3 on Docker.

## Development set

The ten 39C3 excerpts (60 changes, 7 code regions, 593 characters) plus the three complete v1.4.0 holdout talks (78 changes, 15 code regions, 2,298 characters). The v1.4.0 holdout was scored once and is development data now. Its files, results and assets are unchanged. A [corrected copy of its labels](data/v150-development-labels.json) moves five WikiPie times to the first source frame that shows each addition and fixes one Shell comment; each correction is listed in its changelog.

Before changing code, every extra boundary and code error of the v1.4.0 baseline was attributed ([boundaries](data/v150-development-boundary-attribution.json), [code](data/v150-development-code-attribution.json)). Across both paths there were 460 distinct extra boundaries: 208 speaker or people motion, 114 recognition differences on the same small-text page, 54 transition frames, 46 layout or camera changes and 38 animation frames. Code errors were mostly small text (Mac 393 of 461 errors), then background text and comment reading order (Docker 125 and 86 of 503).

## What each change did

Boundary rules were measured by reclassifying the saved v1.4.0 development packs, which isolates them from OCR. Code changes were measured on the exact frames the baseline scored. The full runs in the first table are the reported results.

| Boundary rule | Mac precision | Docker precision |
| --- | ---: | ---: |
| v1.4.0 | 137/585 (23.42%) | 137/584 (23.46%) |
| Compare with the last information screen, all rules | 129/293 (44.03%) | 131/414 (31.64%) |
| without one-sample transition settling | 129/352 (36.65%) | 131/427 (30.68%) |
| without return to the last information screen | 129/313 (41.21%) | 132/429 (30.77%) |
| without the transient-motion test | 129/304 (42.43%) | 131/420 (31.19%) |
| without the same-page misread test | 131/306 (42.81%) | 131/427 (30.68%) |
| without misread word matching | 129/307 (42.02%) | 135/438 (30.82%) |
| without the low-confidence filter | 129/293 (44.03%) | 135/475 (28.42%) |

The rules trade recall for precision: development matches fall from 137 to 129 (Mac) and 131 (Docker) in this replay. A rule for changes outside the bright screen area had no measurable effect and was removed. Ignoring stroke-like fragments was measured afterwards on the newer development packs: Mac 130/317 → 130/315, Docker 131/416 → 130/377.

| Code change, exact-frame diagnostic | Mac Vision CER | Docker Tesseract CER |
| --- | ---: | ---: |
| v1.4.0 | 461/2891 (15.95%) | 503/2891 (17.40%) |
| Keep trailing comments on their code line | 461/2891 (15.95%) | 422/2891 (14.60%) |
| Vision: recover lines the high-contrast pass missed | 295/2891 (10.20%) | not used |
| Tesseract: binarize dark code panels inside light slides | not used | 367/2891 (12.69%) |

Three- and four-times upscaling did not reduce Tesseract errors on the smallest development code and was not kept. The [classification](data/v150-development-ablation.json) and [OCR](data/v150-development-ocr-diagnostics.json) records are kept.

## New holdout

Three complete recordings, 69 minutes, 182 changes and 15 code regions, frozen in a [separate commit](HOLDOUT_V150_PROTOCOL.md) before any extraction change: FOSDEM 2026 (English Go code with a speaker inset), PyCon Taiwan 2024 (Mandarin slides with Python and YAML code and a live demo) and COSCUP 2022 (Mandarin, room camera). Code regions come from two talks in two languages; the room camera's only code is unreadable at source and is N/A.

The single campaign used locked candidate `28f8669` on both paths. Both paths finished all three recordings with no OCR exceptions and no leftover temporary media. No parameter, label or result was changed after the campaign, and nothing was rerun.

| Recording | Mac / Docker changes | Mac / Docker predictions | Mac / Docker code CER |
| --- | ---: | ---: | ---: |
| FOSDEM synctest, English, 23 min | 44/45, 45/45 | 73 / 80 | 34/1588 (2.14%) / 42/1588 (2.64%) |
| PyCon Taiwan observability, Mandarin, 31 min | 83/87, 85/87 | 255 / 338 | 901/2577 (34.96%) / 507/2577 (19.67%) |
| COSCUP 2022 government data, Mandarin room camera, 16 min | 42/50, 43/50 | 171 / 275 | N/A |

Post-result inspection, without tuning:

- **Mac code CER** comes almost entirely from PyCon. 867 of its 901 errors are deletions: Vision skips many lines inside the dark code windows on light Mandarin slides. The Tesseract path binarizes such panels; the Vision path does not.
- **Precision** is lowest on the room camera (COSCUP) and in the PyCon live demo, where people, camera moves, scrolling and menus produce states that still add recognized text.
- **Misses**: on Mac, 8 COSCUP changes (three of them within five seconds around 637–642 s), 4 PyCon changes and the FOSDEM closing terminal command; on Docker, 7 COSCUP and 2 PyCon changes.

Raw [Mac](data/v150-holdout-mac-results.json) and [Docker](data/v150-holdout-docker-results.json) reports keep every prediction, match and miss; the [summary](data/v150-summary.json) also lists the final development reports. The [campaign lock](data/v150-holdout-campaign-lock.json), [runtime manifest](data/v150-holdout-runtime.json) and [execution record](data/v150-holdout-execution.json) bind them to the candidate, image and evaluator. The host slept during the Docker run, so its elapsed time is not a speed measurement; per-recording times use a monotonic clock.

## Review

The [holdout review package](https://github.com/yy31104/clipmind/releases/download/v1.5.0/clipmind-v1.5.0-holdout-review.zip) has all 197 annotations with source images and code crops, and a blank CSV, for about 106 minutes of review. Until it is returned, results say: AI-assisted annotations; human review pending.

v1.4.0 results, comparisons and CPU timings remain in the [v1.4.0 version of this page](https://github.com/yy31104/clipmind/blob/v1.4.0/docs/SCREEN_QUALITY.md).
