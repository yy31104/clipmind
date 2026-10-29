param(
    [ValidateSet('doctor', 'demo', 'serve', 'wechat-login')]
    [string] $Action = 'serve',
    [switch] $Deep
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$scripts = Join-Path $repo '.venv\Scripts'
$clipmind = Join-Path $scripts 'clipmind.exe'
if (-not (Test-Path -LiteralPath $clipmind)) {
    throw "ClipMind environment missing. Run 'uv sync --locked --python 3.12' in $repo first."
}

$env:PATH = "$scripts;$env:PATH"
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $ffmpeg = Get-ChildItem -Path $packages -Directory -Filter 'Gyan.FFmpeg_*' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem -Path $_.FullName -Recurse -File -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue } |
        Select-Object -First 1
    if ($ffmpeg) { $env:PATH = "$($ffmpeg.DirectoryName);$env:PATH" }
}
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    $tesseract = Join-Path $env:ProgramFiles 'Tesseract-OCR'
    if (Test-Path -LiteralPath (Join-Path $tesseract 'tesseract.exe')) {
        $env:PATH = "$tesseract;$env:PATH"
    }
}
if (-not $env:TESSDATA_PREFIX) {
    $languages = Join-Path $env:LOCALAPPDATA 'ClipMind\tessdata'
    if (Test-Path -LiteralPath $languages) { $env:TESSDATA_PREFIX = $languages }
}

Push-Location $repo
$serveMutex = $null
$ownsServeMutex = $false
try {
    if ($Action -eq 'serve') {
        # Repeated launches must not start a second queue and mark the first
        # server's running work as interrupted during startup recovery.
        $serveMutex = [System.Threading.Mutex]::new($false, 'Local\ClipMind.Loopback.8420')
        try { $ownsServeMutex = $serveMutex.WaitOne(0) }
        catch [System.Threading.AbandonedMutexException] { $ownsServeMutex = $true }
        if (-not $ownsServeMutex) {
            Write-Output 'ClipMind is already starting or running at http://127.0.0.1:8420/.'
            return
        }
        $existing = $null
        try {
            $existing = Invoke-RestMethod -Uri 'http://127.0.0.1:8420/openapi.json' -TimeoutSec 2
        } catch { }
        if ($existing.info.title -eq 'ClipMind') {
            Write-Output 'ClipMind is already running at http://127.0.0.1:8420/. Existing tasks were left running.'
            return
        }
        $listener = Get-NetTCPConnection -LocalPort 8420 -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            throw 'Port 8420 is already in use. ClipMind did not stop or replace the existing service.'
        }
        & $clipmind doctor
        if ($LASTEXITCODE -ne 0) { throw 'ClipMind doctor failed; fix the reported dependencies before serving.' }
        & $clipmind serve --host 127.0.0.1 --port 8420
    } elseif ($Action -eq 'doctor' -and $Deep) {
        & $clipmind doctor --deep
    } else {
        & $clipmind $Action
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    if ($ownsServeMutex) { $serveMutex.ReleaseMutex() }
    if ($serveMutex) { $serveMutex.Dispose() }
    Pop-Location
}
