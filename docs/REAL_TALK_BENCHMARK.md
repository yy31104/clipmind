# v1.3.0 development benchmark

See [v1.4.0 screen quality](SCREEN_QUALITY.md) for newer results. These historical measurements remain unchanged.

Run: **2026-11-16**, M4 Pro / 24 GB, macOS, Python 3.12, local MLX Whisper
large-v3-turbo (English) and Apple Vision. All ten excerpts finished with complete
transcript/OCR/visual-state artifacts and **zero temporary-media leftovers**.
This establishes execution, not perfect content recovery.

This is **ten fixed 180-second excerpts, 30 minutes total**, not ten complete
videos. A separate [39:24 complete-talk demo](REAL_TALK_DEMO.md) verifies URL
acquisition, search-to-slide navigation and actual agent MCP use. The excerpts
exercise the production local-media path after bounded HTTP source preparation;
they do not validate ten website adapters. All are English 39C3 talks, so this
is not a multilingual or cross-conference benchmark.

## Table

“Captured” is matched informational page/build boundaries within ±1 s, with
one-to-one matching. “Precision” also counts extra canonical-state boundaries:
speaker/layout/other visual changes can lower it. This is an operational test
of retained visual changes, not a semantic slide-classifier accuracy score.
CER/WER are error rates: **lower is better**. N/A is excluded, never scored as
perfect. OCR is measured end-to-end against source ROIs, including loss from
frame selection, reading order, background text and recognition.

