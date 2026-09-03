# Architecture

ClipMind is a local-first multimodal ingestion engine for humans and AI agents.
Its canonical output is a deterministic Evidence Pack, not an AI opinion about
what matters.

```mermaid
flowchart LR
    A[URL / share text / local file] --> B[Source registry]
    B --> C[SourceAdapter]
    C --> D[Durable bounded job queue]
    D --> E[MediaAsset acquisition]
    E --> F[Cheap candidate sampling]
    F --> G[dHash fail-open dedupe]
    G --> H[Complete-pack preflight]
    H -->|within budget or explicit force| I[Readable canonical states]
    E --> J[Transcript provider]
    I --> K[OCR provider]
    J --> L[Optional diarization]
    K --> M[Scenes / builds / scrolls / preview]
    L --> N[Deterministic timeline]
    M --> N
    N --> O[Evidence Pack; manifest last]
    O --> P[Web / CLI / REST / SDK / MCP]
    O --> Q[ZIP / knowledge-base Inbox]
```

## Stable boundaries

### Source adapters

`clipmind.sources` owns recognition and metadata normalization. Acquisition does
not branch on platform names: URL sources flow through one yt-dlp path, while
the local-file adapter copies the source before processing. Specific adapters
run before installed plugins, and plugins run before the generic URL fallback.

The source boundary returns a platform-neutral `MediaAsset` containing a local
media path plus normalized metadata. Third-party packages can register an
adapter with the `clipmind.sources` entry-point group. One broken plugin is
logged and skipped; it cannot disable built-ins.

An adapter is deliberately small. It does not duplicate fetching, cookies,
retry policy, preprocessing, or Evidence Pack serialization. See
[Source adapters](SOURCE_ADAPTERS.md).

### ASR, OCR, and diarization providers

The pipeline depends on three protocols:

- `TranscriptProvider` returns normalized segments and optional word timing;
- `TextRecognizer` returns text and, when available, normalized layout boxes;
- `SpeakerDiarizer` optionally adds speaker labels to existing segments.

Provider selection is runtime configuration, not import-time platform logic in
the pipeline. Apple Silicon defaults to MLX Whisper and Apple Vision. Other
platforms default to faster-whisper and Tesseract. Diarization is off by default;
the optional pyannote provider never blocks the core path or invents labels when
unavailable.

### Evidence Pack

The writer is the only canonical serialization boundary. The current writer
emits schema `1.3.0`; the reader accepts every additive v1 minor back to `1.0.0`.
`manifest.json` is written last and is the sole completion marker.

New v1 fields must be additive and optional for older packs. A change that
invalidates old semantics or reinterprets existing fields requires a new major
schema. Derived caches, UI view models, and compatibility files are not sources
of truth.

### Product surfaces

All product surfaces use the same library and extraction core:

- the web app uses FastAPI plus server-sent progress events;
- the CLI reads and schedules through `JobStore` and `PackLibrary`;
- the Python SDK exposes complete read-only packs and high-level extraction;
- the REST API exposes canonical pack resources;
- the dependency-free stdio MCP server delegates to the SDK;
- the desktop launcher starts the same loopback web application.

No interface has a separate visual-selection or Evidence Pack implementation.

## Pipeline contracts

1. Source parsing accepts unedited share text, URLs, or an explicit local path.
2. `queued` is durably persisted before scheduling.
3. `running` is durably persisted before acquisition or any other external side
   effect.
4. Acquisition materializes a temporary local `MediaAsset`; local originals are
   copied and never modified.
5. Speech and visual work overlap because they use separate resource pools.
6. Cheap samples drive change detection. Every retained index is promoted to a
   readable canonical frame for storage and OCR.
7. Preflight runs before ASR, OCR, and readable-frame promotion. It either
   accepts the complete job or refuses with an estimate. Explicit `force`
   processes the same complete pipeline; it does not truncate or downscale.
8. Timeline fusion joins normalized modalities through stable record IDs and
   requires no LLM.
