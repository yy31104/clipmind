# ClipMind Evidence Pack v1

An Evidence Pack is the canonical output of one successfully ingested media
source. It preserves provenance, speech, on-screen text, visual states, timing,
and extraction diagnostics without deciding what is important or generating a
semantic summary.

The machine-readable manifest schema lives at
[`schemas/evidence-pack-v1.schema.json`](../schemas/evidence-pack-v1.schema.json).

## Completion and compatibility

`manifest.json` is written last. A reusable pack must have:

```json
{
  "schema": {
    "name": "clipmind-evidence-pack",
    "version": "1.3.0"
  },
  "status": "complete"
}
```

The current writer emits `1.3.0`. Readers accept `1.0.0`, `1.1.0`, `1.2.0`, and
`1.3.0`; every v1 minor has been additive. Missing newer fields mean “not
recorded by that writer,” not zero and not an inferred value. Unknown versions,
invalid manifests, or missing required artifacts are rejected.

A partial directory is never a cache hit, library item, search result, SDK pack,
or MCP resource. Structural completion does not imply that every source
modality was available; modality completeness is explicit in the manifest.

## Layout

```text
<library>/<pack-id>/
├── manifest.json
├── source.json
├── job.json
├── preflight.json
├── transcript.jsonl
├── transcript.md
├── ocr.jsonl
├── visual_timeline.jsonl
├── evidence.md
└── visual_states/
    ├── all/
    └── preview/
```

The canonical ZIP includes the artifacts named by the contract and the
manifest. `preflight.json` and compatibility views can remain in the local job
directory without becoming canonical exported evidence.

`metadata.json` and `transcript.json` are UI/migration views outside the v1
contract. `note.md` and `keyframes/` can appear in older libraries and remain
readable, but current jobs do not write them.

## Stable record identities

IDs are deterministic within a pack and follow chronological order:

- `transcript-00001`, `transcript-00002`, …
- `ocr-00001`, `ocr-00002`, …
- `visual-00001`, `visual-00002`, …
- `build-00001`, `build-00002`, …
- `scroll-00001`, `scroll-00002`, …
- `scene-00001`, `scene-00002`, …

The Nth OCR record refers to the Nth canonical visual state. Preview membership,
scene grouping, and rebuilds never change canonical IDs or file targets.

## Files

### `source.json`

Normalized provenance:

- platform and source adapter;
- source ID and canonical/source URL;
- title, uploader, and duration when upstream supplies them;
- acquisition strategy, without cookies or credentials.

Local files use a content digest for identity and a `local:///filename` source
URL that does not reveal the original absolute directory. The original is copied
before processing and is never modified.

### `job.json`

Durable local lifecycle state and a result view used for restart recovery and
the UI. The containing directory, not an ID inside this file, owns pack identity.
Consumers interested only in canonical evidence should start from
`manifest.json` and the named artifacts. The library copy can contain the
original local path required for an explicit retry. ZIP/Inbox delivery replaces
that path with the portable `source.json` URL and removes internal job options.

### `preflight.json`

The estimate generated after cheap visual sampling and before expensive ASR,
OCR, and readable-frame promotion. It includes candidate/canonical counts,
estimated OCR time and pack size, configured limits, exceeded dimensions, and
whether the user explicitly forced the complete run. Its policy is always
`complete_or_refuse`.

### `transcript.jsonl`

One normalized ASR segment per line:

```json
{"id":"transcript-00001","start":3.42,"end":7.18,"text":"..."}
```

Schema 1.3 can add `words`, each with start/end/text and optional probability,
and `speaker` when a configured diarizer actually supplied a label. Their
absence is honest unavailability; ClipMind never fabricates them.

### `transcript.md`

A deterministic human-readable rendering of the same segments. It identifies
an unavailable transcript instead of silently presenting an empty success.

### `ocr.jsonl`

Exactly one OCR record per canonical visual state. An empty `lines` array means
the recognizer found no text. An `error` field means OCR failed for that frame;
these cases are intentionally distinct.

Providers that expose geometry add a `layout` array with normalized bounding
boxes. Coordinates are provider-normalized rather than raw OS pixel units, so
consumers do not need an Apple Vision-specific data model.

