"""Portable speech and image-page navigation, using synthetic local packs."""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from clipmind import compact, handoff, server
from clipmind.evidence import EvidencePackError
from clipmind.sdk import EvidencePack
from tests.pack_fixture import make_complete_pack


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class PortableTranscriptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pack = make_complete_pack(self.root)

    async def detail(self, workdir=None):
        workdir = workdir or self.pack
        store = SimpleNamespace(
            jobs={workdir.name: SimpleNamespace(public=lambda: {"id": workdir.name})},
            workdir=lambda job_id: workdir,
        )
        with patch.object(server, "store", store):
            return await server.detail(workdir.name)

    async def test_full_zip_roundtrip_keeps_canonical_speech_without_compatibility_json(self):
        speech = [{"id": "transcript-00001", "start": 1, "end": 2, "text": "Portable speech",
                   "words": [{"start": 1, "end": 2, "text": "Portable", "probability": 0.8}],
                   "future_field": {"retained": True}}]
        write_rows(self.pack / "transcript.jsonl", speech)
        archive = handoff.export_zip(self.pack, self.root / "full.zip")
        restored = self.root / "restored"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(restored)  # Only the synthetic pack made above.
        transferred = restored / self.pack.name
        self.assertFalse((transferred / "transcript.json").exists())
        result = await self.detail(transferred)
        self.assertEqual(result["transcript"], speech)

    async def test_canonical_transcript_wins_over_stale_legacy_copy(self):
        (self.pack / "transcript.json").write_text('[{"text":"stale legacy speech"}]', encoding="utf-8")
        result = await self.detail()
        self.assertEqual(result["transcript"][0]["text"], "vector retrieval pipeline")

    async def test_empty_canonical_transcript_does_not_resurrect_old_speech(self):
        (self.pack / "transcript.jsonl").write_text("\n", encoding="utf-8")
        (self.pack / "transcript.json").write_text('[{"text":"stale legacy speech"}]', encoding="utf-8")
        self.assertEqual((await self.detail())["transcript"], [])

    async def test_excessively_nested_transcript_returns_actionable_error(self):
        (self.pack / "transcript.jsonl").write_text("[" * 10000 + "0" + "]" * 10000, encoding="utf-8")
        with self.assertRaises(HTTPException) as caught:
            await self.detail()
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("transcript.jsonl", caught.exception.detail)

    async def test_legacy_only_transcript_remains_readable(self):
        (self.pack / "transcript.jsonl").unlink()
        speech = [{"start": 1, "end": 2, "text": "Legacy speech"}]
        (self.pack / "transcript.json").write_text(json.dumps(speech), encoding="utf-8")
        self.assertEqual((await self.detail())["transcript"], speech)

    async def test_missing_transcripts_remain_empty(self):
        (self.pack / "transcript.jsonl").unlink()
        self.assertEqual((await self.detail())["transcript"], [])

    async def test_corrupt_canonical_transcript_is_explicit_error_not_legacy_fallback(self):
        (self.pack / "transcript.json").write_text('[{"text":"stale speech"}]', encoding="utf-8")
        for invalid in (b"not-json\n", b"[]\n", b"\xff\xfe"):
            with self.subTest(invalid=invalid):
                (self.pack / "transcript.jsonl").write_bytes(invalid)
                with self.assertRaises(HTTPException) as raised:
                    await self.detail()
                self.assertEqual(raised.exception.status_code, 422)
                self.assertIn("transcript.jsonl", raised.exception.detail)

    async def test_invalid_legacy_shape_is_explicit_error(self):
        (self.pack / "transcript.jsonl").unlink()
        (self.pack / "transcript.json").write_text('{"text":"not a segment list"}', encoding="utf-8")
        with self.assertRaises(HTTPException) as raised:
            await self.detail()
        self.assertEqual(raised.exception.status_code, 422)


class ImagePageSDKTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = make_complete_pack(self.root, title="Synthetic image lesson")
        source = json.loads((self.path / "source.json").read_text(encoding="utf-8"))
        source.update(media_type="image_collection", duration=0, duration_available=False,
                      time_basis="image_order", visual_primary=True, audio_status="not_acquired")
        (self.path / "source.json").write_text(json.dumps(source), encoding="utf-8")
        (self.path / "transcript.jsonl").write_text("", encoding="utf-8")
        ocr, timeline = [], []
        for index in range(3):
            name = f"image-{index + 1:05d}.png"
            image = Image.new("RGB", (32, 24), (index * 100, 30, 60))
            for directory in ("all", "preview"):
                image.save(self.path / "visual_states" / directory / name)
            text = "SECOND_PAGE_NEEDLE" if index == 1 else f"Unrelated page {index}"
            position = {"source_image_index": index, "time_basis": "image_order", "timestamp_available": False}
            ocr.append({"id": f"ocr-{index + 1:05d}", "visual_state_ref": f"visual-{index + 1:05d}",
                        "timestamp": 0, "text": text, "lines": [text], **position})
            timeline.append({"id": f"visual-{index + 1:05d}", "start": 0, "end": 0,
                             "file": f"visual_states/all/{name}", "preview_file": f"visual_states/preview/{name}",
                             "ocr_ref": ocr[-1]["id"], "transcript_refs": [], "in_preview": True, **position})
        write_rows(self.path / "ocr.jsonl", ocr)
        write_rows(self.path / "visual_timeline.jsonl", timeline)
        self.pack = EvidencePack.open(self.path)

    async def test_ocr_search_hit_opens_exact_image_page_by_ref_and_source_order(self):
        hit = self.pack.search("SECOND_PAGE_NEEDLE")[0]
        self.assertEqual(hit["ref"], "visual-00002")
        self.assertEqual(hit["source_image_index"], 1)
        self.assertEqual(hit["time_basis"], "image_order")
        self.assertFalse(hit["timestamp_available"])
        self.assertIsNone(hit["timestamp"])
        by_ref, record = self.pack.frame(visual_state_id=hit["ref"])
        by_index, _ = self.pack.frame(source_image_index=hit["source_image_index"])
        self.assertEqual(by_ref, by_index)
        self.assertEqual(by_ref.name, "image-00002.png")
        self.assertEqual(record["source_image_index"], 1)
        with patch.object(server, "_pack", return_value=self.pack):
            response = await server.pack_frame(self.pack.id, source_image_index=1)
        self.assertEqual(Path(response.path), by_ref)

    async def test_image_time_lookup_never_silently_returns_first_page(self):
        with self.assertRaisesRegex(EvidencePackError, "image order, not playback timestamps"):
            self.pack.frame(timestamp=0)
        for invalid in (-1, True, 1.0, 999):
            with self.subTest(invalid=invalid), self.assertRaises(EvidencePackError):
                self.pack.frame(source_image_index=invalid)

    async def test_existing_video_ocr_search_and_timestamp_lookup_remain_compatible(self):
        video = EvidencePack.open(make_complete_pack(self.root, name="legacy-video"))
        hit = video.search("向量")[0]
        self.assertEqual(hit["ref"], "visual-00001")
        self.assertEqual(hit["timestamp"], 2)
        self.assertNotIn("source_image_index", hit)
        by_ref, _ = video.frame(visual_state_id=hit["ref"])
        by_time, _ = video.frame(timestamp=3)
        self.assertEqual(by_ref, by_time)

    async def test_text_derivative_searches_omitted_ocr_without_inventing_images(self):
        target = self.root / "text-reading"
        compact.build_compact_pack(self.path, target, image_policy="text")
        reading = EvidencePack.open(target)
        self.assertEqual(reading.ocr, [])
        self.assertEqual(reading.visual_timeline, [])
        hit = reading.search("SECOND_PAGE_NEEDLE")[0]
        self.assertEqual(hit["kind"], "ocr")
        self.assertTrue(hit["omitted_visual"])
        self.assertFalse(hit["visual_available"])
        self.assertIsNone(hit["ref"])
        self.assertIsNone(hit["timestamp"])
        self.assertEqual(hit["source_visual_ref"], "visual-00002")
        self.assertEqual(hit["source_image_index"], 1)
        self.assertEqual(hit["time_basis"], "image_order")
        self.assertFalse(hit["timestamp_available"])

    async def test_unrecognized_supplement_is_never_searched(self):
        (self.path / "omitted_visual_text.jsonl").write_text("not-json", encoding="utf-8")
        self.assertEqual(self.pack.search("ABSENT_NEEDLE"), [])

    async def test_declared_missing_supplement_reports_invalid_pack(self):
        manifest = {**self.pack.manifest, "configuration": {"compact": {"profile": "compact-reading-v1"}}}
        reading = EvidencePack(self.path, manifest)
        with self.assertRaisesRegex(EvidencePackError, "omitted_visual_text.jsonl"):
            reading.search("SECOND_PAGE_NEEDLE")

    async def test_malformed_profile_reports_pack_error(self):
        for configuration in ("not-an-object", {"compact": "not-an-object"}):
            with self.subTest(configuration=configuration):
                reading = EvidencePack(self.path, {**self.pack.manifest, "configuration": configuration})
                with self.assertRaises(EvidencePackError):
                    reading.search("SECOND_PAGE_NEEDLE")


if __name__ == "__main__":
    unittest.main()
