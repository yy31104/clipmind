# Screen extraction evaluation

Protocol: `screen-evidence-v1`. The original ten 39C3 excerpts are the development set. New sources, intervals and source-frame annotations are frozen before extraction changes. Every incremental content addition remains a reference boundary. Build groups and full-build frame coverage are secondary measures; they never reduce the primary denominator.

Annotations are AI-assisted; human review is pending. Publisher caption agreement is not a claim about transcription against a human transcript.

## Fixed measures

- **Canonical control:** every retained state's start inside the review interval, unchanged from the v1.3 evaluator. Keep this result alongside the information score, including when it gets worse.
- **Information boundaries:** the same predictions, except explicit `change_kind` values `speaker`, `layout` and `animation` without extraction warnings. A deduplication warning or OCR error keeps a prediction included regardless of its type. Those frames remain saved and appear in the inventory. `content`, `build`, `uncertain`, missing and unfamiliar values count. The v1.3 output has no types, so all its predictions count.
- **Matching:** sort timestamps and match one-to-one within one second, as in v1.3. Duplicate predictions cannot improve recall. A source interval without an annotated slide sequence is not applicable, not a perfect score.
- **Code CER:** use the actual SDK frame covering each frozen timestamp and OCR blocks whose centers fall within its source ROI. NFKC normalization removes whitespace and preserves case. Count substitutions, deletions and insertions against reference characters. Keep each ROI result, including failures. Report code ROIs separately from prose and UI text. This measures retention and recognition together.
- **Missing results:** a missing frame, unavailable OCR record or failed extraction contributes an empty text hypothesis, so its reference characters remain in CER as deletions. Keep the failure status and reason as well. Before the first extraction call, every planned case is saved with its full reference counts and empty predictions. Interrupted and unattempted cases keep those scores; a campaign cannot claim a successful subset as the full holdout.
- **Complete-build coverage (secondary):** ask the SDK for the frame covering each frozen build reference time, then check its `source_timestamp` against the source-labeled complete-build interval. Do not select another frame that happens to score better. Missing capture provenance is unknown. This measures temporal coverage, not pixel accuracy or legibility, and never replaces per-addition boundary recall.
- **Caption WER:** compare fully contained publisher cues with words in the same interval; normalize NFKC, lowercase and remove punctuation. Missing publisher captions stay not applicable. These captions may themselves contain errors.

The holdout targets stay at 85% information-boundary recall, 50% precision and 15% code CER. The source selection, labels, providers and candidate bytes are frozen before its single evaluation campaign. A failed campaign remains a result. Do not tune against it or replace its report.

## Error attribution

Fresh reports include every reference, every predicted boundary, matches, and all missed changes and extra boundaries. Source frames before and after each error support the attribution. Categories are progressive build, speaker, layout, animation, small text, background text, reading order, transition, duplicate, timing, other and unresolved. A cause is blank until the source pair has been examined; timestamps and prediction counts alone cannot establish a cause.

The v1.3 report contains matched timestamps and prediction counts, but not every predicted timestamp. Preserve it unchanged. A new baseline run supplies the exhaustive inventory rather than guessing its missing events.

## Retaining runs

Each evaluation needs a new `--output`. Rescoring requires `--previous OLD.json --output NEW.json`; it keeps the original extraction provenance and wall time. Reports store `<WORKDIR>` placeholders rather than local paths. Optional `--once-token` claims a campaign marker before the first extraction call. It remains after failure or interruption and cannot be reused. Checkpoints update only the newly claimed run, never an older result.

## Source review package

`scripts/review_screen_labels.py` builds a local HTML page and fillable CSV from source inputs. Each change includes its frozen description and a source pair half a second before and after. Optional collapsed context spans two seconds each way for uncertain labels; the matching tolerance remains one second. Every OCR reference also has its exact source frame and native-size crop. No extraction model or predicted state is involved. The manifest binds the generator, corpus, annotations and input bytes with SHA-256 checksums. Older review archives remain available.

Open `index.html`, fill in accept/edit/reject/unsure and save the CSV. A half-minute per change and a minute per OCR reference gives a rough review budget; the package lists its count and estimate. No annotation is omitted to fit that estimate. The owner completes this review; until the returned table is incorporated, all reports keep “AI-assisted annotations; human review pending.” Images and review ZIPs belong in release assets, not Git.
