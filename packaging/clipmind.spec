from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


# PyInstaller exposes SPECPATH as the directory containing this spec file.
root = Path(SPECPATH).parent
mlx_data, mlx_binaries, mlx_hidden = collect_all("mlx")
whisper_data, whisper_binaries, whisper_hidden = collect_all("mlx_whisper")
hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("clipmind.sources")
    + mlx_hidden
    + whisper_hidden
)

analysis = Analysis(
    [str(root / "packaging" / "desktop_entry.py")],
    pathex=[str(root)],
    binaries=[*mlx_binaries, *whisper_binaries],
    datas=[
        (str(root / "clipmind" / "web"), "clipmind/web"),
        *mlx_data,
        *whisper_data,
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ClipMind",
    console=False,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="ClipMind",
)
app = BUNDLE(
    collection,
    name="ClipMind.app",
    icon=None,
    bundle_identifier="dev.clipmind.app",
)
