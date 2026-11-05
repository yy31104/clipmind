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

Limits: the noise is stationary and synthetic. Music with vocals, keyboard
clicks or crowd noise is harder and is not covered. The Whisper model was not run
here (the model host is not reachable from the evaluation environment), so the
transcript with the guards turned off is not measured; `--decode` adds that
comparison where the model can be loaded.

## Synthetic silent slides on Linux with Tesseract

The recorded [silent-slides result](data/silent-slides-eval.json) used Apple
Vision OCR. On Linux with Tesseract 5.3.4 (chi_sim+chi_tra+eng) the same
evaluation fails ([data](data/silent-slides-eval-linux-tesseract.json)): OCR
reads `ALPHA PLAN BETA SYSTEM MMA DATA REVIEW` (GAMMA read as `MMA`, DELTA
missing), and with fewer recognized lines no frame meets the reading-layout rule,
so the preview is empty (0 of 4). The canonical evidence is still complete. This
is OCR coverage on the generated slides, not a bounding-box mismatch: both
providers return top-left normalized boxes.

