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

`metadata.json` and `transcript.json` are compatibility artifacts for the
current UI and are not part of the v1 contract. `note.md` and `keyframes/`
appear only in packs written before the Evidence Pack became canonical; they
are still readable, but nothing writes them any more.

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


## Transcript alignment (schema 1.1.0)

Every `visual_timeline.jsonl` record carries three measurements describing how
much of the frame's on-screen text the nearby speech does **not** already
carry:

| field | meaning |
| --- | --- |
| `ocr_char_count` | characters across recognised tokens, excluding whitespace and punctuation |
| `transcript_novelty_char_count` | characters in tokens the nearby speech does not cover |
| `transcript_overlap_ratio` | `1 - novelty / total`, `0.0` when the frame has no text |

Comparison is a multiset over tokens — one token per CJK ideograph, one per
latin word — after NFKC normalisation and case folding, against speech within
a small window around the state's own interval. A term shown three times but
spoken once is not treated as fully covered.

**This measures information novelty, not importance.** Burned-in captions
repeat what is being said, so the transcript already holds that information and
the ratio approaches `1.0`. A slide, document or code pane shows text nobody
reads aloud, so the ratio approaches `0.0` and the frame is the only place that
information exists. But a silent video makes every frame novel, code
identifiers are novel because nobody says them out loud, and an ASR mistake
manufactures novelty. Consumers decide what that is worth.

The measurement never affects canonical retention. `visual_states/all/` is
chosen before alignment runs, so an ASR or OCR slip can never delete evidence.
It does give preview derivation one safety net: a state carrying enough
unspoken text earns its own preview slot even when the whole video reads as a
single visual scene, unless an already-selected state shows substantially the
same terms.

Packs written under schema `1.0.0` do not carry these fields and remain
readable. Running `scripts/rebuild_preview.py` on such a pack adds them and
upgrades its manifest.

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

Newly created packs also include an optional `timings` object with wall-clock
seconds for acquisition, sampling, ASR, OCR, and preview derivation. The field
is additive: v1 packs created before timing instrumentation remain valid.

## Responsibility boundary

ClipMind owns acquisition, timestamped transcription, visual-state capture,
OCR, deterministic alignment, and packaging. A downstream knowledge-base agent
owns interpretation, summarisation, relevance, deduplication, learning tasks,
and long-term retention decisions.

## Delivery

`GET /api/jobs/<job-id>/evidence.zip` exports only the files in this contract;
legacy notes and temporary media are excluded. ZIP metadata uses fixed timestamps,
so identical pack bytes produce identical archives.

When `CLIPMIND_KB_INBOX` is configured, the UI can copy the same canonical files
to `<Inbox>/<job-id>/`. Copying occurs through a private temporary directory and
publishes `manifest.json` last. Delivery is one-way: ClipMind never reads or
modifies the downstream knowledge base beyond that Inbox directory.
