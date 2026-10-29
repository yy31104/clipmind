# Installation

ClipMind currently supports installation from source, Python wheel builds,
Docker, and local macOS app/DMG builds. Do not confuse a build recipe with a
published, signed release: the project is not yet on PyPI and no notarized DMG or
Homebrew cask is claimed here.

## System requirements

All platforms need:

- Python 3.11 or 3.12; use **3.12** for the recommended locked checkout;
- FFmpeg, with both `ffmpeg` and `ffprobe` on `PATH`; stream detection uses
  `ffprobe` to distinguish audio-only files from videos;
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

Use a checkout and `uv.lock` for the reproducible installation path. Git is
needed to clone and update the repository. Install [uv](https://docs.astral.sh/uv/)
and the system tools before creating the Python environment.

On macOS with Homebrew:

```bash
brew install ffmpeg uv
```

On Debian/Ubuntu, install FFmpeg, Tesseract and the default OCR languages:

```bash
sudo apt-get update
sudo apt-get install ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra
```

Then, on macOS/Linux:

```bash
git clone https://github.com/yy31104/clipmind.git
cd clipmind
uv python install 3.12
uv sync --locked --python 3.12
uv run --locked clipmind doctor
uv run --locked clipmind demo
uv run --locked clipmind serve --host 127.0.0.1 --port 8420
```

Open `http://127.0.0.1:8420/`; keep the server process running. Use the equivalent
system packages on other Linux distributions. Windows has its own steps below.
`uv run --locked clipmind doctor --json` reports the selected providers, library
path and core capabilities without starting a media job.

`scripts/install.sh` remains an optional macOS/Linux convenience installer using
`uv tool install`. That creates a separate tool environment and resolves package
dependencies; it is **not** the lockfile installation path above. Do not mix its
bare `clipmind` command with the checkout's environment when diagnosing a problem.

### Windows (PowerShell)

Do not run `scripts/install.sh` on Windows. First install Git if `git --version`
is unavailable, then the system tools:

```powershell
winget install --id astral-sh.uv --exact
winget install --id Gyan.FFmpeg --exact
winget install --id UB-Mannheim.TesseractOCR --exact
# Install Chrome separately if Douyin browser fallback is needed.
```

Close and reopen PowerShell so package-manager PATH changes take effect. Clone
the repository and create its environment from `uv.lock`:

```powershell
git clone https://github.com/yy31104/clipmind.git
Set-Location clipmind
uv python install 3.12
uv sync --locked --python 3.12
```

For a fresh Windows checkout without the CUDA 12 cuBLAS and cuDNN 9 runtime,
create a local `.env` before processing speech:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
@'
CLIPMIND_FASTER_WHISPER_DEVICE=cpu
CLIPMIND_FASTER_WHISPER_MODEL=small
CLIPMIND_FASTER_WHISPER_COMPUTE_TYPE=int8
'@ | Set-Content -Encoding ascii .env
}
```

The guard preserves an existing `.env`; edit its settings instead of overwriting
it. ASCII is intentional for these ASCII-only lines: Windows PowerShell 5.1's
`utf8` output includes a BOM that can change the first variable name. The `.env`
is ignored by Git. The default
`device=auto`, `compute_type=auto` behavior remains unchanged in the project.

The Tesseract installer may include only English. Add simplified and traditional
Chinese language data to a per-user directory without changing Program Files:

```powershell
$data = Join-Path $env:LOCALAPPDATA 'ClipMind\tessdata'
New-Item -ItemType Directory -Path $data -Force | Out-Null
Copy-Item "$env:ProgramFiles\Tesseract-OCR\tessdata\eng.traineddata" $data
Copy-Item "$env:ProgramFiles\Tesseract-OCR\tessdata\osd.traineddata" $data
Invoke-WebRequest 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata' -OutFile (Join-Path $data 'chi_sim.traineddata')
Invoke-WebRequest 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_tra.traineddata' -OutFile (Join-Path $data 'chi_tra.traineddata')
```

The launcher also
finds the usual winget locations and sets `TESSDATA_PREFIX` for this directory:

```powershell
.\scripts\clipmind_windows.ps1 doctor
.\scripts\clipmind_windows.ps1 demo
.\scripts\clipmind_windows.ps1 serve
```

Open `http://127.0.0.1:8420/`. The launcher stays in the foreground; closing the
terminal stops the server. Wait for active work to finish, then Ctrl+C stops it.
Starting it again detects a running ClipMind instance and does not interrupt it.

If `doctor` reports missing languages, check the files in the per-user tessdata
directory. Choose `CLIPMIND_TESSERACT_LANGUAGES=eng` only when English-only OCR is
what you want; do not label missing Chinese data as a successful Chinese setup.

To try WeChat Channels share links, install Chrome and run
`.\scripts\clipmind_windows.ps1 wechat-login`. Scan the code yourself on the
Tencent Yuanbao page, then close that Chrome window before retrying the link.
This path is experimental and is not part of the Windows end-to-end acceptance
that verified Douyin. It never needs a Cookie file or pasted credentials.

