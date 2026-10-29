from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path

import clipmind
from clipmind import mcp
from scripts import build_release_assets

ROOT = Path(__file__).resolve().parents[1]


class ReleaseArtifactTests(unittest.TestCase):
    def test_source_release_cannot_omit_the_visual_gate_or_include_local_env(self):
        required = ("pyproject.toml", "requirements-test.txt", "scripts/evaluate_visual_quality.py",
                    "scripts/build_release_assets.py", "tests/test_visual_quality.py", "tests/pack_fixture.py",
                    "schemas/evidence-pack-v1.schema.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release.tar.gz"
            for names, valid in ((required, True), (required[:-1], False), ((*required, ".env"), False)):
                with tarfile.open(path, "w:gz") as archive:
                    for name in names:
                        entry = tarfile.TarInfo("clipmind/" + name)
                        entry.size = 1
                        archive.addfile(entry, io.BytesIO(b"x"))
                if valid:
                    build_release_assets.verify_source_distribution(path)
                else:
                    with self.assertRaises(RuntimeError):
                        build_release_assets.verify_source_distribution(path)

    def test_release_tag_must_match_the_package_version(self) -> None:
        build_release_assets.validate_release_tag("v1.2.1", "1.2.1")

        with self.assertRaisesRegex(ValueError, "does not match package version"):
            build_release_assets.validate_release_tag("v1.2.0", "1.2.1")

    def test_runtime_versions_match_the_package_version(self) -> None:
        # Package metadata is absent from frozen desktop builds, so the runtime
        # keeps a literal; this is what stops it drifting from pyproject again.
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(clipmind.__version__, project["project"]["version"])
        self.assertEqual(mcp.SERVER_INFO["version"], clipmind.__version__)

    def test_checksum_manifest_covers_only_the_built_distributions(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            wheel = root / "example.whl"
            sdist = root / "example.tar.gz"
            wheel.write_bytes(b"wheel")
            sdist.write_bytes(b"sdist")
            destination = root / "SHA256SUMS"

            build_release_assets.write_checksums([sdist, wheel], destination)

            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                "\n".join(
                    (
                        f"{hashlib.sha256(sdist.read_bytes()).hexdigest()}  {sdist.name}",
                        f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}",
                        "",
                    )
                ),
            )


if __name__ == "__main__":
    unittest.main()
