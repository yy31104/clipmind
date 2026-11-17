# Default-tool comparison on the development set

Three independent tools ran on the same ten SHA256-bound 180-second 39C3 excerpts. No held-out input was used. The three comparators use their default visual and recognition settings. ClipMind uses the declared English development protocol; both Docker runs specify English, while the new Mac run detects speech language automatically. These are AI-assisted source annotations; human review is pending.

| Tool and pinned version | Informational changes matched | Boundary precision | Output | Measured wall time |
| --- | --- | --- | --- | --- |
| ClipMind v1.3.0 | 44/60, 73.3% | 44/159, 27.7% | Transcript, spatial OCR, visual states | About 120 s, warm MLX/Vision baseline |
| ClipMind v1.4.0, Mac | 59/60, 98.3% | 59/227, 26.0% | Transcript, spatial OCR, typed visual states | 126.500 s summed case time |
| ClipMind v1.4.0, Docker CPU | 59/60, 98.3% | 59/224, 26.3% | Transcript, spatial OCR, typed visual states | 409.299 s summed case time |
| [Slidegeist](https://pypi.org/project/slidegeist/2026.4.23/) 2026.4.23 | 23/60, 38.3% | 23/45, 51.1% | 106 slide JPEGs, timestamped Markdown and OCR | 174.849 s |
| [slides-extractor](https://github.com/kagiorgos/slides-extractor/tree/57bd8d8f4b3f1371b64afe0a9666e908f20ed005) `57bd8d8` | 21/60, 35.0% | 21/34, 61.8% | 37 slide images in timestamped PPTX decks | 21.227 s |
| [mcp-video-analyzer](https://github.com/guimatheus92/mcp-video-analyzer/tree/84ffd825cbc87c28958c29697e30ea23fbbf71ed) 0.11.0, `84ffd82` | N/A: keyframe sampler | N/A: keyframe sampler | 173 JPEGs, full-frame OCR, transcript, timeline | 357.946 s |

Wall times cover different features and are not a speed ranking. All ran on the same M4 Pro / 24 GB Mac. MCP's first run includes a 72 MB Whisper model download. The initial no-native-OCR Slidegeist run overlapped part of that MCP run; the table uses the later prerequisite-complete OCR run. The PPTX extractor ran after the first two ended. Other local development workloads overlapped some runs, so these are descriptive timings rather than isolated hardware benchmarks. Source preparation and dependency installation are excluded.

## What ClipMind loses

Both slide-only tools have higher boundary precision than either ClipMind version. They also missed more annotated changes, so higher precision alone does not establish better content recovery. The PPTX extractor is much faster for a slide-only job; it does not perform OCR or ASR.

MCP's uniform samples retained Xous's later `scalar_message` and `lend_message` build lines at 01:06. The PPTX extractor retained them at 01:16. ClipMind v1.3.0 retained an earlier, incomplete frame. The selected comparator images are in the results archive under `examples/`. ClipMind v1.4.0 now retains the complete build at the fixed 68-second code reference; its end-to-end Xous code CER is 1.88% on Vision and 1.25% on Docker Tesseract. MCP OCR still loses part of the first line and includes presenter-background text. This is a frame-retention example, not a clean-code OCR score.

## Scope and missing values

Seven windows have discrete informational slide/build labels: 60 changes total. The other three are animation or speaker-only windows and are excluded for every tool. Scene starts are matched one-to-one within ±1 second, using ClipMind's unchanged development evaluator. Slidegeist's public Markdown truncates timestamps to whole seconds; PPTX notes preserve hundredths of a second.

MCP falls back to uniform samples in Xous, Text Rendering and Pixel TitanM2. A sample at 01:06 is not a claim that a slide changed at 01:06. Its selected timestamps are preserved as a diagnostic, not relabeled as detected boundaries.

The PPTX tool has no ASR or OCR feature. MCP exports whole-frame OCR without boxes, so the frozen code-region CER cannot be reproduced from its default output. Scoring all background text against a small code region would be unfair. These fields are N/A, not zero errors or failed recognition.

Slidegeist's first run had no native Tesseract on PATH: it produced slides but disabled OCR. That attempt remains available. The table uses a fresh run with isolated Tesseract 5.5.4 and its default `eng+deu` languages installed; OCR text is present in the Markdown. It returns no spatial OCR boxes, so code-region CER is N/A. Both runs use identical visual settings and retain the same 106 images. AI descriptions remain empty because this published slides-mode description loop receives no text context. A successful exit does not establish descriptions. No cloud service or alternative model was supplied.

MCP used its documented optional local `openai-whisper==20250625` CLI, with its unchanged `tiny` model and automatic language detection, plus default Tesseract.js `eng+por` OCR. A separate full-clip caption diagnostic is 500/4572 word edits (10.94%). It uses all emitted text because the export lacks segment ends, so it must not be compared directly with ClipMind's narrower word-timed 4.22% metric.

[Video-Lectures-To-Searchable-PDF](https://github.com/anmsajedulalam/Video-Lectures-To-Searchable-PDF/tree/4fd037076d1c8eff36d03d14eeac3e3b9436ce53) was inspected but not run: that snapshot has no license file or project license declaration. It is not counted as a failed algorithm.

## Commands

Run from isolated checkouts/environments. `DEV` denotes the shared published-input directory. Each tool receives only an `input.mp4` hard link in its own output directory, with no adjacent caption sidecar. The `--out` arguments select storage; `-y` only suppresses a pause.

```bash
uv venv --python 3.12 envs/slidegeist
uv pip install --python envs/slidegeist/bin/python slidegeist==2026.4.23
envs/slidegeist/bin/slidegeist slides "$INPUT" --out "$CASE/output"
# The prerequisite-complete run adds isolated Tesseract 5.5.4 to PATH;
# no visual, language or OCR mode setting changes.

# MIT source: kagiorgos/slides-extractor at 57bd8d8
uv venv --python 3.12 envs/slides-extractor
uv pip install --python envs/slides-extractor/bin/python -r comparators/slides-extractor/requirements.txt
envs/slides-extractor/bin/python comparators/slides-extractor/slides_extractor.py "$INPUT" -y

# MIT source: guimatheus92/mcp-video-analyzer at 84ffd82
npm ci --prefix comparators/mcp-video-analyzer
uv venv --python 3.12 envs/mcp-whisper
uv pip install --python envs/mcp-whisper/bin/python openai-whisper==20250625
PATH="$TASK/envs/mcp-whisper/bin:$PATH" node comparators/mcp-video-analyzer/dist/index.js analyze "$INPUT" --out "$CASE/frames"
```

The runner clears inherited credentials and confines models, language data, temporary media and caches to the task directory. The archive includes every command, exit status, input hash, timestamp, dependency version and output. It excludes duplicate input media and model caches. No comparator implementation was copied into ClipMind.

Recordings and derived images are CC BY 4.0, credited to the talk authors and CCC in the source manifest. Tool code remains under its respective MIT license. Raw outputs and code/build examples remain available for inspection; they are not substitutes for the pending manual annotation review.

[Download the complete records and images](https://github.com/yy31104/clipmind/releases/download/v1.4.0/clipmind-v1.4.0-comparators.zip). [Release checksums](https://github.com/yy31104/clipmind/releases/download/v1.4.0/SCREEN_SHA256SUMS).