The demo is silent: it verifies local video sampling, screenshots, OCR, pack
writing and temporary source cleanup, but cannot prove speech transcription.
With `device=auto`, faster-whisper may select an NVIDIA GPU even when its CUDA
runtime DLLs are missing. If transcription fails with a missing
`cublas64_12.dll` or cuDNN library, set
`CLIPMIND_FASTER_WHISPER_DEVICE=cpu` in the checkout's `.env` and rerun a local
video with speech. For a quicker CPU first run, also set
`CLIPMIND_FASTER_WHISPER_MODEL=small`; the default `large-v3-turbo` is much
larger. If a Hugging Face model download stalls, `HF_HUB_DISABLE_XET=1` in
`.env` uses the regular download path. CPU inference is slower but works without
CUDA runtime libraries.
`clipmind doctor` checks that the ASR package imports; its report explicitly
marks the model and runtime untested. `clipmind doctor --deep` runs a short
offline ASR smoke test in a child process with a 90-second timeout. The model
must already be cached; the check never downloads it. This catches missing CUDA
runtime DLLs without silently switching an explicitly selected CUDA device to
CPU. `doctor` also checks that every configured Tesseract language is available.
The launcher binds the UI only to `127.0.0.1:8420`.

For speech acceptance, process a short local recording with speech in the UI.
First use may download the configured model. Check that the transcript is
present and the pack reports its actual completeness. Once the model is cached,
`.\scripts\clipmind_windows.ps1 doctor -Deep` exercises the offline runtime check.
Use `uv run --locked clipmind verify PACK_ID --json` to validate saved artifacts;
this verifies structure and images, not every recognized word.

#### Images and videos without speech

Use **选择文件** or drag in a local image. JPEG, PNG, WebP, BMP, TIFF, GIF,
APNG and AVIF are accepted file types; successful decoding depends on installed
Pillow/FFmpeg support. Animated images follow video sampling. Static images and
acquired Douyin image posts keep original image bytes and page order, then run
the configured local OCR provider. These paths do not require Whisper to load.
Videos without an audio track, or with no detected speech, can still complete
with screenshots and OCR. Image-post audio that was not acquired is reported
as `not_acquired`, not as proof that the post had no speech. A corrupt image,
unsupported codec, inaccessible source or upstream change can still fail; a
successful local test does not verify every website or file format.

#### Optional NVIDIA ASR on Windows

Start with the CPU setup above. The documented faster-whisper GPU setup uses
CUDA 12 cuBLAS and CUDA 12 cuDNN 9. Check your driver's
compatibility against the [NVIDIA cuDNN support matrix](https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/support-matrix.html)
before choosing runtime versions. `nvidia-smi` reports the driver's CUDA
capability; it does not prove the DLLs are installed.

This exact combination was tested on Windows with an RTX 4060 Laptop GPU and
driver 555.97. Install the official NVIDIA wheels outside the locked Python
environment, then make their DLL directories visible to the ClipMind process:

```powershell
$runtime = Join-Path $env:LOCALAPPDATA 'ClipMind\cuda12-runtime'
uv pip install --target $runtime --no-deps nvidia-cublas-cu12==12.5.3.2 nvidia-cudnn-cu12==9.10.0.56
$cublas = Join-Path $runtime 'nvidia\cublas\bin'
$cudnn = Join-Path $runtime 'nvidia\cudnn\bin'
$env:PATH = "$cublas;$cudnn;$env:PATH"
```

The PATH assignment applies to this PowerShell session. Add those two
directories to your user PATH if you want future terminals and background
launches to inherit them. Back up your existing user PATH before changing it.
Do not add an unrelated CUDA 13 runtime or replace a working graphics driver
just because a newer version exists. In your existing `.env`, set:

```dotenv
CLIPMIND_ASR_PROVIDER=faster-whisper
CLIPMIND_FASTER_WHISPER_MODEL=small
CLIPMIND_FASTER_WHISPER_DEVICE=cuda
CLIPMIND_FASTER_WHISPER_COMPUTE_TYPE=float16
```

Run `.\scripts\clipmind_windows.ps1 doctor -Deep`, then process a short local
video containing speech and confirm GPU activity with `nvidia-smi`. The deep
check actually loads a cached model and decodes audio; it never downloads a
model. If CUDA fails, restore the working CPU setting with `device=cpu`,
`compute_type=int8`, and `model=small`. A silent demo alone cannot verify ASR.

## Development checkout

Install Node.js (`node` on `PATH`) for JavaScript syntax and UI behavior tests.
Create the project environment from the lockfile rather than installing a
separate set of unpinned requirements:

```bash
uv sync --locked --python 3.12
```

On macOS/Linux:

```bash
make test
make run
```

On Windows, use native PowerShell commands; Make, jq, and a Unix shell are not
required:

