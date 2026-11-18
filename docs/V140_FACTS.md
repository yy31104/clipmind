# ClipMind v1.4.0: facts and links

[Release](https://github.com/yy31104/clipmind/releases/tag/v1.4.0) · [Code](https://github.com/yy31104/clipmind) · [MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.yy31104%2Fclipmind/versions/1.4.0)

Local video evidence: timestamped speech, screen text and retained images, available through a browser, CLI, Python SDK, REST and MCP. Free local models; no paid API key. Docker includes FFmpeg and Tesseract and uses CPU inference.

| Evidence | Mac, MLX + Vision | Docker CPU, Linux ARM64 |
| --- | ---: | ---: |
| Development change recall, before → after | 44/60 → 59/60 | 44/60 → 59/60 |
| Development code CER, before → after | 39.29% → 6.24% | 54.30% → 23.61% |
| One-shot holdout change recall | 73/78, 93.59% | 73/78, 93.59% |
| Holdout precision / code CER | 20.39% / 19.00% | 20.28% / 16.08% |

Holdout: three full public talks, 40 minutes, English/Mandarin, 78 changes. All 15 code ROIs (2,295 characters) come from one English talk; Chinese code OCR is N/A. Recall meets 85%; precision misses 50% and CER misses 15%. Known timing/transcription discrepancies remain in the frozen references. These scores use the original AI-assisted labels.

[Full results, regressions and limits](https://github.com/yy31104/clipmind/blob/v1.4.0/docs/SCREEN_QUALITY.md) · [Default-tool comparison](TOOL_COMPARISON.md) · [Source licenses](HOLDOUT_SCREEN_PROTOCOL.md)

The published v1.3.0 CPU image processed the full 39:24 Xous talk in 450.892 s, plus a separate 92.120 s first model download, with six CPUs and 4 GiB. Timings may overlap other local work; Windows/CUDA quality was not measured.

[Saved-frame comparison GIF](https://github.com/yy31104/clipmind/releases/download/v1.4.0/clipmind-v1.4.0-screen-retention.gif) shows the actual baseline/final Xous selection at 68 s, with source credit. [Real search-to-slide and agent MCP demo](REAL_TALK_DEMO.md) remains the v1.3.0 recording.

[Owner review ZIP](https://github.com/yy31104/clipmind/releases/download/v1.4.0/clipmind-v1.4.0-owner-review.zip): all 163 source annotations, editable tables/CSV, about 94 minutes. Extract and open `index.html`; exported decision tables start blank. The optional inspection section locates five WikiPie timing questions and the Shell270 comment question. Human review is still pending.

[Run records and failures](https://github.com/yy31104/clipmind/releases/download/v1.4.0/clipmind-v1.4.0-run-records.zip) · [All assets and SHA-256](https://github.com/yy31104/clipmind/releases/download/v1.4.0/SCREEN_ASSETS.json) · [Checksum file](https://github.com/yy31104/clipmind/releases/download/v1.4.0/SCREEN_SHA256SUMS)

```bash
docker run --rm -p 127.0.0.1:8420:8420 -v clipmind-data:/data ghcr.io/yy31104/clipmind:v1.4.0
```

Open http://127.0.0.1:8420. Model weights download on first use. Reprocess older videos to apply new extraction rules. Temporary media are cleaned; saved packs, exports, model weights and recoverable trash remain.
