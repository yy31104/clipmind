# Installation

Install the versioned GitHub package or use the Linux container below. The
PyPI project named `clipmind` is a different project: these instructions do not
install it. This project does not publish to PyPI or provide a notarized DMG
or Homebrew cask.

## One-line package install

After installing [uv](https://docs.astral.sh/uv/) and the system dependencies
listed below, run this in Terminal or PowerShell:

```bash
uv tool install --python 3.12 git+https://github.com/yy31104/clipmind.git@v1.3.0
```

This installs the CLI, web app and MCP server in an isolated tool environment.
Then run `clipmind doctor` and `clipmind serve`; open
[127.0.0.1:8420](http://127.0.0.1:8420). If the command is not on your PATH,
run `uv tool update-shell` and open a new terminal. Git must be installed for
the GitHub source command. No clone or virtual-environment activation is needed.

For English speech, set `CLIPMIND_ASR_LANGUAGE=en` before starting the app;
Chinese is the default. In PowerShell use
`$env:CLIPMIND_ASR_LANGUAGE="en"`, then `clipmind serve`. With Docker, pass
`-e CLIPMIND_ASR_LANGUAGE=en` before the image name.

For a first run with no Python, FFmpeg or Tesseract setup, use the
[one-line Docker command](#docker) instead. Docker itself must be installed.

## System requirements

The package install needs:

- Python 3.12 (uv can install the requested interpreter);
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

## System dependency setup

On macOS with Homebrew, install the prerequisites before the one-line package
command:

```bash
brew install ffmpeg uv
```

On Debian/Ubuntu, install the portable OCR path:

```bash
sudo apt-get update
sudo apt-get install ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra
```

Use the equivalent package manager on Fedora, Arch, Windows, or another
distribution. `clipmind doctor --json` reports the selected providers and core
runtime capabilities without starting a media job.

For Windows, install FFmpeg and Tesseract with your package manager, make their
executable directories available on PATH, and install the OCR language data you
select. Use the package command in PowerShell, or run the Linux container with
Docker Desktop. The Windows GPU is optional; CUDA setup is separate from a
successful CPU installation.

The portable ASR dependency currently uses the immutable official faster-whisper
commit `f6ef59b5e3b70d58e46641417894c3b73eaddb7a`, including its
[empty-alignment fix](https://github.com/SYSTRAN/faster-whisper/pull/1460).
The PyPI 1.2.1 build lacks the matching guard needed by the locked CTranslate2
4.8.2. Both `pyproject.toml` and the pip requirements use the same archive;
`uv.lock` additionally verifies its SHA256. This is an unreleased upstream fix,
not a ClipMind fork or a downgrade of the native runtime.

## Development checkout

```bash
git clone https://github.com/yy31104/clipmind.git
cd clipmind
uv sync --frozen --python 3.12
uv pip install -r requirements-test.txt
make test
make run
```

The development server opens at `http://127.0.0.1:8420`. The source checkout
stores its library under `./out` by default.

`./scripts/install.sh .` installs a checkout as a tool instead. The public
one-line tool install resolves the tagged package's dependency constraints;
the development `uv sync --frozen` command and container build use `uv.lock`.

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

Two guards against Whisper inventing speech during silence or music are on by
default and recorded under `configuration.asr_guards` in each manifest:

```dotenv
CLIPMIND_ASR_VAD_FILTER=1                       # faster-whisper only: Silero VAD before decoding
CLIPMIND_ASR_HALLUCINATION_SILENCE_THRESHOLD=2.0  # seconds; 0 or off disables
```

The silence threshold applies to both providers and relies on word timestamps,
which ClipMind always requests. MLX Whisper has no built-in VAD. Turn the VAD
off if quiet speech over loud music is being dropped.

Optional local speaker diarization is deliberately separate:

```bash
uv tool install --force --python 3.12 'clipmind[diarization] @ git+https://github.com/yy31104/clipmind.git@v1.3.0'
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

The Linux image bundles Python, FFmpeg and Tesseract with English and simplified
and traditional Chinese OCR data. Its Python environment is installed from the
frozen lock; speech model weights download only when first needed.

Run the versioned image with one command (Terminal or PowerShell):

```bash
docker run --rm -p 127.0.0.1:8420:8420 -v clipmind-data:/data ghcr.io/yy31104/clipmind:v1.3.0
```

Open [127.0.0.1:8420](http://127.0.0.1:8420). The first speech extraction can
download a large model. `HF_HOME=/data/.cache/huggingface` keeps downloaded
weights in the named volume alongside the library, so later containers reuse
them. The hidden cache is excluded from the library listing. Evidence Packs
and model weights persist after `--rm`; deleting the container does not release
their disk space. Remove the volume only when you intend to delete both.

Temporary source video and audio follow the normal extraction cleanup lifecycle;
they are different from the retained result packs and reusable model cache.
The container uses CPU inference by default; it does not use Apple Silicon's GPU.

To build the same image from a checkout:

```bash
docker build --build-arg VERSION=1.3.0 -t clipmind .
docker run --rm \
  -p 127.0.0.1:8420:8420 \
  -v "$PWD/clipmind-data:/data" \
  clipmind
```

The image uses faster-whisper and Tesseract. Mount a persistent `/data` volume
for the library and reusable model cache. Chrome is not bundled, and the image
defaults to `CLIPMIND_COOKIE_SOURCES=-`; it does not read the host's browser
cookies. For account-gated sources, mount a narrowly scoped cookie file and
configure `CLIPMIND_COOKIE_FILE`. Douyin's browser-session fallback needs a native
Chrome installation and has not been validated in this container. The native
and container builds do not promise the same platform acquisition coverage.

## Build a Python wheel

```bash
uv build --wheel
```

The wheel includes `clipmind/web/*.html`, `*.css`, and `*.js`. Test an artifact
in a clean environment and run `clipmind doctor` before distributing it.

## Publish Python release assets

Publishing a GitHub Release is the explicit authorization boundary. The
`release assets` workflow checks out that release's tag, requires the tag to
match the version in `pyproject.toml`, and builds one wheel and one source
distribution. It installs the wheel with its dependencies in a clean virtual
environment outside the checkout, verifies the CLI and packaged web assets,
then attaches the two distributions and `SHA256SUMS` to the existing Release.

The workflow does not create a Release and does not upload the unsigned macOS
app or DMG. After the Python artifacts pass, a stable published release also
builds Linux amd64/arm64 images from the same tag and pushes the versioned and
latest tags to `ghcr.io/yy31104/clipmind`. The image publishes source, revision,
license and MCP registry ownership labels. No new personal access token is
required: the container job uses its repository-scoped `GITHUB_TOKEN` with
`packages: write`; pull requests never receive a registry write token.

On the **first GHCR publication**, GitHub creates a private package even for a
public repository. A package administrator must change its visibility to Public
in the package settings. Before announcing the image, verify an anonymous pull
with a clean Docker configuration, inspect both architecture manifests, and run
the downloaded image. A successful push alone does not establish public access.
The Docker command above is a publication target until that check has passed.

PyPI publication would additionally require a different distribution name and a
released dependency in place of the current direct-URL faster-whisper fix. The
GitHub installation route deliberately preserves that fix.

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
