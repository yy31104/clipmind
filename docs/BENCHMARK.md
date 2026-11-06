# Benchmarks and reproducibility

Three commands cover the release checks:

```bash
make test   # deterministic unit, failure-injection, recovery, and API tests
make eval   # evaluate locally completed real-video Evidence Packs
make bench  # deterministic bounded-scheduler benchmark
```

`make eval` expects the sources in `eval/cases.json` to have already completed
under `out/`. It never downloads media implicitly. This keeps evaluation
network state and local cookie authorization explicit.

`make eval-synthetic` creates a temporary silent four-slide video and runs the
real local visual pipeline. Its recorded result is
[`silent-slides-eval.json`](data/silent-slides-eval.json); no generated media is kept.

The checked-in real-video report currently records 3/3 structurally complete
packs, no cleanup leftovers, and source-duration/processing-time ratios from
4.84× to 6.36×. The detailed extraction metrics are in
[`eval-results.json`](data/eval-results.json) and the methodology is in
[`REAL_WORLD_EVAL.md`](REAL_WORLD_EVAL.md).

The OCR resolution experiment is deliberately separate. On the 227-second
code/UI source, 1280 px OCR recognized 1034 unique characters versus 559 at
640 px. OCR wall time increased from 24.7 seconds to 36.6 seconds. See
[`ocr-resolution-experiment.json`](data/ocr-resolution-experiment.json).

The scheduler benchmark uses eight 50 ms simulated jobs so it measures the
queue itself rather than network, model cache, or video content. The recorded
run reached four active jobs, left four queued at capacity, respected the limit,
and produced the speedup recorded in [`benchmark.json`](data/benchmark.json). Exact
wall times vary by machine; the asserted properties are bounded peak concurrency
and observable backpressure.

Newly generated manifests include per-stage wall time for acquisition, sampling,
ASR, OCR, and preview derivation. Job state adds end-to-end time. Older v1 packs
without the optional `timings` field remain valid.

## Noisy silent slides and the ASR guard

`make eval-noisy` builds two 30-second videos from the same four generated slides:
one with a speech-free bed of pink noise and a quiet two-tone chord, one with the
same bed plus a synthetic sentence (espeak-ng) placed at 4 s. Both go through
`media.extract_audio`, then through the Silero VAD that faster-whisper runs when
`vad_filter=True`, with faster-whisper's default `VadOptions`. That is what
ClipMind's default `CLIPMIND_ASR_VAD_FILTER=1` requests. When the VAD finds no
speech, faster-whisper decodes a zero-length array and returns no segments.

Recorded run ([data](data/noisy-slides-vad-eval.json), faster-whisper 1.2.1, Linux):

| Case | Level | VAD speech | Decoded with defaults |
| --- | --- | --- | --- |
| Noise bed, no speech | -38.9 dBFS | none | 0.0 s, so the transcript is empty |
| Same bed + sentence at 4 s | -30.4 dBFS | 3.60-9.36 s | 5.76 s |

The `--sweep` part checks both directions:

- False speech: 0 of 24 speech-free beds (pink, white and brown noise, with and
  without tones, up to -14.2 dBFS) produced any VAD speech.
- Missed speech: the whole sentence (about 5.8 s) was kept down to about -9 dB
  SNR, part of it between about -10 and -15 dB, and none below about -16 dB.

This historical report ran VAD only, not Whisper model decoding. The real
three-setting comparison below supplies the missing decoding evidence. The
noise remains stationary and synthetic; music with vocals, keyboard clicks and
crowd noise are not covered.

## Synthetic silent slides on Linux with Tesseract

The original [macOS result](data/silent-slides-eval.json) used Apple Vision.
The [original Linux/Tesseract 5.3.4 failure](data/silent-slides-eval-linux-tesseract.json)
is retained. A fresh Linux arm64/Tesseract 5.5.0 reproduction returned the same
`ALPHA PLAN BETA SYSTEM MMA DATA REVIEW` text
([before](data/silent-slides-eval-linux-tesseract-before-fix.json)).

The generated fixture was low contrast: the decorative panel grew to x=350 and
x=440, underneath black headings starting at x=260. All headings now start at
x=520 on white. This changes the sample layout, not OCR, sampling or preview
algorithms, and does not relax the four-label assertion.

