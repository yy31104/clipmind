# Changelog

## v1.6.0

- Rank information changes separately from retained screenshots and show them in the frames view.
- Improve OCR on dark code panels and light overlays, and keep complete screen states available for search.

## v1.5.0

- Compare each screen state with the last information screen; speaker, camera, pointer and transition states stay saved but are typed separately instead of counting as new information.
- Keep trailing code comments in line order, recover thin small text missed by high-contrast preparation on Mac, and binarize dark code panels inside light slides for Tesseract.
- Move the used v1.4.0 holdout to development with a corrected label copy, freeze a new three-talk holdout before code changes, and publish its single-campaign results and a blank review package.

## v1.4.0

- Retain settled progressive builds with actual screenshot times and distinguish supported speaker motion from information changes.
- Improve code contrast, small-text recognition and reading order; bound native OCR worker health, cancellation and shutdown.
- Detect speech language automatically, prefer English first in multilingual Tesseract defaults, and show an analysis-time range.
- Publish development ablations, a frozen full-talk holdout, CPU Docker measurements, default-tool comparisons and editable annotation review packs.


## v1.3.0

Changes since `v1.2.0`. Downloads are on the [GitHub release](https://github.com/yy31104/clipmind/releases/tag/v1.3.0).
See the [release notes](docs/releases/v1.3.0.md) for evidence and limitations.

### Added

- Adapter-owned source identity, an acquisition engine with per-source success
  memory, structured failure classification and an offline adapter conformance
  kit.
- Persistent acquisition ownership and cleanup retries after interrupted work,
  plus a bounded metadata-probe API that explains local resource-limit refusals
  separately from source unavailability.
- A temporary Chrome browser-session fallback for Douyin when ordinary
  acquisition fails; requested-video identity and cancellation are checked.
- Recoverable multi-select removal of terminal tasks and Library versions,
  reading-oriented results, complete-frame browsing, compact reading exports,
  aligned Library cards and a return-to-top control.
- A real public-talk search demonstration, a recorded AI-agent MCP session,
  and a reproducible ten-talk excerpt evaluation with source-bound annotations.
- Version-pinned GitHub installation, a frozen-lock Docker build and versioned
  GHCR publication workflow for Linux amd64/arm64. Python release wheels,
  source distributions and checksums remain part of the release workflow.

### Fixed

- Bilibili part identity and cache reuse: an unspecified-part result cannot
  satisfy a different explicit-part request; same canonical URL results retain
  valid downloader-resolved IDs.
- Spawned-tool shutdown, repeated cancellation, cleanup ownership boundaries,
  failed-removal index restoration and restart cleanup retries.
- Apple Vision parameter compatibility on current macOS, explicit OCR-failure
  warnings and a guarded tool for refreshing OCR/reading previews in old packs.
- Silence-hallucination guards for local ASR. Real portable-Whisper decoding
  exposed an empty-alignment incompatibility; dependencies pin the immutable
  official upstream fix rather than hiding the failure.
- The low-contrast synthetic Linux OCR fixture and its real Tesseract CI gate;
  the four-label assertion is preserved. The original failed measurements
  remain available.

### Boundaries

- No new social-platform adapter is promoted to verified support. See
  [platform evidence](docs/PLATFORMS.md).
- The ten-talk quality measurements use AI-assisted source annotations awaiting
  human review, and publisher-generated captions rather than human ASR gold
  transcripts. Complete packs do not imply perfect transcription or frame
  coverage.
- Temporary acquisition media are cleaned; saved results, exported ZIPs and
  model caches remain. Library removal moves data to `.trash` and does not
  itself free disk space.
- No PyPI publication, signed/notarized macOS installer or hosted service is
  introduced. The PyPI name `clipmind` belongs to another project.
