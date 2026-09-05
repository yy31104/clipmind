from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit
from unittest.mock import patch

from clipmind import evidence
from clipmind.config import Settings
from clipmind.fetch import fetch
from clipmind.jobs import Job, JobStore
from clipmind.links import normalize_url, source_id_from_url
from clipmind.sources import adapter_for, supported_sources
from clipmind.sources import registry
from clipmind.sources.base import SourceAdapter
from tests.pack_fixture import make_complete_pack


class SourceRegistryTests(unittest.TestCase):
    def test_plugin_owned_identity_drives_wrappers_and_real_pack_reuse(self) -> None:
        class Plugin(SourceAdapter):
            def canonicalize_source(self, source):
                return "https://video.example/watch?clip=" + self.source_id(source)

            def source_id(self, source):
                return dict(parse_qsl(urlsplit(source).query)).get("clip")

        plugin = Plugin(name="example-plugin", platform="example", domains=("video.example",))
        entry = SimpleNamespace(name=plugin.name, load=lambda: plugin)
        with patch.object(registry.metadata, "entry_points", return_value=[entry]), \
                tempfile.TemporaryDirectory() as tempdir:
            registry.registered_adapters.cache_clear()
            try:
                root = Path(tempdir)
                pack = make_complete_pack(root, source_id="lesson-7")
                evidence.load_complete_pack(pack)
                store = JobStore(root)
                job = Job(
                    id=pack.name, url="https://video.example/watch?clip=lesson-7",
                    title="Fixture", status="done", result={"id": "lesson-7"},
                )
                store.jobs[job.id] = job
                url = "https://video.example/share?clip=lesson-7&tracking=discard"
                self.assertIs(adapter_for(url), plugin)
                self.assertEqual(normalize_url(url), job.url)
                self.assertEqual(source_id_from_url(url), "lesson-7")
                self.assertIs(store.reusable(url), job)
                self.assertIsNone(store.reusable(url.replace("lesson-7", "lesson-8")))
            finally:
                registry.registered_adapters.cache_clear()

    def test_identity_hooks_are_independently_optional_and_none_is_authoritative(self) -> None:
        adapter = SourceAdapter(name="example", platform="example", domains=("video.example",))
        plugin = SimpleNamespace(
            name=adapter.name, platform=adapter.platform, local=False, generic=False,
            matches=adapter.matches, normalize_info=adapter.normalize_info,
        )
        entry = SimpleNamespace(name=plugin.name, load=lambda: plugin)
        url = "https://video.example/video/123?p=7&utm_source=share"
        with patch.object(registry.metadata, "entry_points", return_value=[entry]):
            registry.registered_adapters.cache_clear()
            try:
                plugin.canonicalize_source = lambda source: "https://video.example/custom"
                self.assertEqual(normalize_url(url), "https://video.example/custom")
                self.assertEqual(source_id_from_url(url), "123")
                del plugin.canonicalize_source
                plugin.source_id = lambda source: None
                self.assertEqual(normalize_url(url), "https://video.example/video/123?p=7")
                self.assertIsNone(source_id_from_url(url))
            finally:
                registry.registered_adapters.cache_clear()

    def test_legacy_plugin_without_identity_hooks_keeps_generic_identity(self) -> None:
        class LegacyPlugin:
            name = "legacy-plugin"
            platform = "example"
            local = False
            generic = False

            def matches(self, source):
                return source.startswith("https://video.example/")

            def normalize_info(self, source, info):
                return {**info, "webpage_url": source}

        plugin = LegacyPlugin()
        entry = type("Entry", (), {"name": plugin.name, "load": lambda self: plugin})()
        self.assertFalse(hasattr(plugin, "canonicalize_source"))
        self.assertFalse(hasattr(plugin, "source_id"))
        with patch.object(registry.metadata, "entry_points", return_value=[entry]):
            registry.registered_adapters.cache_clear()
            try:
                for path, expected_id in (("watch", None), ("video/123", "123"), ("BV1abc", "BV1abc")):
                    url = f"https://video.example/{path}?z=2&p=7&utm_source=share"
                    with self.subTest(url=url):
                        self.assertIs(adapter_for(url), plugin)
                        self.assertEqual(normalize_url(url), f"https://video.example/{path}?p=7&z=2")
                        self.assertEqual(source_id_from_url(url), expected_id)
                self.assertIn(
                    {"name": plugin.name, "platform": plugin.platform}, supported_sources()
                )
            finally:
                registry.registered_adapters.cache_clear()

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
