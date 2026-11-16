# ClipMind

![Python](https://img.shields.io/badge/Python-3.12-2E7D6E?style=flat-square&labelColor=25292E)
[![CI](https://github.com/yy31104/clipmind/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/yy31104/clipmind/actions/workflows/tests.yml?query=branch%3Amain)

A transcript misses the code, charts, and documents shown on screen.

ClipMind turns YouTube and Douyin links or local media into timestamped transcripts, screen text, and frames. It keeps the extracted evidence in a local folder that you can browse, search, or pass to another tool through the CLI, REST API, Python SDK, or MCP server.

Personal project. Built with AI assistance.

[Run locally](#run-locally) · [Real-video results](docs/REAL_TALK_BENCHMARK.md) · [Agent demo](docs/REAL_TALK_DEMO.md#a-real-agent-used-the-mcp-tools) · [Docs](#docs)

![Search a real Xous talk and jump to the corresponding code slide](docs/images/real-talk-search.gif)

Search **“a string that is moved”** → speech at **12:01** → its ownership slide.
This recording uses a complete public 39-minute talk, not a generated fixture.
An agent also answered a question using the saved transcript, timeline and images
through MCP. [Source, attribution, captured calls and reproduction](docs/REAL_TALK_DEMO.md).

The [real-video table](docs/REAL_TALK_BENCHMARK.md) covers ten fixed three-minute
excerpts. It reports missed visual changes and code OCR errors as well as
successful extraction. `complete` means the pipeline finished, not perfect recall.

## How it works

1. [sources/registry.py](clipmind/sources/registry.py) selects a source adapter; [acquisition.py](clipmind/acquisition.py) acquires media in a temporary directory.
2. [preflight.py](clipmind/preflight.py) estimates the work before [pipeline.py](clipmind/pipeline.py) starts speech and visual processing.
3. [asr.py](clipmind/asr.py), [ocr.py](clipmind/ocr.py), and [visual_states.py](clipmind/visual_states.py) extract timestamped speech, text, and visual changes.
4. [evidence.py](clipmind/evidence.py) writes the pack and its completion manifest; [index.py](clipmind/index.py) builds a searchable SQLite index.
5. [server.py](clipmind/server.py), [cli.py](clipmind/cli.py), [sdk.py](clipmind/sdk.py), and [mcp.py](clipmind/mcp.py) expose the saved evidence.

The full frame set is retained separately from the smaller reading preview. The completion manifest is written last; interrupted jobs remain visible after restart.

## Project layout

| Path | What | Start here |
| --- | --- | --- |
| `clipmind/` | Acquisition, extraction, storage, and interfaces | [pipeline.py](clipmind/pipeline.py) |
| `clipmind/web/` | Browser interface | [app.js](clipmind/web/app.js) |
| `schemas/` | Evidence Pack format | [JSON Schema](schemas/evidence-pack-v1.schema.json) |
| `tests/` | Offline lifecycle, API, and extraction checks | [test_pipeline.py](tests/test_pipeline.py) |
| `scripts/` | Installation, local samples, and measurement tools | [evaluate_silent_slides.py](scripts/evaluate_silent_slides.py) |
| `docs/` | Installation, usage, architecture, and privacy | [Usage](docs/USAGE.md) |

## Run locally

With Docker installed, one command starts the app with FFmpeg, Tesseract and
portable speech recognition included:

```bash
docker run --rm -p 127.0.0.1:8420:8420 -v clipmind-data:/data ghcr.io/yy31104/clipmind:v1.4.0
```

Open [127.0.0.1:8420](http://127.0.0.1:8420), paste a link or upload a local
video, then search its saved evidence in Library. No ClipMind account or paid
API key is required. Speech models download on first use. Docker uses the CPU;
its image does not include Chrome for Douyin's browser fallback.

For native Apple Silicon acceleration, or an existing Python setup, install
[uv](https://docs.astral.sh/uv/), Git and FFmpeg first (Linux/Windows also need
Tesseract), then run:

```bash
uv tool install --python 3.12 git+https://github.com/yy31104/clipmind.git@v1.4.0
```

Start with `clipmind doctor`, then `clipmind serve`. Speech language is detected
automatically. Set `CLIPMIND_ASR_LANGUAGE=en` (or another language code) to override it.
The PyPI package called `clipmind` belongs to another project; use the GitHub
command above. [Platform setup, updates and Docker persistence](docs/INSTALL.md).

Temporary source media is cleaned after processing; saved screenshots,
transcripts and model weights remain. Removing packs in the UI moves them to
`.trash`, which still occupies disk space. The Docker named volume persists
across restarts. Review [privacy](docs/PRIVACY.md) before using browser cookies.

## Develop and reproduce

```bash
git clone https://github.com/yy31104/clipmind.git
cd clipmind
uv sync --frozen --python 3.12
uv pip install -r requirements-test.txt -r scripts/requirements-eval.txt
```

Offline checks take about a minute locally:

```bash
make test
make eval-synthetic
```

The synthetic OCR check has recorded passes with macOS Vision and Linux Tesseract. See the [Linux fixture repair](docs/BENCHMARK.md#synthetic-silent-slides-on-linux-with-tesseract) and [real Whisper comparison](docs/BENCHMARK.md#real-whisper-three-setting-comparison) for results and limits.

## Design notes

Speech and visual processing run separately, then join at pack creation. This preserves screen text that was never spoken and keeps a usable record when one recognition provider is unavailable.

The full frame set and the reading preview serve different purposes. Preview selection changes what a reader sees first while the original retained frames remain addressable by timestamp.

Evidence Pack files are the source of truth. The search index can be rebuilt, and the same saved pack is available through every interface. See the [format](docs/EVIDENCE_PACK.md) and [architecture](docs/ARCHITECTURE.md).

## Limits

- Source sites can reject downloads, change their responses, or remove media. Browser cookies are optional and grant access to the configured cookie store; see [privacy](docs/PRIVACY.md).
- OCR, speech recognition, and preview selection can miss information. The full frame set is uncapped, so long videos can require substantial time and disk space.
- New social platforms from the old draft candidates are not shipped. See the [source verification matrix](docs/PLATFORMS.md); generic URL acceptance does not guarantee extraction.
- The web server has no authentication and binds to loopback by default. Keep it local; share exported packs only after reviewing their contents.

## Docs

- [Installation and platform setup](docs/INSTALL.md)
- [CLI, SDK, REST, and MCP usage](docs/USAGE.md)
- [Real talk demo and MCP question answering](docs/REAL_TALK_DEMO.md)
- [Ten-source quality measurements](docs/REAL_TALK_BENCHMARK.md)
- [v1.4.0 release notes](docs/releases/v1.4.0.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Evidence Pack format](docs/EVIDENCE_PACK.md)
- [Privacy](docs/PRIVACY.md)
