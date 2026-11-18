# v1.6.0 screen holdout

Round 1 covers four complete recordings with 80 minutes of audio and video.
The [source records](data/v160-round1-sources.json) list licenses, downloads,
normalization, checksums and screened alternatives.

| Recording | Source | Screen content | Changes | Code regions / characters |
| --- | --- | --- | ---: | ---: |
| [Realistic Lighting Hacks in Blender](https://commons.wikimedia.org/wiki/File:Realistic_Lighting_Hacks_in_Blender.webm) | Gleb Alexandrov, Blender Conference | Room camera and projected slides | 53 | 0 / 0 |
| [Modern Emacs/Elisp hardware/software accelerated graphics](https://emacsconf.org/2025/talks/graphics/) | Emanuel Berg, EmacsConf | Live graphics demo, code and terminal | 75 | 25 / 3,286 |
| [Managing WordPress On the Go](https://wordpress.tv/2020/06/08/jaclyn-chen-%E9%9A%A8%E6%99%82%E9%9A%A8%E5%9C%B0%E7%9A%84%E9%83%A8%E8%90%BD%E6%A0%BC%E7%AE%A1%E7%90%86-wordpress-app-%E4%BD%BF%E7%94%A8-%E9%96%8B%E7%99%BC%E7%B6%93%E9%A9%97%E5%88%86/) | Jaclyn Chen, WordCamp Taipei, footage credited to yuli-yang | Mandarin slides and mobile UI | 48 | 0 / 0 |
| [virtme-ng: quickly test a kernel from Debian](https://debconf25.debconf.org/talks/56-virtme-ng-quickly-test-a-kernel-from-debian/) | Helen Koike, DebConf25 | Slides, terminal commands and live output | 35 | 15 / 448 |

Two separate GPT-6.2 Sol sessions labeled the same source samples independently without extraction output. A separate source-only pass resolved disagreements. The [pre-adjudication agreement](data/v160-round1-label-agreement.json) was 198 matches across 203 and 212 proposals (boundary F1 95.42%). The nine identical crops had 0 edits in 1,417 characters in both directions. Independently placed regions matched in 11 pairs. The annotators chose 6 and 33 unmatched regions, respectively.

Most boundary evidence samples are one second apart. The OCR reference time is the exact presentation timestamp of each source crop, verified by re-encoding 35 source frames and matching all 35 JPEGs byte-for-byte. The [scoring labels](data/v160-round1-native-time-labels.json), [native frame evidence](data/v160-round1-native-time-evidence.json) and [original sample-time labels](data/v160-round1-labels.json) preserve both timings. The EmacsConf recording ends with a short audio-only tail. It has no narration, and its chapter track is unsuitable for speech scoring. It contributes 88.0% of code characters, so results also give per-recording OCR scores.

The [prepared inputs](data/v160-round1-cases.json) are scaled and padded to 1280×720 without cropping, with H.264 video and AAC audio. The label and source hashes bind both paths to identical prepared video bytes. Score one attempt per backend using the [frozen screen rules](SCREEN_LABEL_PROTOCOL_V160.md) and the existing one-to-one matcher at ±1 second. Each backend targets at least 85% recall and 50% precision, with code CER at most 15%. Every expected change and code character stays in the denominator if a recording fails.

## Candidate

The [campaign lock](data/v160-round1-campaign.json) binds commit `b8982c4`,
the [runtime files](data/v160-round1-runtime.json), both scoring scripts and
the [Linux ARM64 image](data/v160-round1-docker-image.json). Vision runs with
MLX Whisper on Apple Silicon. Tesseract runs with faster-whisper on Docker
CPU. Both use automatic speech language detection. The Mandarin recording
uses Traditional Chinese and English for OCR. The [development
results](SCREEN_V160_DEVELOPMENT.md) give the baseline and candidate scores
on the exposed recordings.
