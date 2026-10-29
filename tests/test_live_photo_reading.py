"""LivePhoto reading uses unique visual positions and clip-relative motion times."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from PIL import Image

from clipmind import compact, evidence, render, visual_states
from clipmind.asr import Transcript
from clipmind.sdk import EvidencePack
from clipmind.sources.base import MediaAsset
from scripts import rebuild_preview
from tests.test_result_reading import APP, HARNESS


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class LivePhotoPackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Match the SDK's canonical path even when Windows TEMP is an 8.3 alias.
        self.root = Path(temporary.name).resolve()
        self.pack = self.root / "livephoto"
        all_dir = self.pack / "visual_states" / "all"
        all_dir.mkdir(parents=True)
        self.positions = [(0, None), (0, 0.0), (0, 0.5), (1, None), (1, 1.0)]
        self.frames = []
        for index, (page, motion) in enumerate(self.positions):
            extension = ".png" if motion is None else ".jpg"
            path = all_dir / f"state-{index}{extension}"
            Image.new("RGB", (48, 32), (index * 30, 60, 90)).save(path)
            text = "MOTION_HALF" if index == 2 else f"Visual state {index}"
            self.frames.append(compact.media.Frame(index, 0, path, text=text, lines=(text,),
                source_image_index=page, source_motion_timestamp=motion))
        preview = visual_states.materialize_preview(self.frames, self.pack / "visual_states" / "preview")
        item = MediaAsset(self.frames[0].path, {
            "id": "synthetic-livephoto", "title": "Synthetic motion lesson", "duration": 0,
            "webpage_url": "https://example.test/note/synthetic-livephoto", "_clipmind_platform": "fixture",
            "_clipmind_media_type": "image_collection", "_clipmind_audio_status": "not_acquired",
            "_clipmind_speech_status": "not_acquired", "_clipmind_visual_primary": True,
            "_clipmind_time_basis": "image_order", "_clipmind_source_image_count": 2,
            "_clipmind_source_motion_count": 2, "_clipmind_motion_sample_fps": 2,
            "_clipmind_motion_audio_status": "not_acquired", "_clipmind_motion_time_basis": "clip_relative_seconds",
        }, image_paths=tuple(frame.path for frame in self.frames if frame.source_motion_timestamp is None))
        manifest = evidence.write_pack(self.pack, item, Transcript([], engine="none"), self.frames, preview, [],
            candidate_frame_count=5, preview_algorithm="visual-only-primary-v1")
        self.metadata = render.write_all(self.pack, item, Transcript([], engine="none"), visual_states=self.frames,
            visual_preview=preview, evidence_manifest=manifest)
        (self.pack / "job.json").write_text(json.dumps({"version": 1, "job": {
            "id": "livephoto", "status": "done", "title": item.title, "result": self.metadata,
        }}), encoding="utf-8")

    def assert_provenance(self, path):
        for name in ("visual_timeline.jsonl", "ocr.jsonl"):
            records = rows(path / name)
            self.assertEqual([row["visual_state_index"] for row in records], list(range(5)))
            self.assertEqual([(row["source_image_index"], row.get("source_motion_timestamp")) for row in records], self.positions)
            self.assertTrue(all(row["time_basis"] == "image_order" and row["timestamp_available"] is False for row in records))

    def test_render_displays_original_pages_and_clip_motion_without_fake_global_time(self):
        for collection in ("visual_states", "visual_preview"):
            values = self.metadata[collection]
            self.assertEqual([row["visual_state_index"] for row in values], list(range(5)))
            self.assertEqual(values[0]["clock"], "第 1 张")
            self.assertEqual(values[1]["clock"], "第 1 张 · 动态 0 秒")
            self.assertEqual(values[2]["clock"], "第 1 张 · 动态 0.5 秒")
            self.assertTrue(all(row["timestamp"] == 0 and row["timestamp_available"] is False for row in values))
        self.assertEqual(self.metadata["source_image_count"], 2)
        self.assertEqual(self.metadata["source_motion_count"], 2)

    def test_compact_and_text_export_preserve_original_and_motion_provenance(self):
        before = snapshot(self.pack)
        result = compact.build_compact_pack(self.pack, self.root / "reading")
        reading = Path(result["path"])
        self.assertEqual(result["retained_count"], 5)
        self.assert_provenance(reading)
        for source_frame, record in zip(self.frames, rows(reading / "visual_timeline.jsonl")):
            self.assertEqual((reading / record["file"]).read_bytes(), source_frame.path.read_bytes())
        original_source = (self.pack / "source.json").read_bytes()
        self.assertEqual((reading / "source.json").read_bytes(), original_source)
        self.assertEqual(json.loads((reading / "metadata.json").read_text(encoding="utf-8"))["source_image_count"], 2)
        compact.build_compact_pack(self.pack, self.root / "text", image_policy="text")
        omitted = rows(self.root / "text" / "omitted_visual_text.jsonl")
        self.assertEqual([row["visual_state_index"] for row in omitted], list(range(5)))
        self.assertEqual([(row["source_image_index"], row.get("source_motion_timestamp")) for row in omitted], self.positions)
        hit = EvidencePack.open(self.root / "text").search("MOTION_HALF")[0]
        self.assertEqual(hit["visual_state_index"], 2)
        self.assertEqual(hit["source_motion_timestamp"], 0.5)
        self.assertFalse(hit["visual_available"])
        self.assertIsNone(hit["ref"])
        self.assertIsNone(hit["timestamp"])
        self.assertEqual(snapshot(self.pack), before)

    def test_sdk_selects_exact_motion_ref_and_prefers_original_for_a_page(self):
        pack = EvidencePack.open(self.pack)
        hit = pack.search("MOTION_HALF")[0]
        self.assertEqual(hit["ref"], "visual-00003")
        self.assertEqual(hit["visual_state_index"], 2)
        self.assertEqual(hit["source_image_index"], 0)
        self.assertEqual(hit["source_motion_timestamp"], 0.5)
        by_ref, record = pack.frame(visual_state_id=hit["ref"])
        self.assertEqual(by_ref, self.frames[2].path)
        self.assertEqual(record["source_motion_timestamp"], 0.5)
        timeline = pack.visual_timeline
        # A page lookup remains a lookup for its original image, even if an
        # imported timeline happens to list its motion states before the still.
        with patch.object(EvidencePack, "visual_timeline", new_callable=PropertyMock,
                          return_value=[timeline[1], timeline[2], timeline[0], *timeline[3:]]):
            original, _record = pack.frame(source_image_index=0)
        self.assertEqual(original, self.frames[0].path)
        with self.assertRaisesRegex(evidence.EvidencePackError, "not playback timestamps"):
            pack.frame(timestamp=0)

    def test_rebuild_and_ocr_refresh_preserve_all_same_page_motion_states(self):
        class Reader:
            def read_text(self, path):
                return ["Refreshed " + path.name]

        source = (self.pack / "source.json").read_bytes()
        original_images = {frame.path: frame.path.read_bytes() for frame in self.frames}
        with patch.object(rebuild_preview, "default_providers", return_value=SimpleNamespace(text=Reader())):
            self.assertEqual(rebuild_preview.rebuild(self.pack, refresh_ocr=True), 5)
        self.assert_provenance(self.pack)
        metadata = json.loads((self.pack / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["visual_preview"][2]["source_motion_timestamp"], 0.5)
        self.assertEqual(metadata["visual_preview"][2]["clock"], "第 1 张 · 动态 0.5 秒")
        self.assertEqual((self.pack / "source.json").read_bytes(), source)
        self.assertTrue(all(path.read_bytes() == data for path, data in original_images.items()))


@unittest.skipUnless(shutil.which("node"), "Node required")
class LivePhotoUITests(unittest.TestCase):
    def test_each_same_page_state_and_search_reference_opens_the_correct_image(self):
        body = r"""
  run(`const frames = [
    {visual_state_index:0,source_image_index:0,timestamp:0,file:'visual_states/all/original.png',text:'original'},
    {visual_state_index:1,source_image_index:0,source_motion_timestamp:0,timestamp:0,file:'visual_states/all/motion0.jpg',text:'motion zero'},
    {visual_state_index:2,source_image_index:0,source_motion_timestamp:0.5,timestamp:0,file:'visual_states/all/motion-half.jpg',text:'motion half'},
    {visual_state_index:3,source_image_index:1,timestamp:0,file:'visual_states/all/second.png',text:'second original'},
  ];
  state.jobs.set('live',{result:{media_type:'image_collection',visual_states:frames}});
  state.detail={job:{id:'live',transcript:[],result:{media_type:'image_collection',audio_status:'not_acquired'}},
    states:frames,frames,modernFrames:true};
  openFrameContext(0);`);
  assert.equal(element('frame-dialog-time').textContent,'第 1 张');
  assert.match(element('frame-dialog-image').src,/original\.png$/);
  run('openFrameContext(1)');
  assert.equal(element('frame-dialog-time').textContent,'第 1 张 · 动态 0 秒');
  run('openFrameContext(2)');
  assert.equal(element('frame-dialog-time').textContent,'第 1 张 · 动态 0.5 秒');
  assert.match(element('frame-dialog-image').src,/motion-half\.jpg$/);
  assert.equal(run(`searchHitPosition('live',{kind:'ocr',ref:'visual-00001',source_image_index:0})`),0);
  assert.equal(run(`searchHitPosition('live',{kind:'ocr',ref:'visual-00003',source_image_index:0,visual_state_index:1})`),2);
  assert.equal(run(`searchHitLabel('live',{kind:'ocr',ref:'visual-00003',source_image_index:0})`),'第 1 张 · 动态 0.5 秒');
  assert.equal(run(`searchHitPosition('live',{kind:'ocr',visual_available:false,visual_state_index:2,source_image_index:0})`),'');
  assert.equal(run(`visualPosition({timestamp:9})`),9);
"""
        result = subprocess.run(["node", "-e", HARNESS.replace("BODY", body), str(APP)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
