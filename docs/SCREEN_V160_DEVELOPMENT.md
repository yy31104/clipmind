# Screen development results

The development pool has 16 recordings, including the formerly held-out
v1.5.0 talks. Thirteen recordings contribute 320 scored changes.
The code regions contain 7,056 reference characters. All comparisons
below use the same historical labels. The [revised v1.5.0 scores](SCREEN_REFERENCE_COMPARISON.md) use a separate set of labels.

| Runtime | Backend | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| v1.5.0 | Mac / Vision | 299/320 (93.44%) | 299/814 (36.73%) | 1230/7056 (17.43%) |
| v1.5.0 | Docker / Tesseract | 303/320 (94.69%) | 303/1070 (28.32%) | 916/7056 (12.98%) |
| v1.6.0 round 1 candidate | Mac / Vision | 301/320 (94.06%) | 301/692 (43.50%) | 1056/7056 (14.97%) |
| v1.6.0 round 1 candidate | Docker / Tesseract | 303/320 (94.69%) | 303/866 (34.99%) | 916/7056 (12.98%) |

On Vision, upscaling dark code panels raised code accuracy with almost
no change in boundary precision. The OCR-only run matched 301 of 815
predictions. Recurring-display comparisons excluded more of the camera
and layout changes. The first installed Tesseract run with that rule
matched 303 of 899 predictions.

A later Vision run with perspective registration had three OCR
deadline failures in one recording. The final candidate completed all
16 recordings on both backends. The per-recording [score receipts](data/v160-round1-development.json)
retain the incomplete run and checksums.

Residual errors include foreground occlusion in front of projected
slides and settled application views during a live demo. A replay that
excluded every single-sample state lost 17 Mac and 18 Docker matches
and still reached only 48.63% and 41.79% precision, respectively.
The final classifier kept those states eligible.

## Fade onsets

In the four recordings from the first holdout, the WordCamp Taipei
recording had 19 unmatched canonical changes per backend. For 18, the
nearest retained change was about one to one and a half seconds late.
The source shows slide elements becoming visible before their clear,
settled captures.

A replay kept every screenshot and moved starts back only where several
source frames showed the same content fading in. WordCamp matches rose
from 27 to 41 on Vision and from 25 to 38 on Tesseract, with no lost
matches on the four recordings. Hard cuts and one-frame flashes keep
their original timings in the regression tests.

[Per-recording replay](data/v160-development-fade-onsets.json)

## Native frame onsets

Sampling at 2 Hz can place a new state just after a reference frame even
when it already shows exactly the same screen. Several EmacsConf and
DebConf code references landed 33–40 ms before the next saved state.
The timeline therefore returned the preceding page.

The native-onset check compares the frames immediately before a sampled
change with the retained screen. It moves the onset only through a
continuous run of matching source frames. The retained image and its
capture time stay the same. A local video test confirms the decoder
timestamp on both modern and older FFmpeg versions.

In a saved-frame replay of the two code recordings, Vision errors fell
from 1,445 to 413 of 3,734 characters. Tesseract errors fell from 1,576
to 598. The replay uses the same OCR records, reference regions and
scorer. These are development results.

[Per-region replay](data/v160-development-native-onsets.json)

## Small terminal text

Tesseract keeps grayscale stroke detail when enlarging dark terminal
frames. Mixed light slides still use local dark-panel preprocessing.
A saved-frame comparison on the older code recordings reduced errors
from 916 to 742 of 7,056 characters. In the two recordings with verified
native onsets, errors fell from 598 to 556 of 3,734 characters. Some
regions regressed, including a small increase in one DebConf region.

[Older code recordings](data/v160-development-dark-terminal-historical.json) ·
[Code recordings from round 1](data/v160-development-dark-terminal-exposed.json)

The timing changes were also tested in the full pipeline on the four
exposed recordings.

