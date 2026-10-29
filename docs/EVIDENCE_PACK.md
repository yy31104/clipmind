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

### Compact reading copies

The web UI's primary ZIP action (`?profile=compact`) creates a **derived reading
copy**, not a claim that all sampled images were retained. From an original full
pack, `?profile=full`, SDK export and Inbox delivery preserve its canonical files.
Re-exporting a compact copy preserves only its selected images and omitted OCR
text; it cannot restore the original images. CLI users can explicitly choose
`clipmind export PACK_ID --compact`.

A compact copy keeps the original transcript JSONL/Markdown bytes, including
word timestamps. For videos with speech, positive evidence of independent reading material
(code, documents, charts, tables or instruction lists) earns a screenshot;
pure commentary has no screenshots and no forced representative/cover image.
Empty OCR, content hints, new OCR words and scene changes alone do not earn
a screenshot. `?profile=text` or CLI `--text-only` explicitly exports no images.
Its OCR and visual timeline are rebuilt with valid local references and matching counts.
Image collections and visual-primary videos retain all canonical pages/states
in a compact export; the text-only profile still omits image files.
Preview records point at the same `visual_states/all/` images, so the required
`visual_states/preview/` directory may be empty. `omitted_visual_text.jsonl`
preserves recognized text and source IDs/timestamps for images omitted from
the reading copy; it does not link to absent screenshots. The manifest records
the compact profile, parent manifest digest, selection algorithm, and original
and selected image counts. These derived IDs apply only within that copy.

The original library pack and full ZIP remain available. Heuristic selection
can miss a meaningful visual. Speech/OCR errors are recorded and do not by
themselves cause a photograph to be included. Compactness reduces exported bytes,
not the initial sampling or OCR workload.

`metadata.json` and `transcript.json` are UI/migration views outside the v1
contract. `note.md` and `keyframes/` can appear in older libraries and remain
readable, but current jobs do not write them.

## Stable record identities

IDs are deterministic within a pack and follow chronological or source-image order:

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

Image collections add `media_type=image_collection`, `source_image_count`,
`time_basis=image_order`, and `duration_available=false`. `audio_status` records
`not_acquired` for an image post whose soundtrack was not fetched, or
`not_applicable` for a local still image. A video with no audio track records
`audio_status=absent`. These are source-modality descriptions, not ASR failures.

For a local multipage TIFF, `image_page_transform=tiff-to-png-lossless` records
that the ordered evidence images are decoded PNG pages, not byte-identical TIFF
containers. The source ID remains the SHA-256 of the original container. All
declared pages must decode successfully within the page/pixel/byte limits; no
prefix-only pack is published when a later page fails. The original user file
is not changed. Single-page TIFF and other local still images retain their
original image bytes.

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

Image pages instead carry a zero-based `source_image_index`,
`time_basis=image_order`, and `timestamp_available=false` in both timeline and
OCR records. Their compatibility timestamp fields are zero; consumers must use
image order rather than interpret those values as playback times. The UI labels
these pages **第 1 张**, **第 2 张**, and so on, without associating nearby speech.

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

Static images and Douyin image collections retain every acquired page in source
order, including identical pages. Canonical image files keep the original bytes
and native format; they are not re-encoded as video screenshots.

### `visual_states/preview/`

A derived chronological view optimized for reading. New jobs and preview
rebuilds select positive evidence of code, document layouts, tables, charts or
instruction lists using the algorithm recorded in the manifest. Pure commentary with speech can have
zero preview images. Scene or caption changes, OCR novelty and recognition
errors do not force an image into this view. It has no fixed count limit and
cannot remove or repoint canonical files. Existing packs can retain older scene
selection until explicitly rebuilt; the manifest records the algorithm used.

For image collections and visual-primary videos (no audio track, or successful
ASR with no detected speech), `visual-only-primary-v1` keeps all canonical pages
or sampled states in the preview. Compact image exports retain those images;
the explicit text-only profile omits image files but keeps their OCR and order.

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

For Live Photo image collections, `source_image_count` counts original image
pages and `source_motion_count` counts associated motion clips. A page can have
several visual states: its native still and sampled dynamic states.
`visual_state_index` is unique across the collection; `source_image_index` can
repeat. Optional `source_motion_timestamp` is seconds inside that page's motion
clip. `time_basis=image_order` and `timestamp_available=false` still mean there
is no acquired whole-post playback clock. Search and SDK frame lookup use the
unique visual reference to distinguish states on the same page.

`motion_sample_fps` records the sampling rate, and
`motion_time_basis=clip_relative_seconds` identifies the local motion clock.
`motion_audio_status=not_acquired` is explicit: motion screenshots and OCR do
not imply an audio transcript. Temporary motion files are cleaned after the
canonical stills and motion screenshots have been written.

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

`diagnostics.audio_status` and `diagnostics.speech_status` distinguish absent or
unfetched audio from a real ASR error. Images and no-audio videos have an
unavailable transcript without a fabricated `asr_error`. Successful ASR that
finds no speech records `speech_status=not_detected`; an actual model/runtime
failure retains its error and `speech_status=unavailable`. No OCR text is
presented as spoken words or word timings.

## Responsibility boundary

ClipMind owns acquisition, transcription, visual-state capture, OCR, timing,
alignment, diagnostics, and packaging. A downstream human or agent owns
interpretation, summarization, relevance, comparison, deduplication, learning
tasks, and long-term retention decisions.

## Export and delivery

The web reader uses canonical `transcript.jsonl` when it is present. Exported
full packs do not need the legacy `transcript.json` compatibility file to show
speech after moving to another library. A present but unreadable canonical
transcript is reported as an error rather than replaced with a stale copy.

Search includes a compact pack's `omitted_visual_text.jsonl` only when its
manifest declares the `compact-reading-v1` profile. Such hits carry
`visual_available=false`, `omitted_visual=true`, `ref=null`, and `timestamp=null`.
`source_visual_ref` and optional `source_image_index` describe original provenance;
they are not frame IDs in the derived pack. The reader can show the complete
retained OCR text without pretending the original screenshot was attached.

`clipmind export`, the SDK, MCP, and `GET /api/jobs/<job-id>/evidence.zip`
produce a deterministic ZIP of canonical contract files. ZIP metadata uses
fixed timestamps so identical pack bytes produce identical archives.

When `CLIPMIND_KB_INBOX` is configured, one-way delivery copies the same
canonical files to `<Inbox>/<job-id>/` through a private temporary directory and
publishes its manifest last. ClipMind never reads or modifies the rest of the
knowledge base.
