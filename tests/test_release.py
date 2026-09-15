from __future__ import annotations

import hashlib
import tempfile
import tomllib
import unittest
from pathlib import Path

import clipmind
from clipmind import mcp
from scripts import build_release_assets

ROOT = Path(__file__).resolve().parents[1]


class ReleaseArtifactTests(unittest.TestCase):
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
