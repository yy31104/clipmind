from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind.config import Settings
from clipmind.fetch import fetch
from clipmind.sources import adapter_for, supported_sources
from clipmind.sources import registry
from clipmind.sources.base import SourceAdapter


class SourceRegistryTests(unittest.TestCase):
    def test_specific_platforms_win_before_the_generic_url_adapter(self) -> None:
        cases = {
            "https://v.douyin.com/example": "douyin",
            "https://youtu.be/example": "youtube",
            "https://cdn.example.com/video.mp4": "web",
        }
        for url, platform in cases.items():
            with self.subTest(url=url):
                self.assertEqual(adapter_for(url).platform, platform)

    def test_local_file_has_its_own_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "demo.mov"
            source.write_bytes(b"media")
            adapter = adapter_for(str(source))

        self.assertTrue(adapter.local)
        self.assertEqual(adapter.platform, "local")

    def test_registry_exposes_contributor_facing_capabilities(self) -> None:
        platforms = {item["platform"] for item in supported_sources()}
        self.assertEqual(platforms, {"douyin", "youtube", "local"})

    def test_installed_adapter_entry_point_precedes_the_generic_url_fallback(self) -> None:
        plugin = SourceAdapter(
            name="example-plugin",
            platform="example",
            domains=("video.example",),
        )
        entry = type("Entry", (), {"name": "example", "load": lambda self: plugin})()
        with patch.object(registry.metadata, "entry_points", return_value=[entry]):
            registry.registered_adapters.cache_clear()
            try:
                selected = adapter_for("https://video.example/watch/1")
            finally:
                registry.registered_adapters.cache_clear()

        self.assertIs(selected, plugin)

    def test_broken_plugins_do_not_disable_builtin_sources(self) -> None:
        broken = type(
            "Entry",
            (),
            {"name": "broken", "load": lambda self: (_ for _ in ()).throw(RuntimeError("boom"))},
        )()
        incomplete = type(
            "Entry",
            (),
            {"name": "incomplete", "load": lambda self: object()},
        )()
        with patch.object(
            registry.metadata,
            "entry_points",
            return_value=[broken, incomplete],
        ), self.assertLogs(registry.logger, level="ERROR") as captured:
            registry.registered_adapters.cache_clear()
            try:
                selected = adapter_for("https://youtu.be/example")
                platforms = {item["platform"] for item in supported_sources()}
            finally:
                registry.registered_adapters.cache_clear()

        self.assertEqual(selected.platform, "youtube")
        self.assertIn("youtube", platforms)
        self.assertEqual(len(captured.output), 2)

    def test_plugin_match_failure_falls_through_to_the_generic_adapter(self) -> None:
        plugin = SourceAdapter(
            name="broken-match",
            platform="broken",
            domains=("video.example",),
        )
        object.__setattr__(
            plugin,
            "matches",
            lambda _source: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        entry = type("Entry", (), {"name": "broken-match", "load": lambda self: plugin})()
        with patch.object(registry.metadata, "entry_points", return_value=[entry]), \
                self.assertLogs(registry.logger, level="ERROR"):
            registry.registered_adapters.cache_clear()
            try:
                selected = adapter_for("https://video.example/watch/1")
            finally:
                registry.registered_adapters.cache_clear()

        self.assertEqual(selected.platform, "web")


class LocalSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_media_is_copied_and_normalized_without_touching_the_original(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "My Demo.mov"
            source.write_bytes(b"local media")
            workdir = root / "job"
            with patch("clipmind.fetch._probe_local", return_value={"duration": 12.5}):
                asset = await fetch(str(source), workdir, config=Settings())

            self.assertEqual(asset.platform, "local")
            self.assertEqual(asset.title, "My Demo")
            self.assertEqual(asset.duration, 12.5)
            self.assertEqual(asset.media_path.read_bytes(), b"local media")
            self.assertNotEqual(asset.media_path, source)
            self.assertTrue(source.exists())
            self.assertEqual(len(asset.source_id), 64)
            self.assertEqual(asset.webpage_url, "local:///My%20Demo.mov")
            self.assertNotIn(tempdir, asset.webpage_url)


if __name__ == "__main__":
    unittest.main()