9. The writer publishes all canonical artifacts and writes `manifest.json` last.
10. Cleanup removes temporary acquisition/sampling data without touching final
    evidence.

## Visual-state semantics

`visual_states/all/` and `visual_states/preview/` have different contracts.

- `all/` is canonical and has no count or duration cap. dHash compares against
  the last retained frame. If hashing or comparison fails, the affected frame is
  retained with a safe diagnostic and comparison continuity is deliberately
  broken, so the next frame is retained too.
- duplicate runs record observed sample count and stable duration without
  keeping redundant files.
- scene boundaries, content hints, progressive builds, and scroll groups are
  measurements. None can remove a canonical state.
- OCR layout uses normalized coordinates when the provider supplies them.
- transcript novelty measures on-screen text not present in nearby speech. It is
  not an importance score and never controls canonical membership.
- `preview/` is derived. It chooses stable/readable representatives, removes
  intermediate progressive builds, and has a novelty safety net for documents
  or code that would otherwise disappear inside a single visual scene.
- preview can be rebuilt from a complete pack without reacquiring media or
  rerunning OCR. Rebuild removes the completion marker during its atomic swap
  and republishes it only after every derived view agrees.

## Failure behavior

| Failure | Result |
| --- | --- |
| unsupported/private/removed/account-gated URL | terminal `error` with a safe action; no raw credentials in the UI |
| estimated complete pack exceeds a configured budget | refuse before expensive stages and persist the estimate |
| user explicitly chooses `force` | run the full untruncated pipeline |
| ASR unavailable or fails | visual evidence can complete; transcript is explicitly `unavailable` |
| OCR fails for some frames | images remain; records identify failures and completeness is `partial` |
| dHash/comparison fails | evidence fails open: retain, diagnose, and break comparison continuity |
| optional diarization fails | transcript remains; the optional error is recorded without invented speakers |
| compatibility metadata write fails | canonical pack remains valid |
| process stops while `running` | restart marks it `interrupted`, cleans only temporary data, and never auto-replays |
| process stops while `queued` | restart schedules it exactly once |
| subscriber cannot keep up with SSE | bounded queue requests snapshot resync without blocking other subscribers |

## Concurrency and backpressure

The outer job semaphore limits active videos. Independent pools bound fetch,
ASR, and OCR work so a slow network job cannot consume GPU capacity and a slow
subscriber cannot block state transitions. Submitting beyond capacity leaves
durable jobs in `queued`; it does not create unbounded active tasks.

SSE is a notification channel, not the truth source. On initial connection or a
resync message, the UI fetches `/api/jobs`; terminal state remains recoverable
from the durable snapshot.

## State, identity, and indexing

- The containing `<library>/<job-id>/` directory owns job identity. Data inside
  `job.json` cannot redirect later reads or writes.
- Every state mutation is write-through. Memory is the live cache; disk rebuilds
  it once on startup.
- Repeated normalized sources reuse the newest complete pack. Reprocess is an
  explicit new job. Terminal jobs do not transition back into work.
- A local file's content hash becomes its source ID. Platform URLs use normalized
  source metadata when available.
- `.evidence-index.sqlite3` is a derived full-text cache. It indexes only
  complete packs, can be rebuilt from files, and returns only the newest pack for
  the same source identity.

## Filesystem and trust boundaries

ClipMind writes only its library, temporary upload directory inside that
library, optional configured knowledge-base Inbox, and an explicitly requested
ZIP destination. Inbox delivery is one-way and manifest-last. The downstream
agent owns interpretation, summarization, relevance, deduplication, and long-term
retention.

The local web server has no authentication and binds to `127.0.0.1` by default.
MCP is stdio. Neither should be exposed as a multi-user public service without a
separate authentication and isolation layer.

## Packaging and portability

The Python wheel includes the web assets and selects a writable per-user library
outside site-packages. CI runs the offline contract suite on macOS, Linux, and
Windows and separately builds the package. Docker provides the portable
faster-whisper/Tesseract path. The macOS build script can create an app bundle and
DMG, but an artifact is not a public release until it is signed and notarized.