| Backend | Recall | Precision | Code CER |
| --- | ---: | ---: | ---: |
| Mac / Vision | 186/211 (88.15%) | 186/907 (20.51%) | 413/3734 (11.06%) |
| Docker / Tesseract | 177/211 (83.89%) | 177/937 (18.89%) | 598/3734 (16.01%) |

The [development extraction audit](data/v160-development-timing-extraction.json)
includes the temporary native frames found after these runs. The
original inventory missed that directory. The pipeline now removes
those frames as soon as timing verification ends. Restart cleanup and
the benchmark check the same directory list.

## Dark code panels and presenter motion

The four recordings from round 2 are now development inputs. Full
re-extraction with local contrast for dark code and a fixed-room
presenter check gave the following results. The comparison uses the
[original round 2 scores](data/v160-round2-summary.json).

| Runtime | Backend | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| Round 2 | Mac / Vision | 128/139 (92.09%) | 128/608 (21.05%) | 940/6980 (13.47%) |
| Development `80163de` | Mac / Vision | 128/139 (92.09%) | 128/515 (24.85%) | 498/6980 (7.13%) |
| Round 2 | Docker / Tesseract | 129/139 (92.81%) | 129/582 (22.16%) | 2695/6980 (38.61%) |
| Development `80163de` | Docker / Tesseract | 129/139 (92.81%) | 129/515 (25.05%) | 999/6980 (14.31%) |

The room check initially suppressed a new diagram inside the area a
presenter had occupied. The revised check requires nearby views on
both sides and keeps boundaries with new local image detail. On saved
frames, the revised OCR read 499/6980 characters incorrectly on Vision
and 977/6980 on Tesseract. Both engines still missed the projected
JSON-LD example, where some glyphs are only five pixels high.

[Per-recording development receipts](data/v160-development-room-ocr.json)

## Information-boundary ranking

The ranker combines a small fitted logistic model and a fixed decision
forest. It compares neighboring raster texture, screen motion, OCR
layout and changes in visible words. All frames and OCR stay in the
full pack. The frames view can show the marked information changes.

The development folds kept 12 producers separate, with both backend
copies of each recording in the same fold. There are 21 scored
recordings and 670 reference events per backend. The shared cutoff
was selected from these exposed predictions.

| Development cohort | Backend | Recall | Precision |
| --- | --- | ---: | ---: |
| All exposed | Mac / Vision | 588/670 (87.76%) | 588/1086 (54.14%) |
| All exposed | Docker / Tesseract | 585/670 (87.31%) | 585/1080 (54.17%) |
| Former round 2 | Mac / Vision | 121/139 (87.05%) | 121/226 (53.54%) |
| Former round 2 | Docker / Tesseract | 121/139 (87.05%) | 121/238 (50.84%) |

The former round 2 cohort has two producers, with 116 of its 139
references from FOSS4G. No shared cutoff passed every earlier cohort
separately on both backends. Two real changes within a second can
also compete for one prediction.

[Training inputs and per-recording development scores](data/v160-development-rank.json)

The same four recordings also went through full extraction with and
without ranking. Both rows use the guarded room and revised OCR code.

| Backend | Boundary mode | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| Mac / Vision | Before ranking | 128/139 (92.09%) | 128/572 (22.38%) | 499/6980 (7.15%) |
| Mac / Vision | With ranking | 126/139 (90.65%) | 126/196 (64.29%) | 499/6980 (7.15%) |
| Docker / Tesseract | Before ranking | 130/139 (93.53%) | 130/565 (23.01%) | 977/6980 (14.00%) |
| Docker / Tesseract | With ranking | 125/139 (89.93%) | 125/202 (61.88%) | 977/6980 (14.00%) |

The deployed fit includes earlier extractions of these recordings.
The ranking pass took 15.3 seconds across four Mac runs and 18.5
seconds across four Docker runs. The installed candidate kept the
same canonical counts and code errors on each backend.

[Full-extraction score receipts](data/v160-development-rank-extraction.json)
