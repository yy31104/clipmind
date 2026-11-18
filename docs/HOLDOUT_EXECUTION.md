# Holdout execution

The [source freeze](data/v140-holdout-freeze.json) binds the three complete recordings and annotations. The final campaign uses one locked candidate commit and one attempt per recording on each of these two paths:

| Path | Speech | Screen text | Execution |
| --- | --- | --- | --- |
| Native Mac | MLX Whisper large-v3-turbo, automatic language | Apple Vision, source-declared languages | Apple Silicon; warm model cache |
| Docker CPU | faster-whisper large-v3-turbo, automatic language, int8 | Tesseract, source-declared languages | Linux ARM64; 6 CPUs; 4 GiB; warm model cache |

This does not measure Windows, x86 Linux or an NVIDIA GPU. On the selected CPU image, loading the cached model with either `compute_type=auto` or `compute_type=int8` resolves to CTranslate2's `int8_float32`. The evaluator records the production model's actual device and compute type after each case.

Both paths use `--force` to finish the full recording when the complete-pack cost estimate exceeds its local threshold. Fetch and probe limits keep their configured time, byte and request bounds. No video is shortened to fit the cost estimate.

## Run records

Prepare two separate work directories containing the frozen input bytes. Use distinct once-token files and new report paths. Each token is created before the first SDK call and remains after failure or interruption. Run both paths against the same candidate bytes, then retain all outcomes without post-result tuning or replacement.

```bash
python scripts/evaluate_real_talks.py run \
  --corpus docs/data/v140-holdout-cases.json \
  --labels docs/data/v140-holdout-labels.json \
  --workdir ../holdout-mac --output ../campaign/mac-results.json \
  --once-token ../campaign/mac-once.json --force \
  --asr-provider mlx --ocr-provider vision
```

Docker uses the installed package from the candidate image. Mount only the evaluation driver, frozen manifests, prepared inputs, output directory and evaluation-only metric dependencies. Supply `--installed-runtime --expected-runtime-fingerprint /evaluation/runtime.json`; that manifest contains the locked checkout's Python/web SHA-256 hashes and commit. The driver removes the mounted checkout from runtime import lookup and checks every installed Python/web byte before reserving a token or calling the SDK. A mismatched image is a setup failure, not a holdout result.

The Docker launch uses `--cpus=6 --memory=4g --network=none`, a read-only warm model cache, `--asr-provider faster-whisper --ocr-provider tesseract`, `--force`, and its own once-token. The per-source OCR languages are in the frozen corpus; ASR language stays automatic. Reports include the complete dependency versions, actual runtime hash, evaluator hashes, source checksums, timings and cleanup inventory. The launch record includes the image digest and resource limits.

## Time estimates

Before transcription and OCR, ClipMind shows a broad planning range. It excludes acquisition, queue time and model downloads and assumes one active video. CPU scheduling and competing workloads can extend it.

The published v1.3.0 Docker image processed the complete 39:24 Xous recording in 450.892 seconds with the resource limits above: 382.773 seconds of ASR, 66.159 seconds of OCR and 61.794 seconds of sampling. Speech and OCR overlap, so their stage times should not be added to predict elapsed time. The model download took another 92.120 seconds and was measured separately. This development recording informed the portable estimate's lower range; no holdout result is used to tune it.

Annotations remain AI-assisted; human review is pending. Source review packages are separate from model outputs and contain editable owner-review tables.

## Recorded campaign

Candidate: `47c5334f43f5841ebaaa3ecfeab4938088fdf97f`. Runtime SHA-256: `e7c9c63c00c4843c4ed4128621b2b4aa6cb6514d6f2b6049102ee29e40fafc20`. Both one-attempt tokens bind these bytes, the frozen source files and the evaluator hashes. The installed Docker byte check passed before its token was reserved.

Both paths finished all three recordings. Recall is 73/78 on each; precision is 20.39% on Mac and 20.28% on Docker; code CER is 19.00% and 16.08%. Recall meets its target; precision and code CER miss theirs. See the [complete results and limits](https://github.com/yy31104/clipmind/blob/v1.4.0/docs/SCREEN_QUALITY.md#frozen-holdout) and [independent audit](data/v140-holdout-independent-audit.json). No replacement run or post-result tuning was performed.
