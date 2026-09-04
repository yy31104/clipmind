from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind import evidence
from clipmind.index import EvidenceIndex


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class EvidenceIndexTests(unittest.TestCase):
    def make_pack(
        self,
        root: Path,
        *,
        name: str = "pack",
        title: str = "RAG architecture lesson",
        transcript: str = "vector retrieval pipeline",
    ) -> Path:
        pack = root / name
        pack.mkdir()
        for artifact in evidence.PACK_ARTIFACTS:
            path = pack / artifact
            if artifact.endswith("/"):
                path.mkdir(parents=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
        write_json(
            pack / "source.json",
            {
                "platform": "youtube",
                "source_id": "video-1",
                "title": title,
                "url": "https://youtu.be/video-1",
            },
        )
        (pack / "transcript.jsonl").write_text(
            json.dumps(
                {"id": "transcript-00001", "start": 12.0, "text": transcript}
            ) + "\n",
            encoding="utf-8",
        )
        (pack / "ocr.jsonl").write_text(
            json.dumps(
                {"id": "ocr-00001", "visual_state_ref": "visual-00001", "timestamp": 14.0, "text": "向量数据库"},
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        write_json(
            pack / "manifest.json",
            {
                "schema": {"name": evidence.SCHEMA_NAME, "version": evidence.SCHEMA_VERSION},
                "status": "complete",
            },
        )
        return pack

    def test_indexes_transcript_and_ocr_and_skips_unchanged_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = self.make_pack(root)
            index = EvidenceIndex(root / "index.sqlite3")

            self.assertTrue(index.sync("job-1", pack))
            self.assertFalse(index.sync("job-1", pack))
            english = index.search("retrieval")
            chinese = index.search("向量")

        self.assertEqual(english[0]["job_id"], "job-1")
        self.assertEqual(english[0]["hits"][0]["kind"], "transcript")
        self.assertEqual(chinese[0]["hits"][0]["ref"], "visual-00001")

    def test_partial_pack_is_never_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = root / "partial"
            pack.mkdir()
            index = EvidenceIndex(root / "index.sqlite3")

            self.assertFalse(index.sync("partial", pack))
            self.assertEqual(index.search("anything"), [])

    def test_search_returns_only_newest_pack_for_the_same_source(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            old = self.make_pack(root, name="old", transcript="shared old wording")
            new = self.make_pack(root, name="new", transcript="shared current wording")
            index = EvidenceIndex(root / "index.sqlite3")

            self.assertTrue(index.sync("old-job", old))
            self.assertTrue(index.sync("new-job", new))
            results = index.search("shared")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["job_id"], "new-job")
        self.assertEqual(results[0]["hits"][0]["text"], "shared current wording")

    def test_every_operation_closes_its_sqlite_connection(self) -> None:
        real_connect = sqlite3.connect
        opened: list[sqlite3.Connection] = []

        def tracking_connect(*args, **kwargs) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as tempdir, patch(
            "clipmind.index.sqlite3.connect", side_effect=tracking_connect
        ):
            root = Path(tempdir)
            pack = self.make_pack(root)
            index = EvidenceIndex(root / "index.sqlite3")
            self.assertTrue(index.sync("job-1", pack))
            index.search("retrieval")

        self.assertEqual(len(opened), 3)
        for connection in opened:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                connection.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
