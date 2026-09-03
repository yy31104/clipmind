from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH).parent.parent
hidden = collect_submodules("uvicorn") + collect_submodules("clipmind.sources")

analysis = Analysis(
    [str(root / "packaging" / "desktop_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "clipmind" / "web"), "clipmind/web")],
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
