# Installation

ClipMind currently supports installation from source, Python wheel builds,
Docker, and local macOS app/DMG builds. Do not confuse a build recipe with a
published, signed release: the project is not yet on PyPI and no notarized DMG or
Homebrew cask is claimed here.

## System requirements

All platforms need:

- Python 3.11 or 3.12;
- FFmpeg (`ffmpeg` and preferably `ffprobe` on `PATH`);
- yt-dlp (installed as a Python dependency, or available on `PATH`);
- enough disk for model weights, temporary source media, and uncapped canonical
  visual evidence.

Default local providers:

| Platform | ASR | OCR | Additional system dependency |
| --- | --- | --- | --- |
| Apple Silicon macOS | MLX Whisper | Apple Vision | none beyond FFmpeg |
| Intel macOS | faster-whisper | Apple Vision | none beyond FFmpeg |
| Linux | faster-whisper | Tesseract | Tesseract and desired languages |
| Windows | faster-whisper | Tesseract | Tesseract and desired languages |

## Recommended source install

Install [uv](https://docs.astral.sh/uv/) and FFmpeg, then:

```bash
git clone https://github.com/yy31104/clipmind.git
cd clipmind
./scripts/install.sh .
clipmind doctor
clipmind-app
```

On macOS with Homebrew:

```bash
brew install ffmpeg uv
./scripts/install.sh .
```

On Debian/Ubuntu, install the portable OCR path before running the script:

```bash
sudo apt-get update
sudo apt-get install ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra
```

Use the equivalent package manager on Fedora, Arch, Windows, or another
distribution. `clipmind doctor --json` reports the selected providers and core
runtime capabilities without starting a media job.

## Development checkout

```bash
uv venv --python 3.12
uv pip install -r clipmind/requirements.txt
uv pip install -r requirements-test.txt
make test
make run
```

The development server opens at `http://127.0.0.1:8420`. The source checkout
stores its library under `./out` by default.

## Data directories

Installed builds choose a writable per-user directory:

| Platform | Default library |
| --- | --- |
| macOS | `~/Library/Application Support/ClipMind` |
| Windows | `%LOCALAPPDATA%\ClipMind` |
| Linux | `$XDG_DATA_HOME/clipmind` or `~/.local/share/clipmind` |
| source checkout | `<repository>/out` |

Override all of these with an absolute or user-relative `CLIPMIND_OUT` path.

## Provider selection

The `auto` defaults are usually correct:

```dotenv
CLIPMIND_ASR_PROVIDER=auto        # auto | mlx | faster-whisper
CLIPMIND_OCR_PROVIDER=auto        # auto | vision | tesseract
CLIPMIND_ASR_LANGUAGE=zh          # empty means provider auto-detection
CLIPMIND_TESSERACT_LANGUAGES=chi_sim+chi_tra+eng
```

First use can download a large ASR model. Select a different compatible model
with `CLIPMIND_ASR_MODEL` or `CLIPMIND_FASTER_WHISPER_MODEL`.

Optional local speaker diarization is deliberately separate:

```bash
uv tool install --force '.[diarization]'
```

```dotenv
CLIPMIND_DIARIZATION_PROVIDER=pyannote
CLIPMIND_DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
HF_TOKEN=your-hugging-face-token
```

The configured model may require accepting its upstream license. If diarization
is unavailable or fails, ClipMind preserves the transcript without inventing
speaker labels.

## Cookies

The default is `CLIPMIND_COOKIE_SOURCES=chrome,-`: try Chrome, then no cookies.
To avoid browser-cookie access:

```dotenv
CLIPMIND_COOKIE_SOURCES=-
```

For a narrow exported cookie file:

```dotenv
CLIPMIND_COOKIE_SOURCES=-
CLIPMIND_COOKIE_FILE=/absolute/path/to/cookies.txt
```

Read [Privacy](PRIVACY.md) before enabling browser cookies. The permission
boundary is the browser cookie store, not only the submitted source domain.

## Docker

```bash
docker build -t clipmind .
docker run --rm \
  -p 127.0.0.1:8420:8420 \
  -v "$PWD/clipmind-data:/data" \
  clipmind
```

The image uses faster-whisper and Tesseract. Mount a persistent `/data` volume
for the Evidence Pack library. The provider model cache is separate; mount a
second volume at `/root/.cache/huggingface` if it should survive container
replacement. For account-gated sources, mount a narrowly scoped cookie file and
configure `CLIPMIND_COOKIE_FILE`; the image does not inherit host browser state.

## Build a Python wheel

```bash
uv build --wheel
```

The wheel includes `clipmind/web/*.html`, `*.css`, and `*.js`. Test an artifact
in a clean environment and run `clipmind doctor` before distributing it.

## Build the macOS app and DMG

```bash
./scripts/build_macos_app.sh
```

This creates `dist/ClipMind.app` and `dist/ClipMind.dmg`. Without
`CODE_SIGN_IDENTITY` they are unsigned development artifacts. A public macOS
release must be signed, hardened, notarized, and tested from the downloadable
artifact—not merely from the source checkout.

## Useful checks

```bash
clipmind doctor
clipmind doctor --json
clipmind serve --host 127.0.0.1 --port 8420
clipmind-app --no-browser
```

If `doctor` reports OCR unavailable on Linux/Windows, verify both the Tesseract
binary and selected language data. If URL acquisition fails, verify the link in
a browser and then decide whether an unauthenticated request, a fresh session,
or a narrow cookie file is appropriate. ClipMind does not bypass private,
removed, DRM-protected, or region-restricted media.
