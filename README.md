# ClipMind

Local-first Douyin evidence extraction for macOS. Paste one or more share links;
ClipMind downloads the media through your local session, transcribes speech,
captures content-driven visual states, runs OCR, aligns everything on a timeline,
and emits a versioned Evidence Pack for a human or downstream knowledge agent.

```text
share text → bounded acquisition ─┬→ MLX Whisper transcript ─┐
                                  └→ visual states + OCR ─────┤
                                                              ├→ timeline
                                                              └→ Evidence Pack
```

The canonical path is free and requires no API key. It does not decide what is
important or what belongs in your knowledge base; it preserves the evidence so
another agent can make that decision with your existing context.

![ClipMind paste-to-Evidence-Pack demo](docs/demo.gif)

The demo uses a generated silent four-slide fixture; it contains no downloaded
video, third-party imagery, creator identity, or account information.

## What v1 does

- extracts multiple Douyin links from unedited share text;
- uses yt-dlp with the local Chrome session—no manual MP4 download or extension;
- runs MLX Whisper and macOS Vision OCR locally;
- retains every deduped canonical visual state without a per-video cap;
- stores readable 1280 px evidence while using 640 px frames for cheap detection;
- derives an uncapped, content-driven preview and labels progressive builds;
- writes deterministic transcript, OCR, visual timeline, Markdown, and manifest;
- survives restarts without silently replaying interrupted work;
- reuses completed packs for repeated URLs, with an explicit Reprocess action;
- exports a deterministic ZIP or copies one-way to a configured knowledge-base Inbox.

## Requirements

- Apple Silicon Mac running a current macOS release;
- Python 3.12;
- Chrome for videos that require a local Douyin session;
- about 1.6 GB for the first Whisper model download, plus space for Evidence Packs.

Install the system tools and Python environment:

```bash
brew install ffmpeg yt-dlp uv
uv venv --python 3.12
uv pip install -r clipmind/requirements.txt
```

The first Chrome-cookie read can trigger a macOS Keychain prompt. ClipMind does
not ask for your Douyin password. See [Privacy](docs/PRIVACY.md) before use—the
browser-cookie boundary is broader than only `douyin.com` unless you configure a
dedicated cookie file.

## Run

```bash
make run
```

