# v1.5.0 screen holdout protocol

The v1.4.0 holdout has been scored once and is now development data. This set
replaces it for v1.5.0. It is frozen before any extraction change and runs once.

## Selection

Three complete recordings from three publishers, none previously used by
ClipMind: not the ten 39C3 excerpts, the full Xous talk, or the three v1.4.0
holdout talks.

| Case | Publisher and license | Language | Why it is here |
| --- | --- | --- | --- |
| `fosdem2026-synctest` | FOSDEM 2026, CC BY 2.0 BE | English | Dense Go code and terminal output; slides beside a speaker inset |
| `pycontw2024-observability` | PyCon Taiwan 2024, YouTube CC BY | Mandarin | Traditional Chinese slides with small Python and config code; speaker inset |
| `coscup2022-govdata` | COSCUP 2022 via Wikidata Taiwan on Wikimedia Commons, CC BY 4.0 | Mandarin | Room camera filming a projected screen, with people in front |

The set has 69 minutes, 182 annotated changes and 15 code regions (4,165
reference characters): 8 in the English Go talk and 7 in the Mandarin Python
talk. Candidates were screened from three source thumbnails each, never from
ClipMind output. The pool and the reasons for each choice are in
[the selection record](data/v150-holdout-selection.json). Code regions come
from two different talks and both languages. The camera recording tests
whether layout and speaker handling generalize to a room view.

## Preparation

Keep each full published recording, including openings, questions and
camera-only parts. Normalize to 1280x720 letterboxed H.264 CRF 20 with AAC at
128 kbit/s, exactly as in v1.4.0. Record the original's bytes and SHA-256 (and
the publisher SHA-1 where one exists) plus the normalized input's SHA-256.

```bash
python scripts/prepare_v150_holdout.py --workdir ../holdout-v150
```

Encoder versions can change normalized bytes. The release input archive is the
scoring copy.

## Labels

Labels follow the v1.4.0 rules in [HOLDOUT_SCREEN_PROTOCOL.md](HOLDOUT_SCREEN_PROTOCOL.md):
every incremental informational build is a primary change; speaker motion,
camera cuts, picture-in-picture changes, cursor motion and animation frames are
not. Each one-second contact-sheet cell is inspected, then full-size frames
around every candidate change. A source-only pixel-difference listing helps
find small builds; it does not decide a label. Times use the same zero-based
clock as ClipMind sampling.

Three cases the v1.4.0 rules did not spell out are fixed here before any
run. A page shown for under one second while the speaker navigates is not a
change. Bold or boxed emphasis on unchanged content is not a change. In a live
demo, a new screen or command result counts once it stays unchanged for at
least one second; typing, menus, hovering and scrolling do not count.

Code references are transcribed from full-size source frames without OCR,
spread across each code talk, including small text. Code that cannot be read
from the source is marked N/A with a reason rather than guessed: the COSCUP
room camera shows one SPARQL screenshot whose text is blurred even in the
1080p original.

Annotations are AI-assisted; human review is pending.

## Single campaign

Freeze this file, the selection record, the source manifest, the labels and the
preparation script in one commit before changing extraction code. After
development ends, lock one candidate commit and run it once on each path:

| Path | Speech | Screen text |
| --- | --- | --- |
| Mac | MLX Whisper large-v3-turbo, automatic language | Apple Vision |
| Docker CPU | faster-whisper large-v3-turbo int8, automatic language | Tesseract |

Both paths count as one campaign. The targets are unchanged: change recall at
least 85%, information-boundary precision at least 50% and code CER at most
15%. Results are published as they are; there is no tuning or rerun afterwards.
