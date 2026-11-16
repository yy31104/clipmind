# Changelog

## v1.3.0

Prepared on 2026-11-16. Changes since `v1.2.0`; publication and downloadable
artifacts are recorded on the [GitHub release](https://github.com/yy31104/clipmind/releases/tag/v1.3.0).
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
