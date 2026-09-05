# Changelog

Notable user-visible changes are recorded here. Evidence Pack schema versions
are documented separately and do not have to equal the application version.

## Unreleased

### Changed

- acquisition writes into a job-owned `acquisition/` directory whose ownership
  is recorded on disk before downloading, so completion, failure, cancellation
  and restart recovery share one cleanup contract and temporary media is removed
  whatever the acquiring strategy named it. With `CLIPMIND_KEEP_VIDEO=1` the
  retained media now lives in `acquisition/` instead of at the job root.

## 1.2.0 — 2026-09-04

### Added

- verified source adapters for YouTube, Douyin, and local files, plus the
  installed `clipmind.sources` plugin boundary;
- complete-or-refuse visual cost preflight with an explicit full-processing
  override;
- portable faster-whisper and Tesseract providers, word timing, OCR layout,
  stability/scene/scroll metadata, and optional pyannote diarization;
- browser uploads, local evidence search, and productized Inbox/Library/detail
  views;
- Python SDK, canonical REST resources, full CLI, and stdio MCP tools/resources;
- Python packaging, cross-platform CI, Docker, desktop launcher, and local macOS
  app/DMG build path;
- public installation, architecture, MCP, source-plugin, privacy, security, and
  contribution documentation;
- separate existing-pack audits from fresh real-source re-extraction, with exact
  canonical-count checks gated by matching source SHA-256.

### Changed

- runtime settings and media providers are injected rather than fixed at import;
- preview serialization has one canonical view builder;
- Evidence Pack writer emits additive schema `1.3.0` while accepting all v1
  minor versions;
- the SQLite search index is explicitly derived from complete packs and keeps
  only the newest source version in global results.
- ZIP and Inbox delivery sanitize machine-local retry paths from `job.json`.

### Removed

- duplicate legacy keyframe/note generation and its obsolete configuration and
  summarization dependencies. Older packs remain readable.

## 1.1.0

- measured transcript novelty for every canonical visual state;
- added a preview safety net for unspoken document/code text without changing
  canonical membership.

## 1.0.1

- kept slow SSE subscribers attached through bounded resync rather than silently
  dropping them;
- prioritized complete Evidence Packs in the library UI and collapsed failures.

## 1.0.0

- published the local-first Douyin extraction baseline;
- added durable recovery, uncapped canonical visual states, derived previews,
  readable evidence resolution, deterministic Evidence Packs, knowledge-base
  handoff, evaluation, and runtime safety.
