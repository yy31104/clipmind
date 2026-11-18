# v1.6.0 holdout results, round 2

The [round 2 lock](data/v160-round2-campaign.json) fixes the inputs,
references, candidate and scorer. Both paths use the same four full
recordings. The [protocol](HOLDOUT_V160_ROUND2.md) lists the sources
and the independent same-model annotation agreement.

| Backend | Recall | Precision | Code CER | Sum of recording times |
| --- | ---: | ---: | ---: | --- |
| Mac / Vision | 128/139 (92.09%) | 128/608 (21.05%) | 940/6980 (13.47%) | 5.6 min |
| Docker / Tesseract | 129/139 (92.81%) | 129/582 (22.16%) | 2695/6980 (38.61%) | 17.1 min |

Round 2 missed the release gate. Both paths cleared 85% recall and
missed 50% precision. Only Vision cleared the 15% code CER target.
The DuckDB and Terra Draw demos accounted for 459 of 480 unmatched
information predictions on Mac and 434 of 453 on Docker. These four
recordings now join the development pool.

## Mac / Vision

| Recording | Status | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| r02-a01 | measured | 23/23 (100.00%) | 23/41 (56.10%) | 269/269 (100.00%) |
| r02-a02 | measured | 22/22 (100.00%) | 22/25 (88.00%) | 497/1503 (33.07%) |
| r02-a03 | measured | 27/27 (100.00%) | 27/313 (8.63%) | 19/1665 (1.14%) |
| r02-a04 | measured | 56/67 (83.58%) | 56/229 (24.45%) | 155/3543 (4.37%) |

Canonical boundary precision was 130/1029 (12.63%). The receipt records
0 OCR failures and 0 cases with temporary files left.
[Full results](data/v160-round2-mac-results.json) · [Attempt receipt](data/v160-round2-mac-once.json)

## Docker / Tesseract

| Recording | Status | Recall | Precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| r02-a01 | measured | 23/23 (100.00%) | 23/38 (60.53%) | 269/269 (100.00%) |
| r02-a02 | measured | 22/22 (100.00%) | 22/26 (84.62%) | 566/1503 (37.66%) |
| r02-a03 | measured | 27/27 (100.00%) | 27/249 (10.84%) | 208/1665 (12.49%) |
| r02-a04 | measured | 57/67 (85.07%) | 57/269 (21.19%) | 1652/3543 (46.63%) |

Canonical boundary precision was 130/1031 (12.61%). The receipt records
0 OCR failures and 0 cases with temporary files left.
[Full results](data/v160-round2-docker-results.json) · [Attempt receipt](data/v160-round2-docker-once.json)

The Docker report's path redactor changed URL text in three DuckDB code
rows after scoring. The [corrected text](data/v160-round2-docker-ocr-text-corrections.json)
gives the original strings. The saved report and all score fields are unchanged.

[Machine-readable summary](data/v160-round2-summary.json)
