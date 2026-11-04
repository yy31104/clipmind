# Roadmap

ClipMind's product direction is:

> Local-first multimodal video ingestion for humans and AI agents.

The roadmap is organized by enduring product outcomes rather than a promise
that every idea will land in a particular version. A proposed feature belongs
only if it makes ingestion more universal, extraction more faithful, or evidence
easier for humans/agents to use.

## Shipped in the current development line

- versioned, manifest-last Evidence Packs with additive v1 compatibility;
- durable bounded jobs, restart recovery, explicit interrupted state, and
  exception-safe temporary cleanup;
- uncapped canonical visual states plus a separately derived preview;
- progressive builds, scene/stability/scroll measurements, transcript novelty,
  OCR layout, word timing, and optional speaker diarization;
- SourceAdapter boundaries with verified YouTube, Douyin, and local-file
  built-ins plus installed plugins;
- complete-or-refuse preflight with explicit full-processing override;
- Apple and portable ASR/OCR provider boundaries;
- browser uploads, Inbox/Library/detail UI, local full-text evidence search;
- CLI, Python SDK, canonical REST read API, and stdio MCP server;
- wheel, Docker, cross-platform CI, and local macOS app/DMG build paths.

“Shipped” here means implemented and covered in the repository. It does not mean
that an unsigned artifact is a public installer or that every upstream platform
URL has an access guarantee.

## Near-term product hardening

- publish signed/notarized macOS artifacts and verify install/update/uninstall
  from the downloaded release;
- publish a reproducible Python package and container release provenance;
- first-run model/dependency UX beyond the existing `doctor` command;
- retry/backoff and visible failure state when a browser snapshot refresh fails;
- cancellation, library deletion with recovery/confirmation, and pagination;
- broader synthetic and permission-safe real-world evaluation across languages,
  long videos, silent documents, whiteboards, multi-speaker sources, and every
  advertised input;
- end-to-end tests for independently packaged source plugins;
- accessibility and localization review of the web application.

## Extraction quality

- better transition rejection without losing short stable evidence;
- richer code editor, browser, slide, document, and whiteboard state modeling;
- layout-aware OCR reading order across portable providers;
- scroll stitching as a derived artifact while preserving every canonical state;
- provider quality measurements by language and hardware;
- diarization confidence and overlap representation without identity claims;
- corpus-driven adaptive sampling only after a stable-state miss is demonstrated.

The invariant does not change: canonical evidence may be redundant, but it must
not silently discard information to make the preview look cleaner.

## Ecosystem

- independent source/provider plugin packages and contributor-owned adapters;
- client examples for major MCP-capable agents and editors;
- versioned SDK/API documentation and compatibility fixtures;
- optional downstream integrations that consume Evidence Packs without changing
  the core extraction contract;
- project governance that recognizes maintainers of adapters, providers, docs,
  packaging, and evaluation corpora.

## Explicit non-goals

- a social network, hosted account system, or enterprise analytics dashboard;
- DRM circumvention or bypass of source access controls;
- silently partial “summaries” that look complete;
- a built-in agent that decides what a user's knowledge base should keep;
- paid cloud inference as a requirement for the canonical path;
- feature work unrelated to media ingestion, evidence fidelity, or evidence use.

## How priorities are chosen

Issues are evaluated on:

1. user impact and reproducibility;
2. risk of silent evidence loss or privacy/security failure;
3. whether the change strengthens a stable extension boundary;
4. measured quality or installation improvement;
5. maintenance cost across platforms and schema versions.

Benchmarks and fixtures should accompany quality claims. Roadmap entries are not
deadlines; opened pull requests still need focused scope and green contracts.
