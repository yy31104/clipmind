#!/usr/bin/env python3
"""Build and verify the Python artifacts attached to a GitHub Release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def project_identity(root: Path = ROOT) -> tuple[str, str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    return str(project["name"]), str(project["version"])


def validate_release_tag(tag: str | None, version: str) -> None:
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"release tag {tag!r} does not match package version {version!r}")


def write_checksums(files: list[Path], destination: Path) -> None:
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(files, key=lambda path: path.name)
    ]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clean_release_outputs(dist: Path) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.whl", "*.tar.gz", "SHA256SUMS"):
        for path in dist.glob(pattern):
            path.unlink()


def _built_distributions(dist: Path, version: str) -> list[Path]:
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("expected exactly one wheel and one source distribution")
    distributions = [wheels[0], sdists[0]]
    if any(version not in path.name for path in distributions):
        raise RuntimeError("built distribution filename does not contain package version")
    return distributions


def _verify_clean_install(wheel: Path, name: str, version: str) -> None:
    temp_root = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    with tempfile.TemporaryDirectory(
        prefix="clipmind-release-install-", dir=temp_root
    ) as tempdir:
        checkout_free = Path(tempdir)
        environment = checkout_free / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        clipmind = scripts / ("clipmind.exe" if os.name == "nt" else "clipmind")

        subprocess.run(
            [python, "-m", "pip", "install", str(wheel.resolve())],
            check=True,
            cwd=checkout_free,
        )
        subprocess.run([clipmind, "--help"], check=True, cwd=checkout_free)
        verification = (
            "import importlib.metadata, sys; "
            "from pathlib import Path; "
            "import clipmind; "
            "from clipmind.config import WEB_DIR; "
            "assert importlib.metadata.version(sys.argv[2]) == sys.argv[1]; "
            "assert Path(clipmind.__file__).resolve().is_relative_to("
            "Path(sys.prefix).resolve()); "
            "assert all((WEB_DIR / item).is_file() for item in "
            "('index.html', 'app.js', 'style.css'))"
        )
        subprocess.run(
            [python, "-c", verification, version, name],
            check=True,
            cwd=checkout_free,
        )


def build_release_assets(*, tag: str | None = None, root: Path = ROOT) -> dict:
    name, version = project_identity(root)
    validate_release_tag(tag, version)
    dist = root / "dist"
    _clean_release_outputs(dist)
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(dist)],
        check=True,
        cwd=root,
    )
    distributions = _built_distributions(dist, version)
    wheel = next(path for path in distributions if path.suffix == ".whl")
    _verify_clean_install(wheel, name, version)
    checksums = dist / "SHA256SUMS"
    write_checksums(distributions, checksums)
    result = {
        "name": name,
        "version": version,
        "tag": tag,
        "artifacts": [path.name for path in distributions] + [checksums.name],
    }
    print(json.dumps(result, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="require this release tag to equal v<version>")
    args = parser.parse_args()
    build_release_assets(tag=args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
