"""Searchable text survives portable derivatives without inventing images."""
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clipmind.index import EvidenceIndex
from tests import test_index


class PortableIndexTests(unittest.TestCase):
    def test_newer_text_pack_does_not_hide_its_retained_screen_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = test_index.EvidenceIndexTests()
            full = fixture.make_pack(root, name="full")
            text = fixture.make_pack(root, name="text")
            (text / "ocr.jsonl").write_text("", encoding="utf-8")
            omitted = {"id": "visual-00004", "timestamp": 0, "text": "portable screen keyword",
                       "source_image_index": 3, "time_basis": "image_order", "timestamp_available": False}
            (text / "omitted_visual_text.jsonl").write_text(json.dumps(omitted) + "\n", encoding="utf-8")
            marker = text / "manifest.json"
            completion = json.loads(marker.read_text())
            completion["configuration"] = {"compact": {"profile": "compact-reading-v1"}}
            marker.write_text(json.dumps(completion), encoding="utf-8")
            newer = (full / "manifest.json").stat().st_mtime_ns + 1_000_000_000
            os.utime(marker, ns=(newer, newer))
            index = EvidenceIndex(root / "index.sqlite3")
            index.sync("full", full)
            index.sync("text", text)
            result = index.search("portable")[0]
            self.assertEqual(result["job_id"], "text")
            hit = result["hits"][0]
            self.assertEqual(hit["kind"], "ocr")
            self.assertIsNone(hit["ref"])
            self.assertIsNone(hit["timestamp"])
            self.assertEqual(hit["source_visual_ref"], "visual-00004")
            self.assertEqual(hit["source_image_index"], 3)
            self.assertFalse(hit["visual_available"])
            self.assertTrue(hit["omitted_visual"])
            self.assertFalse(hit["timestamp_available"])

    def test_regular_image_hit_keeps_ordinal_and_retrievable_visual_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = test_index.EvidenceIndexTests().make_pack(root)
            (pack / "ocr.jsonl").write_text(json.dumps({
                "id": "ocr-00002", "visual_state_ref": "visual-00002", "timestamp": 0,
                "text": "second page", "source_image_index": 1, "time_basis": "image_order",
                "timestamp_available": False,
            }) + "\n", encoding="utf-8")
            index = EvidenceIndex(root / "index.sqlite3")
            index.sync("gallery", pack)
            hit = index.search("second")[0]["hits"][0]
            self.assertEqual(hit["ref"], "visual-00002")
            self.assertEqual(hit["source_image_index"], 1)
            self.assertIsNone(hit["timestamp"])
            self.assertNotIn("visual_available", hit)

    def test_unclaimed_extra_file_is_not_indexed_as_canonical_ocr(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = test_index.EvidenceIndexTests().make_pack(root)
            (pack / "omitted_visual_text.jsonl").write_text('{"text":"unclaimed extra text"}\n', encoding="utf-8")
            index = EvidenceIndex(root / "index.sqlite3")
            index.sync("job", pack)
            self.assertEqual(index.search("unclaimed"), [])

    def test_previous_database_is_rebuildable_with_optional_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "index.sqlite3"
            with closing(sqlite3.connect(path)) as database:
                database.executescript("""
                    CREATE TABLE packs(job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                        title TEXT NOT NULL, platform TEXT NOT NULL, source_url TEXT);
                    CREATE TABLE documents(job_id TEXT NOT NULL, kind TEXT NOT NULL,
                        ref TEXT, timestamp REAL, text TEXT NOT NULL);
                """)
            index = EvidenceIndex(path)
            pack = test_index.EvidenceIndexTests().make_pack(root)
            self.assertTrue(index.sync("old-format", pack))
            self.assertEqual(index.search("向量")[0]["job_id"], "old-format")


if __name__ == "__main__":
    unittest.main()