Open [http://127.0.0.1:8420](http://127.0.0.1:8420), paste the complete share
text, and choose **提取**. Multiple links in one paste are queued together.
Overflow stays queued when the configured video limit is reached.

The CLI uses the same durable queue and cache:

```bash
.venv/bin/python cli.py "分享文字 https://v.douyin.com/xxxx/"
.venv/bin/python cli.py --reprocess "https://v.douyin.com/xxxx/"
```

If Douyin rejects a link, open it in Chrome, refresh the page, copy a fresh share
link, and retry. v1 reports expired/private links, login requirements, rejected
cookies, and media failures as separate, actionable errors; raw yt-dlp output is
kept out of the UI.

## Evidence Pack

Every successful job writes `manifest.json` last:

```text
out/<job-id>/
├── manifest.json                 # clipmind-evidence-pack@1.0.0
├── source.json
├── job.json                      # durable lifecycle + result
├── transcript.jsonl
├── transcript.md
├── ocr.jsonl
├── visual_timeline.jsonl
├── evidence.md                   # chronological evidence, no semantic ranking
└── visual_states/
    ├── all/                      # canonical, content-driven, no fixed cap
    └── preview/                  # compact derived view, no fixed cap
```

`metadata.json` and `transcript.json` remain as compatibility artifacts and are
excluded from the canonical ZIP. `note.md` and `keyframes/` survive only in
packs written before the Evidence Pack became canonical.
Source video, audio, and sampling frames are deleted after processing unless
`CLIPMIND_KEEP_VIDEO=1` is set.

The complete schema and responsibility boundary are documented in
[Evidence Pack v1](docs/EVIDENCE_PACK.md). `manifest.json` is the completion
marker; a partial directory is never a cache hit.

## Knowledge-base handoff

Download the ZIP directly, or set an absolute local Inbox path:

```dotenv
CLIPMIND_KB_INBOX=/absolute/path/to/your-knowledge-base/Inbox
```

The UI then exposes **发送到知识库 Inbox**. Delivery is atomic and one-way:
ClipMind writes into that Inbox only and publishes the copied manifest last. It
does not read or modify the rest of the knowledge base. A downstream Codex task
can summarize, compare, deduplicate, and decide retention independently.

## Visual extraction

```text
2 fps at 640 px
  → dHash fail-open dedupe
  → matching 1280 px canonical images + Vision OCR
  → visual_states/all/
      ├→ progressive-build labels
      └→ adaptive scene clusters → readable representatives → preview/
```

Canonical membership depends only on safe near-duplicate filtering. A hash
failure retains the frame and records a warning. Build grouping and preview
selection never delete or repoint canonical artifacts. `scripts/rebuild_preview.py`
can regenerate the derived preview without reacquiring media or rerunning OCR.

The 1280 px decision came from a same-source experiment on a 227-second code/UI
recording: OCR recognized 1034 unique characters instead of 559 at 640 px, while
OCR wall time increased from 24.7 to 36.6 seconds. The raw measurement is in
[`docs/ocr-resolution-experiment.json`](docs/ocr-resolution-experiment.json).

## Evaluation

```bash
make test            # deterministic offline tests
make eval            # audit existing real-video packs; no re-extraction
make eval-reextract  # fresh real-source extraction; network/providers required
make eval-synthetic  # generated silent-slide end-to-end fixture
make bench
```

- `make test` runs deterministic lifecycle, failure-injection, schema, timeline,
  recovery, cache, concurrency, and visual-algorithm tests without network access.
- `make eval` audits completed real-video packs listed in `eval/cases.json`.
  It is fast and normally offline, but does not prove that the current pipeline
  can reproduce those packs.
- `make eval-reextract` runs the same cases through the current pipeline in a
  fresh temporary library without cache reuse. It requires usable local
  providers, network access, and possibly a current Chrome session; third-party
  source links may expire or disappear. Exact canonical counts are enforced only
  when the downloaded source SHA-256 matches the reviewed case baseline; source
  drift is reported separately and falls back to the broad quality bounds.
- `make bench` checks bounded batch scheduling with deterministic simulated work.

The checked-in three-video evaluation covers long code/UI and scrolling, dense
small text, talking-head captions, inserted documents, and progressive slides:

| Source type | Canonical | Old preview | v1 preview | End-to-end |
| --- | ---: | ---: | ---: | ---: |
| code/UI, 227 s | 243 | 205 | 69 | 41.15 s |
| talking head + documents, 64 s | 29 | 27 | 6 | 13.22 s |
| talking head + slides, 52 s | 31 | 23 | 4 | 8.17 s |

The long code/UI video was also probed at 4 fps. Production 2 fps sampling
covered 105/105 states that stayed dHash-stable for at least 0.5 seconds, so v1
does not add unmeasured adaptive sampling. See
[Real-world evaluation](docs/REAL_WORLD_EVAL.md) and
[Benchmarks](docs/BENCHMARK.md) for methodology and caveats.

`make eval-synthetic` generates a silent four-slide fixture and runs the real
FFmpeg, Vision OCR, no-audio degradation, preview, and packaging path. The
recorded run recognized all four labels and retained all four canonical and
preview states.

## Concurrency and configuration

Independent resource pools prevent network work from occupying GPU capacity:

| Variable | Default | Resource |
| --- | ---: | --- |
| `CLIPMIND_MAX_VIDEOS` | 4 | active videos |
| `CLIPMIND_MAX_FETCH` | 4 | network acquisition |
| `CLIPMIND_MAX_ASR` | 1 | Apple GPU |
| `CLIPMIND_MAX_OCR` | 2 | Vision / CPU |
| `CLIPMIND_SAMPLE_FPS` | 2 | change-detection samples |
| `CLIPMIND_SAMPLE_WIDTH` | 640 | change-detection width |
| `CLIPMIND_EVIDENCE_WIDTH` | 1280 | canonical/OCR width |

Copy `.env.example` to `.env` for the complete list. The default cookie order is
`chrome,-`; Safari is not attempted because its protected cookie container would
otherwise require misleading Full Disk Access advice.

## Design and scope

- [Architecture](docs/ARCHITECTURE.md)—stage purpose, visual semantics,
  concurrency, failure isolation, persistence, and cache identity.
- [Privacy](docs/PRIVACY.md)—network calls, Chrome-cookie access, retained data,
  optional external summary, and Inbox boundary.
- [Limitations](docs/LIMITATIONS.md)—what the measured v1 does not claim.

v1 intentionally supports Douyin on macOS only. Semantic summarization,
multi-platform adapters, bidirectional knowledge-base sync, cancellation, and
large-library pagination are outside the canonical extraction contract.

Licensed under the [MIT License](LICENSE).
