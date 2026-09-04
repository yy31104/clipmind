"""The generated sample and the routing that lets `clipmind demo` reach it."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from clipmind import cli, demo
from clipmind.links import extract_sources


# Rendering the sample shells out to ffmpeg. The unit suite stays hermetic and
# runs on machines without it; `make eval-synthetic` covers the real render.
needs_ffmpeg = unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg/ffprobe not installed",
)


@needs_ffmpeg
class SampleVideoTests(unittest.TestCase):
    def test_the_sample_is_a_real_decodable_video(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = demo.build_sample_video(Path(tempdir))

            self.assertTrue(video.is_file())
            self.assertGreater(video.stat().st_size, 1024)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                 "-of", "default=noprint_wrappers=1", str(video)],
                capture_output=True, text=True, check=True,
            )
            self.assertIn("codec_type=video", probe.stdout)

    def test_the_sample_is_a_source_the_pipeline_accepts(self) -> None:
        """A demo that produces something ingestion rejects is worthless."""
        with tempfile.TemporaryDirectory() as tempdir:
            video = demo.build_sample_video(Path(tempdir))

            self.assertEqual(extract_sources(str(video)), [str(video.resolve())])


class CommandRoutingTests(unittest.TestCase):
    """A bare link means "analyze"; a subcommand must never be swallowed by it."""

    def _routed(self, argument: str) -> str:
        # The shipped routing, not a copy of it.
        return cli.normalize_argv([argument])[0]

    def test_subcommands_are_not_rewritten_to_analyze(self) -> None:
        parser = cli._parser()
        subcommands = [
            action.choices
            for action in parser._actions
            if hasattr(action, "choices") and isinstance(action.choices, dict)
        ]
        self.assertTrue(subcommands, "the parser exposes no subcommands")
        for name in subcommands[0]:
            with self.subTest(command=name):
                self.assertEqual(self._routed(name), name)

    def test_a_link_is_routed_to_analyze(self) -> None:
        self.assertEqual(self._routed("https://youtu.be/jNQXAC9IVRw"), "analyze")

    def test_an_existing_media_path_is_routed_to_analyze(self) -> None:
        # Routing only asks whether the path exists, so this needs no encoder.
        with tempfile.TemporaryDirectory() as tempdir:
            media = Path(tempdir) / "clip.mp4"
            media.write_bytes(b"not a real encode")

            self.assertEqual(self._routed(str(media)), "analyze")


if __name__ == "__main__":
    unittest.main()
