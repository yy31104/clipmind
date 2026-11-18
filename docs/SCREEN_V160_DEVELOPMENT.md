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