The [repaired run](data/silent-slides-eval-linux-tesseract-fixed.json) recognizes
all four labels, retains four canonical states and leaves no acquisition media.
It still has zero reading previews: a single heading per slide does not satisfy
the reading-layout selection rule. Canonical evidence and OCR are present;
this fixture does not claim to exercise a four-preview reading pack.

The layout regression test inspects rendered pixels. Pairing it with the old
generator fails; the actual old Linux evaluation also exits 1. The repaired
evaluation exits 0. CI now runs the real Linux decoder/Tesseract pipeline on
every PR and uploads its JSON, alongside the separate hermetic unit suite.

## Real Whisper three-setting comparison

The [recorded real decode](data/noisy-slides-whisper-eval.json) uses the default
`large-v3-turbo` weights (snapshot `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`)
on Linux arm64 CPU, CTranslate2 4.8.2, `int8_float32`. The English fixture is
explicitly decoded as English, not the application's default Chinese language.
Every row calls the production `FasterWhisperProvider.transcribe()` and consumes
its segments; it does not reimplement the model call in the evaluator.

| Setting | VAD | Silence threshold | Speech-free noise | Control word coverage |
| --- | --- | --- | --- | --- |
| Guards off | off | none | `Thank you. You` (hallucinated) | 5/8 |
| VAD only | on | none | empty | 7/8 |
| Default guards | on | 2.0 s | empty | 7/8 |

All six calls succeed. VAD legitimately skips neural decoding for audio with no
detected speech; the three speech-control rows run real model inference. Both
guarded results still read `GammaNator` instead of `Gamma Data`. The complete raw
segments, missing expected words and `all_expected_words_present: false` are
preserved. This is a paired speech-retention check, not a zero-error ASR claim.
This sample shows no additional benefit from the silence threshold beyond VAD.

The noise seed is fixed and each setting shares exactly the same extracted WAV.
The report records audio/model hashes, installed decoder source and archive
hash, device, compute type, versions, parameters and timings. Model caching and
sequential execution make the timings unsuitable for a speed comparison.

Acceptance requires all six calls to succeed, default noise transcription to be
empty, all speech controls to remain nonempty and overlap the known speech time,
and guarded expected-word coverage to remain positive and no lower than the
paired guards-off baseline. Coverage counts unique expected words, splitting
observed CamelCase boundaries equally in every row; it is not WER. The original
perfect-word check was replaced with this paired retention criterion because
ordinary recognition mistakes must remain measurements, rather than being
misreported as speech erased by a guard. No audio, seed or expected words were
changed to obtain the result. Errors remain explicit failed rows and a nonzero
exit, never empty successful transcripts.

Real decoding also exposed an existing dependency incompatibility. PyPI
faster-whisper 1.2.1 plus the locked CTranslate2 4.8.2 raises `IndexError` for the
noise/guards-off row ([failed run](data/noisy-slides-whisper-before-fix.json)).
The immutable official [f6ef59b fix](https://github.com/SYSTRAN/faster-whisper/pull/1460)
handles empty word alignments; installation metadata and `uv.lock` now use that
archive. No application ASR algorithm or CTranslate2 version was changed. A
model-free test exercises this boundary in the actual installed dependency;
the old build fails it, the fixed build passes. Frozen-install CI runs it on
platforms with the portable provider installed.

Reproduce on Linux after the documented install (the first decode downloads
free model weights; no paid API is used):

```bash
sudo apt-get install ffmpeg espeak-ng fonts-dejavu-core
make eval-synthetic
make eval-noisy-decode
```

For a cached immutable model directory and a saved JSON:

```bash
HF_HUB_OFFLINE=1 PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/evaluate_noisy_slides.py --decode --sweep --model /path/to/model/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf --output /tmp/whisper-eval.json
```

Generated videos/audio and packs are scoped to temporary directories and removed
on exit. The seeded VAD sweep also covers 24 speech-free beds (none detected as
speech) and 24 mixed controls; nine sufficiently noisy/quiet controls lose all
detected speech. These are VAD measurements, not additional Whisper decodes,
and demonstrate why the result cannot guarantee preservation at arbitrary SNR.