### `visual_timeline.jsonl`

One record per canonical visual state. `start` is the sample timestamp; `end` is
the next canonical state's timestamp or source duration for the last state.
Every record points to its canonical image and OCR record, lists overlapping
transcript IDs, and may point to a derived preview image.

Additive measurements include:

- observed duplicate sample count and stable duration;
- content hint (`visual`, text-rich document/code/UI categories, and related
  heuristic labels);
- scene ID, boundary flag, change score, and boundary reason;
- progressive-build group, position, and size;
- scroll group, position, and size;
- dHash/comparison warning;
- OCR character count, transcript novelty count, and overlap ratio.

All of these describe a retained state. None controls canonical membership.

### `evidence.md`

A deterministic chronological rendering of every transcript segment and every
canonical visual state. It contains source provenance, images, OCR, timing, and
available labels, but no semantic ranking or generated summary.

### `visual_states/all/`

The canonical visual evidence set. It has no per-video frame budget. Membership
is decided by fail-open near-duplicate filtering before OCR, transcript novelty,
scene grouping, or preview logic. If a comparison cannot be trusted, evidence is
kept and the warning is serialized.

### `visual_states/preview/`

A derived chronological view optimized for browsing. It prefers stable/readable
states, collapses intermediate progressive builds, uses adaptive scene grouping,
and guarantees a slot for sufficiently novel unspoken text unless an equivalent
state is already represented. It has no fixed count limit and cannot remove or
repoint canonical files.

## Transcript alignment (introduced in 1.1)

Every timeline record can carry:

| Field | Meaning |
| --- | --- |
| `ocr_char_count` | recognized characters excluding whitespace/punctuation |
| `transcript_novelty_char_count` | characters in OCR tokens not covered by nearby speech |
| `transcript_overlap_ratio` | `1 - novelty / total`, or `0.0` for no OCR text |

Comparison uses a normalized multiset: individual CJK ideographs and Latin
words are matched against speech around the visual interval. Burned-in captions
usually overlap heavily; slide, document, or code text nobody reads aloud does
not. This is an extraction signal, not an importance score. Silent videos, ASR
mistakes, and identifiers can all raise novelty.

## Structural measurements (introduced in 1.2 and 1.3)

Later additive v1 fields expose why a preview representative was useful without
pretending to perform semantic understanding:

- duplicate-run stability says how long a nearly unchanged state was observed;
- scene boundaries quantify visual changes;
- progressive builds model monotonic text addition;
- scroll groups connect overlapping text windows;
- normalized OCR layout preserves reading geometry where supported;
- word timing and optional speakers make transcript evidence more addressable.

Older v1 packs remain valid and these fields are not synthesized during reads.

## Completeness

`manifest.json.completeness` reports independent modalities:

- transcript: `complete` or `unavailable`;
- OCR: `complete`, `partial`, or `unavailable`;
- visual states: `complete` or `complete_with_warnings`;
- word timing: `complete` or `unavailable` when recorded;
- speaker diarization: `complete` or `unavailable` when recorded.

The manifest can also include wall-clock timings, counts, configuration needed
to interpret extraction, and safe diagnostics. “Complete pack” means the package
transaction completed and all absences are explicit—not that an unavailable
modality was somehow extracted.

## Responsibility boundary

ClipMind owns acquisition, transcription, visual-state capture, OCR, timing,
alignment, diagnostics, and packaging. A downstream human or agent owns
interpretation, summarization, relevance, comparison, deduplication, learning
tasks, and long-term retention decisions.

## Export and delivery

`clipmind export`, the SDK, MCP, and `GET /api/jobs/<job-id>/evidence.zip`
produce a deterministic ZIP of canonical contract files. ZIP metadata uses
fixed timestamps so identical pack bytes produce identical archives.

When `CLIPMIND_KB_INBOX` is configured, one-way delivery copies the same
canonical files to `<Inbox>/<job-id>/` through a private temporary directory and
publishes its manifest last. ClipMind never reads or modifies the rest of the
knowledge base.
