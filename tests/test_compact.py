from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from clipmind import compact, evidence, handoff
from clipmind.asr import Segment, Transcript, Word
from clipmind.media import Frame
from clipmind.sdk import EvidencePack
from clipmind.sources.base import MediaAsset


class CompactExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows runner TEMP may use an 8.3 alias while exported paths are
        # resolved to their long name. Compare one canonical fixture root.
        self.root = Path(self.temp.name).resolve()
        self.pack = self.root / "original"
        self.make_pack()

    def make_pack(self):
        all_dir = self.pack / "visual_states" / "all"
        all_dir.mkdir(parents=True)
        (self.pack / "visual_states" / "preview").mkdir()
        frames = []
        for index, text in enumerate(("重复讲解内容", "短暂出现的文档数据", "async def load(): return client.fetch()")):
            path = all_dir / f"state-{index}.jpg"
            Image.effect_noise((240, 160), 80).convert("RGB").save(path, quality=90)
            frames.append(Frame(index, float(index), path, text=text, lines=(text,),
                ocr_layout=({"text": text, "confidence": .98, "bbox": [.1, .2, .5, .1]},)))
        speech = Transcript([Segment(0, 3, "重复讲解内容", (
            Word(0, 1, "重复", .95), Word(1, 3, "讲解内容", .91)), "speaker-1")], engine="fixture")
        item = MediaAsset(self.pack / "nonexistent.mp4", {
            "id": "fixture", "title": "Synthetic compact fixture", "duration": 3.,
            "uploader": "fixture", "webpage_url": "https://example.test/video/fixture",
            "_clipmind_platform": "generic", "_clipmind_strategy": "fixture"})
        evidence.write_pack(self.pack, item, speech, frames, [], [], candidate_frame_count=6,
                            timings={"asr_seconds": 12.3, "ocr_seconds": 23.4})
        # Byte preservation includes fields unknown to the current dataclasses.
        rows = self.rows(self.pack / "transcript.jsonl")
        rows[0]["future_field"] = {"literal": "保留原文", "number": 7}
        (self.pack / "transcript.jsonl").write_text(
            json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")
        self.write_json(self.pack / "job.json", {"version": 1, "job": {
            "id": "original", "url": "/private/retry.mp4", "title": item.title,
            "created_at": 10., "finished_at": 20., "status": "done", "stage": "done",
            "options": {"local_source_path": "/private/retry.mp4"},
            "result": {"asr_engine": "fixture"}}})

    def rows(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def snapshot(self, root=None):
        root = self.pack if root is None else root
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}

    def assert_references(self, pack):
        manifest = evidence.load_complete_pack(pack)
        timeline, ocr = self.rows(pack / "visual_timeline.jsonl"), self.rows(pack / "ocr.jsonl")
        by_id = {row["id"]: row for row in ocr}
        for row in timeline:
            self.assertTrue((pack / row["file"]).is_file())
            self.assertTrue((pack / row["preview_file"]).is_file())
            self.assertEqual(row["ocr_ref"], by_id[row["ocr_ref"]]["id"])
            self.assertEqual(row["id"], by_id[row["ocr_ref"]]["visual_state_ref"])
        job = json.loads((pack / "job.json").read_text(encoding="utf-8"))["job"]
        meta = json.loads((pack / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(job["result"], meta)
        for row in meta["visual_states"] + meta["visual_preview"]:
            self.assertTrue((pack / row["file"]).is_file())
        self.assertEqual(len(timeline), manifest["counts"]["canonical_visual_states"])
        self.assertEqual(len(ocr), len(timeline))
        self.assertEqual(job["transcript"], self.rows(pack / "transcript.jsonl"))

    def test_build_is_self_contained_preserves_asr_and_parent_and_has_no_duplicate_jpegs(self):
        before = self.snapshot()
        with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
            report = compact.build_compact_pack(self.pack, self.root / "reading")
        derived = Path(report["path"])
        self.assert_references(derived)
        self.assertEqual((report["original_count"], report["retained_count"], report["omitted_count"]), (3, 1, 2))
        for name in ("source.json", "transcript.jsonl", "transcript.md"):
            self.assertEqual((derived / name).read_bytes(), (self.pack / name).read_bytes())
        self.assertEqual(len(list((derived / "visual_states" / "all").glob("*.jpg"))), 1)
        self.assertEqual(list((derived / "visual_states" / "preview").iterdir()), [])
        loaded = EvidencePack.open(derived)
        self.assertTrue(loaded.frame(timestamp=2., preview=True)[0].is_file())
        manifest = report["manifest"]
        self.assertEqual(manifest["timings"], {"asr_seconds": 12.3, "ocr_seconds": 23.4})
        profile = manifest["configuration"]["compact"]
        self.assertEqual(profile["parent_manifest_sha256"], before["manifest.json"])
        self.assertEqual(profile["omitted_visual_states"], 2)
        self.assertEqual(self.rows(derived / "ocr.jsonl")[0]["layout"], self.rows(self.pack / "ocr.jsonl")[2]["layout"])
        omitted = self.rows(derived / "omitted_visual_text.jsonl")
        self.assertEqual([x["id"] for x in omitted], ["visual-00001", "visual-00002"])
        self.assertTrue(all(set(x) == {"id", "timestamp", "text", "lines", "selection_reason"} for x in omitted))
        self.assertEqual(before, self.snapshot())

    def test_zip_is_deterministic_smaller_and_never_overwrites_full_archive(self):
        full = handoff.export_zip(self.pack)
        full_bytes, before = full.read_bytes(), self.snapshot()
        with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
            first = compact.export_compact_zip(self.pack)
            first_bytes = first.read_bytes()
            second = compact.export_compact_zip(self.pack, self.root / "another.zip")
        self.assertEqual(first.name, "original.compact.evidence.zip")
        self.assertEqual(first_bytes, second.read_bytes())
        self.assertLess(len(first_bytes), len(full_bytes))
        self.assertEqual(full.read_bytes(), full_bytes)
        self.assertEqual(before, self.snapshot())
        with zipfile.ZipFile(first) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertEqual(len([n for n in names if n.endswith(".jpg")]), 1)
            for extra in compact.EXTRA_ARTIFACTS:
                self.assertIn("original/" + extra, names)
            exported = json.loads(archive.read("original/job.json"))["job"]
            self.assertEqual(exported["options"], {})
            self.assertNotIn("/private/", json.dumps(exported))
            self.assertEqual(exported["transcript"], self.rows(self.pack / "transcript.jsonl"))

    def test_recognition_uncertainty_does_not_force_photos(self):
        for failure in ("asr", "empty_asr", "ocr"):
            with self.subTest(failure=failure):
                original_manifest = (self.pack / "manifest.json").read_bytes()
                original_speech = (self.pack / "transcript.jsonl").read_bytes()
                original_ocr = (self.pack / "ocr.jsonl").read_bytes()
                manifest = json.loads(original_manifest)
                if failure == "asr":
                    manifest["completeness"]["transcript"] = "unavailable"
                    manifest["diagnostics"]["asr_error"] = "fixture failure"
                elif failure == "empty_asr":
                    (self.pack / "transcript.jsonl").write_text("", encoding="utf-8")
                else:
                    manifest["completeness"]["ocr"] = "partial"
                    records = self.rows(self.pack / "ocr.jsonl")
                    records[0]["error"] = "OCR failed; image retained"
                    (self.pack / "ocr.jsonl").write_text("".join(json.dumps(x)+"\n" for x in records), encoding="utf-8")
                self.write_json(self.pack / "manifest.json", manifest)
                before = self.snapshot()
                with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
                    result = compact.build_compact_pack(self.pack, self.root / failure)
                self.assertEqual(result["retained_count"], 1)
                self.assert_references(Path(result["path"]))
                self.assertTrue(result["manifest"]["configuration"]["compact"]["conservative_retention"])
                self.assertEqual(before, self.snapshot())
                (self.pack / "manifest.json").write_bytes(original_manifest)
                (self.pack / "transcript.jsonl").write_bytes(original_speech)
                (self.pack / "ocr.jsonl").write_bytes(original_ocr)

    def test_hash_failure_is_declared_but_is_not_proof_of_useful_content(self):
        real_hash = compact.media.dhash
        def unreliable(path):
            if path.name == "state-0.jpg":
                raise ValueError("fixture hash failure")
            return real_hash(path)
        with patch.object(compact.media, "dhash", side_effect=unreliable), patch.object(
                compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
            result = compact.build_compact_pack(self.pack, self.root / "warning")
        self.assertEqual(result["retained_count"], 1)
        self.assertIn("hash_uncertain", result["manifest"]["configuration"]["compact"]["conservative_retention"])

    def test_caption_only_selection_can_have_no_images_and_still_preserves_all_speech(self):
        # Exercise serialization of an empty selection, independently of heuristics.
        with patch.object(compact.visual_content, "select_content_frames", return_value=[]):
            result = compact.build_compact_pack(self.pack, self.root / "speech-only")
        derived = Path(result["path"])
        self.assert_references(derived)
        self.assertEqual(result["retained_count"], 0)
        self.assertEqual(len(self.rows(derived / "omitted_visual_text.jsonl")), 3)
        self.assertEqual((derived / "transcript.jsonl").read_bytes(), (self.pack / "transcript.jsonl").read_bytes())

    def test_explicit_text_only_has_zero_images_even_when_the_source_has_code(self):
        before = self.snapshot()
        result = compact.build_compact_pack(self.pack, self.root / "explicit-text", image_policy="text")
        derived = Path(result["path"])
        self.assertEqual(result["retained_count"], 0)
        self.assert_references(derived)
        self.assertEqual(list((derived / "visual_states").rglob("*.jpg")), [])
        self.assertEqual((derived / "transcript.jsonl").read_bytes(), (self.pack / "transcript.jsonl").read_bytes())
        self.assertEqual(len(self.rows(derived / "omitted_visual_text.jsonl")), 3)
        self.assertEqual(before, self.snapshot())

    def test_unsafe_or_existing_destinations_and_outside_image_reference_are_refused(self):
        before = self.snapshot()
        for output in (self.pack, self.pack / "derived", self.root):
            with self.subTest(output=output), self.assertRaises((evidence.EvidencePackError, FileExistsError)):
                compact.build_compact_pack(self.pack, output)
        with self.assertRaises(evidence.EvidencePackError):
            compact.export_compact_zip(self.pack, self.pack.with_suffix(".evidence.zip"))
        self.assertEqual(before, self.snapshot())
        rows = self.rows(self.pack / "visual_timeline.jsonl")
        rows[0]["file"] = "../outside.jpg"
        (self.pack / "visual_timeline.jsonl").write_text("".join(json.dumps(x)+"\n" for x in rows), encoding="utf-8")
        with self.assertRaises(evidence.EvidencePackError):
            compact.build_compact_pack(self.pack, self.root / "invalid")
        self.assertFalse((self.root / "invalid").exists())

    def test_bad_json_shapes_and_missing_input_fields_are_pack_errors(self):
        cases = (
            ("source.json", b"[1,2]"),
            ("source.json", b"{invalid"),
            ("source.json", b'{}'),
            ("transcript.jsonl", b"not-json\n"),
            ("transcript.jsonl", b"[]\n"),
            ("transcript.jsonl", b'{"start":0,"text":"missing end"}\n'),
            ("visual_timeline.jsonl", b"{}\n"),
            ("manifest.json", b'{"schema":[],"configuration":{}}'),
        )
        for index, (name, data) in enumerate(cases):
            with self.subTest(name=name, data=data):
                path = self.pack / name
                original = path.read_bytes()
                path.write_bytes(data)
                output = self.root / f"bad-{index}"
                with self.assertRaises(evidence.EvidencePackError):
                    compact.build_compact_pack(self.pack, output)
                self.assertFalse(output.exists())
                self.assertEqual(path.read_bytes(), data)
                path.write_bytes(original)

    def test_recompact_is_explicitly_refused_without_losing_omitted_text(self):
        with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
            report = compact.build_compact_pack(self.pack, self.root / "reading")
        derived = Path(report["path"])
        omitted = (derived / "omitted_visual_text.jsonl").read_bytes()
        artifacts = report["manifest"]["artifacts"]
        self.assertEqual(len(artifacts), len(set(artifacts)))
        with self.assertRaisesRegex(evidence.EvidencePackError, "already compact"):
            compact.build_compact_pack(derived, self.root / "again")
        with self.assertRaisesRegex(evidence.EvidencePackError, "already compact"):
            compact.export_compact_zip(derived)
        self.assertFalse((self.root / "again").exists())
        self.assertFalse(derived.with_suffix(".compact.evidence.zip").exists())
        self.assertEqual((derived / "omitted_visual_text.jsonl").read_bytes(), omitted)

    def test_compact_full_export_and_inbox_roundtrip_preserve_omitted_text(self):
        with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
            result = compact.build_compact_pack(self.pack, self.root / "reading")
        derived = Path(result["path"])
        archive_path = handoff.export_zip(derived, self.root / "roundtrip.zip")
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            for extra in compact.EXTRA_ARTIFACTS:
                self.assertEqual(archive.read(f"reading/{extra}"), (derived / extra).read_bytes())
        receipt = handoff.send_to_inbox(derived, self.root / "inbox")
        copied = Path(receipt["destination"])
        self.assert_references(copied)
        for extra in compact.EXTRA_ARTIFACTS:
            self.assertEqual((copied / extra).read_bytes(), (derived / extra).read_bytes())
        second = handoff.export_zip(copied, self.root / "inbox-roundtrip.zip")
        self.assertEqual(archive_path.read_bytes(), second.read_bytes())

    def test_full_and_compact_inbox_collisions_never_claim_complete_delivery(self):
        with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
            result = compact.build_compact_pack(self.pack, self.root / "content" / self.pack.name)
        reading = Path(result["path"])
        originals = {self.pack: self.snapshot(), reading: self.snapshot(reading)}
        for name, first, incoming in (("compact-first", reading, self.pack),
                                      ("full-first", self.pack, reading)):
            with self.subTest(name=name):
                inbox = self.root / name
                receipt = handoff.send_to_inbox(first, inbox)
                existing = Path(receipt["destination"])
                before = self.snapshot(existing)
                with self.assertRaisesRegex(evidence.EvidencePackError, "different Evidence Pack variant"):
                    handoff.send_to_inbox(incoming, inbox)
                self.assertEqual(self.snapshot(existing), before)
                self.assertEqual(list(inbox.iterdir()), [existing])
                for path, hashes in originals.items():
                    self.assertEqual(self.snapshot(path), hashes)

    def test_content_and_text_inbox_variants_conflict_but_identical_compact_is_idempotent(self):
        with patch.object(compact.visual_content, "select_content_frames", side_effect=lambda f, **kw: [f[-1]]):
            content = Path(compact.build_compact_pack(
                self.pack, self.root / "content" / self.pack.name)["path"])
        text = Path(compact.build_compact_pack(
            self.pack, self.root / "text" / self.pack.name, image_policy="text")["path"])
        originals = {self.pack: self.snapshot(), content: self.snapshot(content), text: self.snapshot(text)}
        for name, first, incoming in (("content-first", content, text), ("text-first", text, content)):
            with self.subTest(name=name):
                inbox = self.root / name
                receipt = handoff.send_to_inbox(first, inbox)
                existing = Path(receipt["destination"])
                before = self.snapshot(existing)
                duplicate = handoff.send_to_inbox(first, inbox)
                self.assertEqual(duplicate, {"status": "already_present", "destination": str(existing)})
                self.assertEqual(self.snapshot(existing), before)
                with self.assertRaisesRegex(evidence.EvidencePackError, "different Evidence Pack variant"):
                    handoff.send_to_inbox(incoming, inbox)
                self.assertEqual(self.snapshot(existing), before)
                self.assertEqual(list(inbox.iterdir()), [existing])
                for path, hashes in originals.items():
                    self.assertEqual(self.snapshot(path), hashes)

    def test_real_selector_keeps_two_similar_white_plots_with_empty_ocr(self):
        timeline = self.rows(self.pack / "visual_timeline.jsonl")[:2]
        records = self.rows(self.pack / "ocr.jsonl")[:2]
        for index, row in enumerate(timeline):
            image = Image.new("RGB", (240, 160), "white")
            drawing = ImageDraw.Draw(image)
            drawing.line([(20, 10), (20, 140), (225, 140)], fill="black", width=2)
            drawing.line([(20, 130), (90, 100), (150, 65), (220, 30 + index)], fill="blue", width=2)
            image.save(self.pack / row["file"], quality=95)
        (self.pack / "visual_states" / "all" / "state-2.jpg").unlink()
        for row in records:
            row.update(text="", lines=[], layout=[])
        for name, rows in (("visual_timeline.jsonl", timeline), ("ocr.jsonl", records)):
            (self.pack / name).write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
        manifest = json.loads((self.pack / "manifest.json").read_text(encoding="utf-8"))
        manifest["counts"].update(canonical_visual_states=2, ocr_records=2)
        self.write_json(self.pack / "manifest.json", manifest)
        before = self.snapshot()
        result = compact.build_compact_pack(self.pack, self.root / "plots")
        self.assertEqual(result["retained_count"], 2)
        self.assert_references(Path(result["path"]))
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
