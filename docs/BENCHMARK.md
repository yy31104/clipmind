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

The same command also creates a temporary silent four-slide video and runs the
real local visual pipeline. Its recorded result is
[`silent-slides-eval.json`](silent-slides-eval.json); no generated media is kept.

The checked-in real-video report currently records 3/3 structurally complete
packs, no cleanup leftovers, and source-duration/processing-time ratios from
4.84× to 6.36×. The detailed extraction metrics are in
[`eval-results.json`](eval-results.json) and the methodology is in
[`REAL_WORLD_EVAL.md`](REAL_WORLD_EVAL.md).

The OCR resolution experiment is deliberately separate. On the 227-second
code/UI source, 1280 px OCR recognized 1034 unique characters versus 559 at
640 px. OCR wall time increased from 24.7 seconds to 36.6 seconds. See
[`ocr-resolution-experiment.json`](ocr-resolution-experiment.json).

The scheduler benchmark uses eight 50 ms simulated jobs so it measures the
queue itself rather than network, model cache, or video content. The recorded
run reached four active jobs, left four queued at capacity, respected the limit,
and produced the speedup recorded in [`benchmark.json`](benchmark.json). Exact
wall times vary by machine; the asserted properties are bounded peak concurrency
and observable backpressure.

Newly generated manifests include per-stage wall time for acquisition, sampling,
ASR, OCR, and preview derivation. Job state adds end-to-end time. Older v1 packs
without the optional `timings` field remain valid.