```powershell
node --check clipmind/web/app.js
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\.venv\Scripts\python.exe -m compileall -q clipmind tests scripts
& .\.venv\Scripts\python.exe -m json.tool schemas/evidence-pack-v1.schema.json > $null
.\scripts\clipmind_windows.ps1 serve
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

## Update and rollback

1. Stop submitting work and wait for running jobs to finish. Stop the server
   with Ctrl+C in its terminal. Native model calls may need time to return.
2. Run `uv run --locked clipmind doctor --json` and note its `library` path.
   Back up that directory and your existing `.env` **outside** the checkout.
   If you set `CLIPMIND_OUT`, back up that location rather than assuming `out/`.
   Keep original media and exported Evidence Packs; an update does not require
   deleting either.
3. Record the current commit with `git rev-parse HEAD`. Check `git status --short`;
   save or commit your own changes before updating. Do not use a hard reset to
   work around a dirty tree.
4. Update the branch you intentionally installed and rebuild its locked environment:

   ```powershell
   git pull --ff-only
   uv sync --locked --python 3.12
   .\scripts\clipmind_windows.ps1 doctor
   .\scripts\clipmind_windows.ps1 demo
   uv run --locked clipmind verify PACK_ID --json
   .\scripts\clipmind_windows.ps1 serve
   ```

   Replace `PACK_ID` with an existing saved pack. On macOS/Linux, use
   `uv run --locked clipmind doctor`, `demo`, and
   `serve --host 127.0.0.1 --port 8420` in place of the PowerShell launcher.
   Preserve `--extra diarization` on `uv sync` if you use that optional provider.

If an update fails, stop the new server. Restore the recorded previous commit
in a separate checkout, sync that revision's lockfile, and restore your own
configuration. Test against a **copy** of the backed-up library before pointing
the older version at your working library. Run only one server against a library.
Do not overwrite a newer library with the backup just to roll back application
code. Moving between a tool installation and a checkout also changes the default
library path; set an explicit absolute `CLIPMIND_OUT` to keep the intended library.

Updating does not automatically re-extract failed links or rewrite historical
previews. New jobs and compact exports use the installed selector; full original
packs remain available. See [production operation](PRODUCTION.md) for recovery,
verification and the boundaries of these checks.

## Provider selection

The `auto` defaults are usually correct:

```dotenv
CLIPMIND_ASR_PROVIDER=auto        # auto | mlx | faster-whisper
CLIPMIND_OCR_PROVIDER=auto        # auto | vision | tesseract
CLIPMIND_ASR_LANGUAGE=zh          # empty means provider auto-detection
CLIPMIND_FASTER_WHISPER_DEVICE=auto # auto | cpu | cuda
CLIPMIND_FASTER_WHISPER_COMPUTE_TYPE=auto # auto | int8 | float16, as supported by the device
CLIPMIND_TESSERACT_LANGUAGES=chi_sim+chi_tra+eng
```

First use can download a large ASR model. Select a different compatible model
with `CLIPMIND_ASR_MODEL` or `CLIPMIND_FASTER_WHISPER_MODEL`.

Optional local speaker diarization is deliberately separate:

For a source checkout, including the Windows launcher, install the locked extra
into the same project `.venv`:

```bash
uv sync --locked --python 3.12 --extra diarization
```

Keep `--extra diarization` on later `uv sync` commands if you want to retain
that optional provider. If you installed ClipMind as a standalone uv tool,
update its separate tool environment instead:

```bash
uv tool install --force '.[diarization]'
```

The tool command does not install anything into the checkout's `.venv`.

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
On Windows, Chrome cookie decryption can fail even while Chrome itself plays a
video. Douyin also has a separate temporary, signed-out browser strategy, so
one cookie-read failure does not establish that your login expired. For Douyin,
`source_metadata_unavailable` means the configured acquisition routes did not
identify the requested item; the site or link may have changed, and repeated
login refreshes are not a reliable fix. A `/user/self?showTab=favorite_collection` page names a
collection, not one video; submit the video's share link or a URL with its
`modal_id`. Never send cookies or login credentials with a bug report.
To avoid reading the existing browser-cookie store while keeping the temporary
signed-out Douyin Chrome fallback enabled:

```dotenv
CLIPMIND_COOKIE_SOURCES=-
CLIPMIND_DOUYIN_BROWSER=1
```

The browser fallback is enabled by default and requires installed Chrome.
A custom launcher that sets `CLIPMIND_DOUYIN_BROWSER=0` disables it even if
Chrome is installed. Update that launcher as well as `.env`, then restart the
server to load the changed environment. The fallback still verifies the target
item ID and can fail; it does not substitute recommended content.

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

## Publish Python release assets

Publishing a GitHub Release is the explicit authorization boundary. The
`release assets` workflow checks out that release's tag, requires the tag to
match the version in `pyproject.toml`, and builds one wheel and one source
distribution. It installs the wheel with its dependencies in a clean virtual
environment outside the checkout, verifies the CLI and packaged web assets,
then attaches the two distributions and `SHA256SUMS` to the existing Release.

The workflow does not create a Release and does not upload the unsigned macOS
app or DMG.

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
binary and selected language data. If URL acquisition fails, read its error code
and the [input support matrix](INPUT_TYPES.md). Browser playback alone does not
prove the extraction path can acquire that item, and a metadata failure does not
prove cookies expired. Do not repeatedly sign in or export credentials for an
unsupported-source error. ClipMind does not bypass private,
removed, DRM-protected, or region-restricted media.
