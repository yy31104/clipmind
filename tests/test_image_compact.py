"""Exports keep visual-only sources useful without inventing speech or timing."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from clipmind import compact, evidence
from clipmind.asr import Transcript
from clipmind.media import Frame
from clipmind.sources.base import MediaAsset


class ImageCompactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @staticmethod
    def rows(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    @staticmethod
    def snapshot(root):
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*") if path.is_file()}

    def make_pack(self, name="gallery", *, image_collection=True,
                  audio_status="not_acquired", speech_status="not_acquired",
                  asr_error=None, visual_primary=True):
        pack = self.root / name
        all_dir = pack / "visual_states" / "all"
        all_dir.mkdir(parents=True)
        (pack / "visual_states" / "preview").mkdir()
        formats = (("png", "PNG"), ("webp", "WEBP"), ("jpg", "JPEG"), ("tiff", "TIFF"), ("png", "PNG")) if image_collection else (("jpg", "JPEG"), ("jpg", "JPEG"))
        frames = []
        for index, (extension, image_format) in enumerate(formats):
            path = all_dir / f"page-{index:03d}.{extension}"
            if image_collection and index == 4:
                path.write_bytes(frames[0].path.read_bytes())
            else:
                image = Image.new("RGB", (240, 160), (30 + index * 10, 60, 100))
                ImageDraw.Draw(image).ellipse((45, 30, 130, 130), fill="tan")
                image.save(path, format=image_format)
            text = ("Overview", "Tools", "", "Risk", "Overview")[index] if image_collection else ""
            frames.append(Frame(index, 0.0 if image_collection else float(index), path,
                text=text, lines=(text,) if text else (),
                ocr_warning="fixture OCR unavailable" if image_collection and index == 2 else None,
                source_image_index=index if image_collection else None))
        info = {
            "id": name, "title": "Synthetic visual-only material", "duration": 0.0 if image_collection else 2.0,
            "webpage_url": f"https://example.test/{name}", "_clipmind_platform": "fixture",
            "_clipmind_strategy": "fixture", "_clipmind_audio_status": audio_status,
            "_clipmind_speech_status": speech_status, "_clipmind_visual_primary": visual_primary,
        }
        if image_collection:
            info.update(_clipmind_media_type="image_collection", _clipmind_time_basis="image_order")
        item = MediaAsset(pack / "not-retained.mp4", info,
                          image_paths=tuple(frame.path for frame in frames) if image_collection else ())
        manifest = evidence.write_pack(
            pack, item, Transcript([], engine="fixture", error=asr_error), frames, frames, [],
            candidate_frame_count=len(frames),
            ocr_error="OCR failed on 1/5 frames" if image_collection else None,
        )
        (pack / "job.json").write_text(json.dumps({"version": 1, "job": {
            "id": name, "title": info["title"], "url": info["webpage_url"],
            "status": "done", "stage": "done", "result": {"asr_engine": "fixture"},
        }}), encoding="utf-8")
        return pack, frames, manifest

    def test_native_image_pages_and_duplicate_pages_survive_content_pack_and_zip(self):
        pack, frames, parent = self.make_pack()
        before = self.snapshot(pack)
        result = compact.build_compact_pack(pack, self.root / "reading")
        derived = Path(result["path"])
        self.assertEqual(result["retained_count"], 5)
        timeline, ocr = self.rows(derived / "visual_timeline.jsonl"), self.rows(derived / "ocr.jsonl")
        self.assertEqual([row["source_image_index"] for row in timeline], list(range(5)))
        self.assertEqual([row["id"] for row in timeline], [f"visual-{index:05d}" for index in range(1, 6)])
        for index, row in enumerate(timeline):
            self.assertEqual((derived / row["file"]).read_bytes(), frames[index].path.read_bytes())
            self.assertEqual(row["time_basis"], "image_order")
            self.assertFalse(row["timestamp_available"])
            self.assertEqual(ocr[index]["source_image_index"], index)
            self.assertEqual(ocr[index]["visual_state_ref"], row["id"])
            self.assertEqual(ocr[index]["text"], frames[index].text)
        self.assertEqual(result["manifest"]["completeness"], parent["completeness"])
        for field in ("audio_status", "speech_status", "asr_error", "ocr_error", "ocr_failure_count"):
            self.assertEqual(result["manifest"]["diagnostics"][field], parent["diagnostics"][field])
        for name in ("source.json", "transcript.jsonl", "transcript.md"):
            self.assertEqual((derived / name).read_bytes(), (pack / name).read_bytes())
        archive_path = compact.export_compact_zip(pack, self.root / "gallery.zip")
        with zipfile.ZipFile(archive_path) as archive:
            for frame in frames:
                self.assertEqual(archive.read(f"{pack.name}/visual_states/all/{frame.path.name}"), frame.path.read_bytes())
        self.assertEqual(before, self.snapshot(pack))

    def test_text_export_keeps_ordered_ocr_and_errors_without_any_image_files(self):
        pack, frames, parent = self.make_pack()
        before = self.snapshot(pack)
        result = compact.build_compact_pack(pack, self.root / "text", image_policy="text")
        derived = Path(result["path"])
        self.assertEqual(result["retained_count"], 0)
        self.assertFalse(any(path.is_file() for path in (derived / "visual_states").rglob("*")))
        omitted = self.rows(derived / "omitted_visual_text.jsonl")
        self.assertEqual([row["source_image_index"] for row in omitted], list(range(5)))
        self.assertEqual([row["text"] for row in omitted], [frame.text for frame in frames])
        self.assertEqual(omitted[2]["ocr_error"], "fixture OCR unavailable")
        self.assertTrue(all(row["time_basis"] == "image_order" and row["timestamp_available"] is False for row in omitted))
        self.assertEqual(result["manifest"]["completeness"]["transcript"], "unavailable")
        for field in ("audio_status", "speech_status", "ocr_error", "ocr_failure_count"):
            self.assertEqual(result["manifest"]["diagnostics"][field], parent["diagnostics"][field])
        self.assertEqual((derived / "source.json").read_bytes(), (pack / "source.json").read_bytes())
        self.assertEqual(before, self.snapshot(pack))

    def test_visual_primary_videos_keep_photos_for_no_audio_and_no_speech(self):
        for audio, speech in (("absent", "no_audio_track"), ("present", "not_detected")):
            with self.subTest(audio=audio, speech=speech):
                pack, frames, parent = self.make_pack(audio, image_collection=False,
                    audio_status=audio, speech_status=speech)
                before = self.snapshot(pack)
                result = compact.build_compact_pack(pack, self.root / f"{audio}-reading")
                derived = Path(result["path"])
                self.assertEqual(result["retained_count"], 2)
                self.assertEqual(result["manifest"]["completeness"], parent["completeness"])
                for field in ("audio_status", "speech_status", "asr_error"):
                    self.assertEqual(result["manifest"]["diagnostics"][field], parent["diagnostics"][field])
                timeline = self.rows(derived / "visual_timeline.jsonl")
                self.assertEqual([row["start"] for row in timeline], [0.0, 1.0])
                self.assertTrue(all("source_image_index" not in row for row in timeline))
                self.assertEqual(before, self.snapshot(pack))

    def test_real_asr_failure_remains_a_failure_in_export_diagnostics_and_ui_metadata(self):
        pack, frames, parent = self.make_pack("asr-failed", image_collection=False,
            audio_status="present", speech_status="unavailable", asr_error="fixture CUDA unavailable", visual_primary=False)
        before = self.snapshot(pack)
        # Selection has separate real-image tests. This case isolates evidence
        # export when speech failed but the chosen visual evidence is usable.
        with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda values, **kwargs: values):
            result = compact.build_compact_pack(pack, self.root / "failed-reading")
        derived = Path(result["path"])
        metadata = json.loads((derived / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(result["manifest"]["completeness"]["transcript"], "unavailable")
        self.assertEqual(metadata["asr_error"], "fixture CUDA unavailable")
        self.assertEqual(metadata["audio_status"], "present")
        self.assertEqual(metadata["speech_status"], "unavailable")
        self.assertFalse(metadata["visual_primary"])
        self.assertEqual(before, self.snapshot(pack))


if __name__ == "__main__":
    unittest.main()
