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
