# Screen motion development

The selected stationary-stroke rule plus supported speaker-pane typing matches **59 of 60** fixed development references with **228 predictions**: **98.33% recall and 25.88% precision**. Recall exceeds the 85% target; precision remains below the 50% target. These are development measurements, not held-out quality claims. Annotations are AI-assisted; human review is pending.

| Saved-frame measurement | Matched / 60 | Predictions | Precision |
| --- | ---: | ---: | ---: |
| Original baseline canonical control | 44 | 159 | 27.67% |
| Original baseline with pane helper | 43 | 145 | 29.66% |
| Original retention with pane helper | 59 | 365 | 16.16% |
| Old 347-sample v4 control | 59 | 1,087 | 5.43% |
| Old 347-sample stationary variant | 58 | 362 | 16.02% |
| Corrected-clock v4 raw | 60 | 1,121 | 5.35% |
| Corrected-clock v4 with pane typing | 59 | 351 | 16.81% |
| Corrected-clock stationary raw | 59 | 371 | 15.90% |
| Corrected-clock stationary with integrated typing | 59 | 228 | 25.88% |

All ten original clips were sampled once with the corrected clock: 360 actual-PTS samples per 180-second clip at 2 fps and 640 pixels wide. Both dedupe functions used fresh clones of the same frames. Retained images were promoted from the original source at the selected sample indexes to 1280 pixels; each promoted PTS was checked against the sample manifest. No image upsampling was used.

The old elapsed-since-last sampler accumulated 0.52-second steps on 25 fps video and produced 347 samples. Its reports remain preserved and marked superseded. The first corrected-clock promotion setup failed on a long FFmpeg expression. Recovery reused the saved 360-frame sets with an equivalent balanced expression and completed all ten cases without failure.

The stationary rule keeps the existing activation and tile thresholds, then requires at least 80% of activated current pixels to change by no more than 12 gray levels at the next sample. The last sample stays conservative. Speaker typing requires a bounded image-derived pane, recurring local face support, and fine RGB stability over the whole screen outside that pane. It changes labels without deleting, reordering, or retiming canonical frames.

The final integration measurement replays previously measured local motion evidence through `annotate_change_kinds` on identical source images, indexes, and capture times. No OCR or ASR inference was rerun for the corrected-clock selections. Text and layout are empty, so no further camera-layout exclusions are asserted. This is a conservative saved-frame ablation, not an end-to-end OCR pipeline evaluation. All 629 selected canonical frames remain present. The ten frozen code/text/diagram ROIs preserve their visible content on source-versus-retained inspection, including all three code lines at Xous 68 seconds. That visual check is not an OCR accuracy score.

The Xous edge patch is a branded 39C3 / XOUS × BAOCHIP badge, not a recognized timecode. Original-source crops show encoded shading changes; they do not establish whether the cause is animation or compression. No badge exclusion, arbitrary localized mask, or outside-pane threshold relaxation was added. Full camera views and unresolved image-only changes remain uncertain. A synthetic residual risk remains: large actor motion can dominate the global stationary-pixel ratio and suppress a smaller stable new code stroke at the optional detail gate, although the global hash may retain it separately.

The targeted verification command passed 34 tests, including three pipeline integration tests for positive pane evidence, failure remaining uncertain, and a small new code stroke outside the pane. Existing same-title diagram and shifted-layout regressions pass. These measurements use development excerpts only.

The portable evidence bundle is [clipmind-v1.4.0-motion-evidence.zip](https://github.com/yy31104/clipmind/releases/download/v1.4.0/clipmind-v1.4.0-motion-evidence.zip) (410,620,501 bytes). It preserves reports, setup failures, source hashes, sample manifests, derived images, source ROI comparisons, proposed patches, tests, and the residual-risk record. Original videos are omitted. Paths are sanitized, and all 12,646 payload checksums were verified. The archive SHA-256 is `f287bb7033836e2dc86f45d6c01d03e5ddb004b8d06383eb1f4c374a9a0f0c1e`. Machine-readable per-case scores and provenance are in [v140-motion-development.json](data/v140-motion-development.json). Source provenance and attribution follow [REAL_TALK_BENCHMARK.md](REAL_TALK_BENCHMARK.md).
