"""Repairing OCR/previews must preserve source pages and their non-time order."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import render
from clipmind.asr import Transcript
from clipmind.sources.base import MediaAsset
from scripts import rebuild_preview
from tests import test_image_compact as fixtures


class ImageRebuildTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ImageCompactTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def make_pack(self, **kwargs):
        pack, frames, manifest = self.fixture.make_pack(**kwargs)
        source = json.loads((pack / "source.json").read_text(encoding="utf-8"))
        item = MediaAsset(pack / "no-source.mp4", {
            "id": source["source_id"], "title": source["title"], "duration": source["duration"],
            "_clipmind_media_type": source["media_type"],
            "_clipmind_audio_status": source["audio_status"],
            "_clipmind_speech_status": source["speech_status"],
            "_clipmind_visual_primary": source["visual_primary"],
        })
        metadata = render.write_all(pack, item, Transcript([]), visual_states=frames,
            visual_preview=frames, evidence_manifest=manifest)
        job = json.loads((pack / "job.json").read_text(encoding="utf-8"))
        job["job"]["result"] = metadata
        (pack / "job.json").write_text(json.dumps(job), encoding="utf-8")
        return pack, frames, manifest

    def protected(self, pack):
        return {name: digest for name, digest in self.fixture.snapshot(pack).items()
                if name in {"source.json", "transcript.jsonl", "transcript.md", "transcript.json"}
                or name.startswith("visual_states/all/")}

    def assert_page_order(self, pack):
        for name in ("ocr.jsonl", "visual_timeline.jsonl"):
            rows = self.fixture.rows(pack / name)
            self.assertEqual([row["source_image_index"] for row in rows], list(range(5)))
            self.assertTrue(all(row["time_basis"] == "image_order" and row["timestamp_available"] is False for row in rows))
        metadata = json.loads((pack / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual([row["source_image_index"] for row in metadata["visual_preview"]], list(range(5)))
        self.assertEqual([row["clock"] for row in metadata["visual_preview"]], [f"第 {index + 1} 张" for index in range(5)])

    def test_rebuild_keeps_native_and_repeated_image_pages_without_touching_ocr(self):
        pack, frames, _manifest = self.make_pack()
        before, original_ocr = self.protected(pack), (pack / "ocr.jsonl").read_bytes()
        self.assertEqual(rebuild_preview.rebuild(pack), 5)
        self.assert_page_order(pack)
        self.assertEqual((pack / "ocr.jsonl").read_bytes(), original_ocr)
        self.assertEqual(self.protected(pack), before)
        self.assertEqual({path.name for path in (pack / "visual_states" / "preview").iterdir()}, {frame.path.name for frame in frames})
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["configuration"]["preview_algorithm"], "visual-only-primary-v1")
        self.assertEqual(manifest["counts"]["progressive_build_groups"], 0)

    def test_refresh_ocr_preserves_image_ids_order_and_audio_absence_conclusions(self):
        class Reader:
            def read_text(self, path):
                return ["Refreshed " + path.name]

        pack, _frames, old_manifest = self.make_pack()
        before = self.protected(pack)
        with patch.object(rebuild_preview, "default_providers", return_value=SimpleNamespace(text=Reader())):
            self.assertEqual(rebuild_preview.rebuild(pack, refresh_ocr=True), 5)
        self.assert_page_order(pack)
        self.assertEqual(self.protected(pack), before)
        ocr = self.fixture.rows(pack / "ocr.jsonl")
        self.assertTrue(all(row["text"].startswith("Refreshed ") and "error" not in row for row in ocr))
        self.assertEqual([row["visual_state_ref"] for row in ocr], [f"visual-{index:05d}" for index in range(1, 6)])
        readable = (pack / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("## Image 5", readable)
        self.assertNotIn("## 00:00", readable)
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        for field in ("audio_status", "speech_status", "asr_error"):
            self.assertEqual(manifest["diagnostics"][field], old_manifest["diagnostics"][field])
        self.assertEqual(manifest["completeness"]["ocr"], "complete")
        metadata = json.loads((pack / "metadata.json").read_text(encoding="utf-8"))
        self.assertIsNone(metadata["evidence_pack"]["diagnostics"]["ocr_error"])

    def test_text_only_preview_is_explicit_and_reversible_without_losing_pages(self):
        pack, _frames, _manifest = self.make_pack()
        before = self.protected(pack)
        self.assertEqual(rebuild_preview.rebuild(pack, text_only=True), 0)
        self.assertEqual(self.protected(pack), before)
        self.assertEqual(rebuild_preview.rebuild(pack), 5)
        self.assert_page_order(pack)
        self.assertEqual(self.protected(pack), before)

    def test_silent_visual_primary_video_keeps_its_real_timed_frames(self):
        pack, _frames, _manifest = self.make_pack(image_collection=False,
            audio_status="absent", speech_status="no_audio_track")
        before = self.protected(pack)
        self.assertEqual(rebuild_preview.rebuild(pack), 2)
        timeline = self.fixture.rows(pack / "visual_timeline.jsonl")
        self.assertEqual([row["start"] for row in timeline], [0.0, 1.0])
        self.assertTrue(all("source_image_index" not in row for row in timeline))
        self.assertEqual(self.protected(pack), before)

    def test_failed_ocr_refresh_leaves_the_image_pack_completely_unchanged(self):
        class Reader:
            def read_text(self, path):
                raise RuntimeError("fixture OCR unavailable")

        pack, _frames, _manifest = self.make_pack()
        before = self.fixture.snapshot(pack)
        with patch.object(rebuild_preview, "default_providers", return_value=SimpleNamespace(text=Reader())):
            with self.assertRaises(RuntimeError):
                rebuild_preview.rebuild(pack, refresh_ocr=True)
        self.assertEqual(self.fixture.snapshot(pack), before)


if __name__ == "__main__":
    unittest.main()
