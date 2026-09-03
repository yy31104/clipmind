# Limitations

ClipMind is conservative about what it claims. A recognized adapter, a complete
package transaction, and a faithful copy of source evidence are different
things.

## Source access

- **Adapters are not access guarantees.** YouTube, Douyin, and local files are
  the verified built-ins. Private, removed, region-locked, DRM-protected,
  account-gated, or upstream-changed media may still fail in yt-dlp. The generic
  URL fallback and third-party adapters are extension paths, not verified-source
  claims.
- **Cookie sessions expire.** A fresh signed-in browser session or narrowly
  exported cookie file can be required. ClipMind does not bypass platform
  access controls.
- **Playlists are intentionally not expanded.** One submitted source becomes
  one job; batch work is an explicit list of links/files.
- **Plugin quality varies.** Third-party adapters are loaded in isolation, but
  their matching and normalization semantics are maintained by their authors.

## Extraction fidelity

- **Sampling is not continuous recording.** Production uses uniform 2 fps
  change detection. A checked-in 4 fps probe covered all 105 states stable for
  at least 0.5 seconds in one long code/UI source; sub-0.5-second flashes can be
  missed and are not assumed readable.
- **Visual grouping is heuristic.** dHash, OCR overlap, scene scores, build
  groups, scroll groups, and content hints are measurements rather than general
  visual understanding. Canonical output can retain transitions; preview can
  omit a preferred representative while the canonical frame remains available.
- **OCR is not ground truth.** Small fonts, stylized captions, motion blur,
  compression, unusual layouts, and missing Tesseract language packs can cause
  omissions or substitutions. The measured 1280 px choice improved one code/UI
  source but does not guarantee correctness on every source.
- **ASR is not ground truth.** Accents, music, crosstalk, specialized terms, and
  unsupported languages reduce accuracy. ClipMind does not semantically rewrite
  speech to appear more confident.
- **Word timing depends on provider output.** It is absent when the selected ASR
  provider does not return usable word timestamps.
- **Speaker diarization is optional and fallible.** It is off by default,
  requires a separately installed model/provider and token, and cannot identify
  real people—only anonymous speaker turns.
- **Transcript novelty is not importance.** Silent footage, code identifiers,
  and ASR errors all make OCR look novel. Consumers should treat it as evidence
  availability, not semantic ranking.

## Resource use

- **Complete means potentially large.** `visual_states/all/` has no fixed cap.
  Long, visually dense media can require substantial OCR time and disk space.
- **Preflight estimates rather than truncates.** Limits refuse before expensive
  work. Explicit force processes the complete source; there is no automatic
  clipping, frame cap, or resolution reduction.
- **First use can download large models.** MLX Whisper, faster-whisper, and
  optional diarization models use their upstream local caches.
- **GPU acceleration is platform-dependent.** Apple Silicon uses MLX by default.
  Docker and portable providers work without that path but performance varies by
  CPU/GPU/runtime configuration.
- **No automatic eviction.** Temporary media is cleaned, but final Evidence
  Packs remain until the user removes them outside ClipMind.

## Product and library behavior

- **No authentication.** The web server is designed for one local user and binds
  to loopback by default. It is not a hosted multi-tenant service.
- **No per-job cancellation yet.** A running job can be interrupted by stopping
  the app; restart marks it `interrupted` and never silently resumes it.
- **No built-in delete flow yet.** Evidence Packs can be removed from the local
  filesystem, but the UI intentionally does not offer a destructive action.
- **Large-library pagination is not implemented.** The derived SQLite index
  keeps search bounded, but listing a very large library still scans complete
  pack directories.
- **Search is lexical.** The local index covers titles, transcript, and OCR; it is
  not an embedding or semantic search engine.
- **Cache identity has limits.** Exact normalized URLs and known source IDs are
  reused. Different unresolved short URLs for the same media may require one
  acquisition before their shared identity is known.
- **Legacy reads remain.** Old `note.md`/`keyframes/` packs are readable, but new
  jobs do not recreate those outputs.

## Packaging

- **Source install is the documented release path today.** Packaging code can
  build wheels, a Docker image, and a macOS app/DMG, but the repository does not
  claim an already published PyPI package, signed/notarized DMG, Homebrew cask,
  or platform installer.
- **Windows and Linux depend on system tools.** FFmpeg and Tesseract must be
  installed and visible on `PATH`; desired Tesseract language data must also be
  present.

## Evaluation scope

The checked-in real-video suite contains three public Douyin sources covering
code/UI, scrolling, talking-head captions, inserted documents, and progressive
slides, plus a generated silent-slide fixture. It measures pipeline behavior,
not universal quality. More languages, platforms, multi-speaker conversations,
whiteboards, very long sources, vertical live streams, and low-quality archival
footage remain important corpus gaps.
