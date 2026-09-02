# ClipMind Evidence Pack v1

An Evidence Pack is the canonical output of one successfully ingested video.
It preserves source evidence without deciding what is important, summarising it,
or changing a downstream knowledge base.

## Completion marker

`manifest.json` is written last. Its presence with
`schema.name = "clipmind-evidence-pack"`, `schema.version = "1.0.0"`, and
`status = "complete"` means the pack is reusable. A directory without a valid
completion marker is partial and must not be treated as a cache hit.

## Layout

```text
out/<job-id>/
├── manifest.json
├── source.json
├── job.json
├── transcript.jsonl
├── transcript.md
├── ocr.jsonl
├── visual_timeline.jsonl
├── evidence.md
└── visual_states/
    ├── all/
    └── preview/
```

`metadata.json`, `transcript.json`, `note.md`, and `keyframes/` are migration
compatibility artifacts. They are not part of the v1 contract.

## Record identities

IDs are deterministic within a pack and follow chronological order:

- `transcript-00001`, `transcript-00002`, …
- `ocr-00001`, `ocr-00002`, …
- `visual-00001`, `visual-00002`, …
- `build-00001`, `build-00002`, …

The Nth OCR record always refers to the Nth canonical visual state. Preview
membership never changes canonical IDs.

## Files

### `source.json`

Source identity and provenance: platform, source ID, canonical URL, title,
uploader, duration, and acquisition strategy. It does not contain browser
cookies or credentials.

### `transcript.jsonl`

One complete ASR segment per line:

```json
{"id":"transcript-00001","start":3.42,"end":7.18,"text":"..."}
```

### `ocr.jsonl`

Exactly one OCR record per canonical visual state. An empty `lines` array means
no text was recognised. If OCR itself failed, the record also contains `error`;
these two cases are intentionally distinct.

### `visual_timeline.jsonl`

One record per canonical visual state. `start` is the sample timestamp and
`end` is the next canonical state timestamp (or source duration for the last
state). `transcript_refs` contains every speech segment overlapping that
interval. Build metadata is additive and never removes an `all/` artifact.

### `evidence.md`

A deterministic chronological rendering of every transcript segment and every
canonical visual state. It contains no semantic ranking or generated summary.

## Completeness

`manifest.json.completeness` reports each independent modality:

- `complete`: the stage ran without a recorded failure;
- `partial`: some OCR records failed;
- `unavailable`: ASR/OCR produced no usable stage result because the stage failed;
- `complete_with_warnings`: visual comparison failed open, so evidence was
  retained and the diagnostic is attached.

A pack can be structurally `complete` while a source modality is unavailable.
That means packaging completed and the absence is explicit; it does not claim
the unavailable evidence was extracted.

## Responsibility boundary

ClipMind owns acquisition, timestamped transcription, visual-state capture,
OCR, deterministic alignment, and packaging. A downstream knowledge-base agent
owns interpretation, summarisation, relevance, deduplication, learning tasks,
and long-term retention decisions.
