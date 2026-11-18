# v1.6.0 holdout results

The [round 1 lock](data/v160-round1-campaign.json) fixes the inputs,
references, candidate and scorer. Both paths use the same four full
recordings. The [protocol](HOLDOUT_V160_PROTOCOL.md) lists the sources
and the independent same-model annotation agreement.

| Backend | Recall | Precision | Code CER | Runtime |
| --- | ---: | ---: | ---: | --- |
| Mac / Vision | 168/211 (79.62%) | 168/894 (18.79%) | 1445/3734 (38.70%) | 8.2 min |
| Docker / Tesseract | 162/211 (76.78%) | 162/931 (17.40%) | 1576/3734 (42.21%) | 18.8 min |

Both backends missed all three targets: 85% recall, 50% precision and
15% code CER. EmacsConf and Blender supplied 584 of the 726 extra Mac
boundaries and 647 of the 769 extra Docker boundaries. The WordCamp Taipei
recording supplied 21 of the 43 misses on Mac and 23 of the 49 on Docker.
These four recordings now belong to the development pool.

## Mac / Vision

| Recording | Status | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| b01 | measured | 52/53 (98.11%) | 52/274 (18.98%) | — |
| b02 | measured | 57/75 (76.00%) | 57/419 (13.60%) | 1287/3286 (39.17%) |
| b03 | measured | 27/48 (56.25%) | 27/44 (61.36%) | — |
| b04 | measured | 32/35 (91.43%) | 32/157 (20.38%) | 158/448 (35.27%) |

Canonical boundary precision was 181/1779 (10.17%). The receipt records
0 OCR failures and 0 cases with temporary files left.
[Full results](data/v160-round1-mac-results.json) · [Attempt receipt](data/v160-round1-mac-once.json)

## Docker / Tesseract

| Recording | Status | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| b01 | measured | 44/53 (83.02%) | 44/298 (14.77%) | — |
| b02 | measured | 61/75 (81.33%) | 61/454 (13.44%) | 1371/3286 (41.72%) |
| b03 | measured | 25/48 (52.08%) | 25/40 (62.50%) | — |
| b04 | measured | 32/35 (91.43%) | 32/139 (23.02%) | 205/448 (45.76%) |

Canonical boundary precision was 183/1777 (10.30%). The receipt records
0 OCR failures and 0 cases with temporary files left.
[Full results](data/v160-round1-docker-results.json) · [Attempt receipt](data/v160-round1-docker-once.json)

[Machine-readable summary](data/v160-round1-summary.json)
