# Screen holdout round 3 results

The [round 3 lock](data/v160-round3-campaign.json) fixes the inputs,
references, candidate and scorer. Both paths use the same four full
recordings. The [protocol](SCREEN_V160_ROUND3.md) lists the sources
and the independent same-model annotation agreement.

| Backend | Recall | Precision | Code CER | Total processing time |
| --- | ---: | ---: | ---: | --- |
| Mac / Vision | 154/174 (88.51%) | 154/274 (56.20%) | 171/1675 (10.21%) | 5.1 min |
| Docker / Tesseract | 154/174 (88.51%) | 154/271 (56.83%) | 131/1675 (7.82%) | 13.8 min |

Both backends met the three pooled targets on these four recordings.
Processing times use pre-staged local files and a warm model cache.
The evaluation forces full processing, uses automatic speech-language
detection and sets OCR languages per recording.

The slide-heavy AppImage talk supplied 69 of 174 reference changes and
67 Mac and 68 Docker matches. Across the other three recordings, Mac
matched 87/105 references with 87/203 precision, and Docker matched
86/105 with 86/201 precision. Inkscape and C/Make produced extra
boundaries on both backends. Code CER covers 15 regions in the English
and German recordings.

Selection handles and tooltips account for some extras in Inkscape.
Mac matched 7 of 10 glyph-edit references and Docker matched 2. Both
matched all 10 commands and results. In C/Make, small edits were
sometimes absent from the retained timeline at the expected time.
Whole-line OCR boxes can also bring neighboring characters into a
scored region or put a recognized line outside it. The Mac hexdump
needed 101 edits over 240 reference characters, including 97 insertions.
Both backends missed two
tight German code regions in scoring even though the OCR calls completed.

## Mac / Vision

| Recording | Status | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| r03-a01 | measured | 36/38 (94.74%) | 36/70 (51.43%) | 125/975 (12.82%) |
| r03-a02 | measured | 17/20 (85.00%) | 17/55 (30.91%) | — |
| r03-a03 | measured | 34/47 (72.34%) | 34/78 (43.59%) | 46/700 (6.57%) |
| r03-a04 | measured | 67/69 (97.10%) | 67/71 (94.37%) | — |

Canonical boundary precision was 163/465 (35.05%). All OCR calls
completed without errors, and no temporary media remained.
[Full results](data/v160-round3-mac-results.json) · [Attempt receipt](data/v160-round3-mac-once.json)

## Docker / Tesseract

| Recording | Status | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| r03-a01 | measured | 36/38 (94.74%) | 36/75 (48.00%) | 48/975 (4.92%) |
| r03-a02 | measured | 12/20 (60.00%) | 12/40 (30.00%) | — |
| r03-a03 | measured | 38/47 (80.85%) | 38/86 (44.19%) | 83/700 (11.86%) |
| r03-a04 | measured | 68/69 (98.55%) | 68/70 (97.14%) | — |

Canonical boundary precision was 164/465 (35.27%). All OCR calls
completed without errors, and no temporary media remained.
[Full results](data/v160-round3-docker-results.json) · [Attempt receipt](data/v160-round3-docker-once.json)

[Machine-readable summary](data/v160-round3-summary.json)
