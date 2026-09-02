from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from clipmind import evidence, handoff


class HandoffTests(unittest.TestCase):
    def make_pack(self, root: Path) -> Path:
        pack = root / "job-123"
        (pack / "visual_states" / "all").mkdir(parents=True)
        (pack / "visual_states" / "preview").mkdir(parents=True)
        (pack / "visual_states" / "all" / "state.jpg").write_bytes(b"canonical")
        (pack / "visual_states" / "preview" / "state.jpg").write_bytes(b"preview")
        for name in (
            "job.json",
            "transcript.jsonl",
            "transcript.md",
            "ocr.jsonl",
            "visual_timeline.jsonl",
            "evidence.md",
        ):
            (pack / name).write_text(f"{name}\n", encoding="utf-8")
        (pack / "source.json").write_text(
            json.dumps({"platform": "douyin", "source_id": "123"}),
            encoding="utf-8",
        )
        (pack / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": {
                        "name": evidence.SCHEMA_NAME,
                        "version": evidence.SCHEMA_VERSION,
                    },
                    "source": {"platform": "douyin", "id": "123"},
                    "status": "complete",
                }
            ),
            encoding="utf-8",
        )
        (pack / "note.md").write_text("legacy", encoding="utf-8")
        (pack / "source.mp4").write_bytes(b"temporary media")
        return pack

    def test_zip_contains_only_canonical_contract_and_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = self.make_pack(root)
            first = handoff.export_zip(pack, root / "first.zip").read_bytes()
            second_path = handoff.export_zip(pack, root / "second.zip")
            second = second_path.read_bytes()
            with zipfile.ZipFile(second_path) as archive:
                names = set(archive.namelist())

        self.assertEqual(first, second)
        self.assertIn("job-123/manifest.json", names)
        self.assertIn("job-123/visual_states/all/state.jpg", names)
        self.assertIn("job-123/visual_states/preview/state.jpg", names)
        self.assertNotIn("job-123/note.md", names)
        self.assertNotIn("job-123/source.mp4", names)

    def test_inbox_copy_is_complete_idempotent_and_excludes_legacy_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = self.make_pack(root)
            inbox = root / "Inbox"

            first = handoff.send_to_inbox(pack, inbox)
            second = handoff.send_to_inbox(pack, inbox)
            delivered = inbox / pack.name

            self.assertEqual(first["status"], "sent")
            self.assertEqual(second["status"], "already_present")
            self.assertEqual(handoff.source_identity(delivered), ("douyin", "123"))
            self.assertEqual(
                handoff.load_complete_pack(delivered)["source"]["id"], "123"
            )
            self.assertFalse((delivered / "note.md").exists())
            self.assertFalse((delivered / "source.mp4").exists())

    def test_partial_pack_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pack = self.make_pack(Path(tempdir))
            (pack / "ocr.jsonl").unlink()

            with self.assertRaisesRegex(evidence.EvidencePackError, "ocr.jsonl"):
                handoff.export_zip(pack)


if __name__ == "__main__":
    unittest.main()