| Public source | Original window | Captured changes | Boundary precision | Code ROI CER | Publisher-caption WER |
| --- | --- | --- | --- | --- | --- |
| [Code to Craft](https://media.ccc.de/v/39c3-code-to-craft-procedural-generation-for-the-physical-world) | 14:00–17:00 | N/A: animated montage | N/A | N/A | 4.78% |
| [Asahi Linux](https://media.ccc.de/v/39c3-asahi-linux-porting-linux-to-apple-silicon) | 15:00–18:00 | 9/12 (75.0%) | 9/53 (17.0%) | N/A | 5.86% |
| [Variable Fonts](https://media.ccc.de/v/39c3-variable-fonts-it-was-never-about-file-size) | 17:00–20:00 | N/A: continuous font demo | N/A | 0.0% (one CSS declaration) | 3.53% |
| [Pwn2Roll](https://media.ccc.de/v/39c3-pwn2roll-who-needs-a-599-remote-when-you-have-wheelchair-py) | 22:30–25:30 | N/A: speaker only | N/A | N/A | 12.89% |
| [Pixel TitanM2](https://media.ccc.de/v/39c3-reverse-engineering-the-pixel-titanm2-firmware) | 14:30–17:30 | 4/5 (80.0%) | 4/11 (36.4%) | 6.58% | 3.04% |
| [Xous](https://media.ccc.de/v/39c3-xous-a-pure-rust-rethink-of-the-embedded-operating-system) | 15:30–18:30 | 6/10 (60.0%) | 6/14 (42.9%) | 47.50% | 1.15% |
| [Cryptography bugs](https://media.ccc.de/v/39c3-how-to-minimize-bugs-in-cryptography-code) | 16:00–19:00 | 3/4 (75.0%) | 3/5 (60.0%) | 5.56% | 2.26% |
| [Text rendering](https://media.ccc.de/v/39c3-the-art-of-text-rendering) | 15:00–18:00 | 3/3 (100.0%) | 3/4 (75.0%) | N/A; prose CER 0.47% | 4.30% |
| [Photon science](https://media.ccc.de/v/39c3-the-eyes-of-photon-science-imaging-simulation-and-the-quest-to-make-the-invisible-visible) | 15:30–18:30 | 6/10 (60.0%) | 6/38 (15.8%) | N/A; diagram-label CER 0.0% | 2.68% |
| [Fuzzer behaviour](https://media.ccc.de/v/39c3-demystifying-fuzzer-behaviour) | 15:30–18:30 | 13/16 (81.3%) | 13/34 (38.2%) | 71.78% | 3.87% |

Reference-word-weighted caption WER: **4.22%**. Across the seven applicable
windows, **44/60** labeled changes match a canonical boundary within ±1 s;
there are **159** predicted boundaries. Those counts disclose missed builds
and extra states rather than hiding them behind the complete-pack count.

The individual source URLs, exact original durations, authors and recording
languages are in the [manifest](data/real-talk-cases.json); consult it rather
than assuming these shortened titles identify arbitrary mirrors.

## How to interpret the errors

- Xous's 47.50% code CER mostly reflects a retained frame missing two later
  code-build lines. A complete pack can still miss a small visual update.
- The Fuzzer sample overlays code and faint terminal statistics. OCR includes
  background statistics inside the selected ROI and sometimes changes reading
  order. Its 71.78% is foreground-reference, end-to-end extraction error, not
  an isolated OCR engine's character accuracy on clean code.
- The USB/Asahi recording has many layout/presenter changes and subtle bullet
  additions. Canonical visual changes are not one-to-one with slides.
- Pwn2Roll's selected window contains no slides. Code to Craft and Variable
  Fonts contain continuously animated demonstrations. Their speech is still
  scored, but slide precision/recall is inapplicable by source review.
- Code OCR coverage is only five windows (seven small ROIs); one is a single
  CSS declaration. It is a useful diagnostic sample, not a large code corpus.

## References were frozen before extraction

Sources were selected for public licensing, English audio, available publisher
captions and varied technical content. For each talk the fixed window begins
at `floor(duration * 0.4 / 30) * 30` and lasts 180 seconds. No window was moved
after seeing extraction quality. Preparation uses HTTP range seeking, checks
the actual duration and preserves source/caption SHA256 values.

All 30 one-second contact sheets were inspected independently of production
outputs; ambiguous points and code references were checked on source frames.
The [frozen labels](data/real-talk-labels.json) contain source hashes, boundaries,
normalized OCR ROIs and exclusions. **AI-assisted annotations; human review
pending.** These labels can contain mistakes. A camera-obscured slide change
is labeled when first visible, without guessing its hidden time.

Publisher VTT files are hosted by CCC and identify **transcribee** generation.
They are not independently verified human transcripts. WER therefore measures
**agreement with published, ASR-generated captions**, not absolute transcription
accuracy. Fully contained cues define the comparison interval; only ASR words
inside that interval are compared. Unicode NFKC, lowercase and punctuation
removal are applied. OCR CER uses NFKC and removes whitespace but preserves
case. Original reference/hypothesis text and error counts remain in the report.

## Reproduce

```bash
uv sync --frozen --python 3.12
uv pip install -r scripts/requirements-eval.txt
uv run --frozen python scripts/prepare_real_talks.py --workdir /tmp/clipmind-real-talks --all --jobs 2
uv run --frozen python scripts/evaluate_real_talks.py --corpus docs/data/real-talk-cases.json --labels docs/data/real-talk-labels.json --workdir /tmp/clipmind-real-talks --output /tmp/clipmind-real-talk-results.json --asr-provider mlx --ocr-provider vision
```

The defaults above are for the measured Mac. Linux/Windows can choose
`--asr-provider faster-whisper --ocr-provider tesseract`; those are a different
quality run, not equivalent recorded results. Keep prepared media outside the
checkout. A different encoder build can produce different excerpt bytes: if
hashes differ, labels are rejected before any model runs. Review/rebind source
annotations explicitly; never bypass that check or replace labels with program
predictions to obtain a pass.

[Raw results](data/real-talk-results.json) include source/caption/label hashes,
completeness, matching pairs, ROI reference/hypothesis, diagnostics and timings.
Machine-specific work paths are replaced with `<WORKDIR>`. Runtime source-byte
fingerprints and dependency versions describe the scoring environment; the
original run's base revision/dirty-tree provenance is retained separately.
The optional `score` mode reuses completed packs, refuses changed caption
inputs, and preserves extraction wall times. Evaluator review caught a
sub-microsecond floating-point boundary comparison error; it now has a
regression test. The final table comes from a fresh complete extraction of all
ten inputs after that fix. Its scores match the initial run. No boundary, ROI,
reference text or applicability decision was changed; only an incorrectly typed
label timestamp was corrected from the original file's modification time,
with the initial label-file digest preserved in the metadata.

The real run took about two minutes for the ten prepared excerpts, with a warm
cached model reused within the evaluator. This excludes source preparation,
model download and the separately measured full-talk acquisition. It is not a
cold-install throughput result. Each SDK run retained all configured evidence;
cleanup checks cover acquisition workspaces, ledgers, source media, temporary
audio and sample directories. The prepared inputs are evaluation-owned files
and are removed separately after publication artifacts are generated.

All recordings and derived reference excerpts/screenshots are attributed to
their authors and CCC under [CC BY 4.0](http://creativecommons.org/licenses/by/4.0).
Reproduction downloads public sources; it does not use browser cookies.
