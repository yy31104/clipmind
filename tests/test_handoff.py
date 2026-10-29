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
            "transcript.jsonl",
            "transcript.md",
            "ocr.jsonl",
            "visual_timeline.jsonl",
            "evidence.md",
        ):
            (pack / name).write_text(f"{name}\n", encoding="utf-8")
        (pack / "source.json").write_text(
            json.dumps(
                {
                    "platform": "local",
                    "source_id": "123",
                    "url": "local:///private-demo.mp4",
                }
            ),
            encoding="utf-8",
        )
        (pack / "job.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "job": {
                        "id": "job-123",
                        "url": "/Users/private/secret/private-demo.mp4",
                        "title": "private-demo",
                        "status": "done",
                        "options": {
                            "uploaded_filename": "private-demo.mp4",
                            "local_source_path": "/Users/private/secret/private-demo.mp4",
                        },
                        "result": {
                            "url": "file:///Users/private/secret/private-demo.mp4"
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        (pack / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": {
                        "name": evidence.SCHEMA_NAME,
                        "version": evidence.SCHEMA_VERSION,
                    },
                    "source": {"platform": "local", "id": "123"},
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
                exported_job = json.loads(archive.read("job-123/job.json"))

        self.assertEqual(first, second)
        self.assertIn("job-123/manifest.json", names)
        self.assertIn("job-123/visual_states/all/state.jpg", names)
        self.assertIn("job-123/visual_states/preview/state.jpg", names)
        self.assertNotIn("job-123/note.md", names)
        self.assertNotIn("job-123/source.mp4", names)
        self.assertEqual(exported_job["job"]["url"], "local:///private-demo.mp4")
        self.assertEqual(exported_job["job"]["options"], {})
        self.assertNotIn("/Users/private", json.dumps(exported_job))

    def test_inbox_copy_is_complete_idempotent_and_excludes_legacy_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = self.make_pack(root)
            inbox = root / "Inbox"

            first = handoff.send_to_inbox(pack, inbox)
            second = handoff.send_to_inbox(pack, inbox)
            delivered = inbox / pack.name
            delivered_job = json.loads(
                (delivered / "job.json").read_text(encoding="utf-8")
            )

            self.assertEqual(first["status"], "sent")
            self.assertEqual(second["status"], "already_present")
            self.assertEqual(handoff.source_identity(delivered), ("local", "123"))
            self.assertEqual(
                handoff.load_complete_pack(delivered)["source"]["id"], "123"
            )
            self.assertNotIn("/Users/private", json.dumps(delivered_job))
            self.assertFalse((delivered / "note.md").exists())
            self.assertFalse((delivered / "source.mp4").exists())

    def test_partial_pack_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pack = self.make_pack(Path(tempdir))
            (pack / "ocr.jsonl").unlink()

            with self.assertRaisesRegex(evidence.EvidencePackError, "ocr.jsonl"):
                handoff.export_zip(pack)

    def test_zip_output_cannot_replace_source_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = self.make_pack(root)
            before = {path.relative_to(pack): path.read_bytes()
                      for path in pack.rglob("*") if path.is_file()}
            for destination in (pack, pack / "manifest.json",
                                pack / "visual_states" / "all" / "state.jpg",
                                root / "unused" / ".." / pack.name / "transcript.md"):
                with self.subTest(destination=destination.name):
                    with self.assertRaisesRegex(evidence.EvidencePackError, "outside"):
                        handoff.export_zip(pack, destination)
            self.assertEqual(before, {path.relative_to(pack): path.read_bytes()
                                     for path in pack.rglob("*") if path.is_file()})
            self.assertFalse((root / "unused").exists())

    def test_zip_output_alias_cannot_replace_source_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = self.make_pack(root)
            original = (pack / "manifest.json").read_bytes()
            alias = root / "output.zip"
            try:
                alias.symlink_to(pack / "manifest.json")
            except (OSError, NotImplementedError):
                self.skipTest("Symbolic links unavailable")
            with self.assertRaisesRegex(evidence.EvidencePackError, "outside"):
                handoff.export_zip(pack, alias)
            self.assertEqual(original, (pack / "manifest.json").read_bytes())
            self.assertTrue(alias.is_symlink())

    def test_revised_pack_is_not_reported_as_already_delivered(self) -> None:
        for artifact in ("transcript.jsonl", "ocr.jsonl", "visual_states/all/state.jpg"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                pack = self.make_pack(root)
                inbox = root / "Inbox"
                handoff.send_to_inbox(pack, inbox)
                delivered = inbox / pack.name
                before = {path.relative_to(delivered): path.read_bytes()
                          for path in delivered.rglob("*") if path.is_file()}
                changed = pack / artifact
                changed.write_bytes(b"repaired evidence")
                with self.assertRaisesRegex(evidence.EvidencePackError, "different artifact contents"):
                    handoff.send_to_inbox(pack, inbox)
                self.assertEqual(before, {path.relative_to(delivered): path.read_bytes()
                                         for path in delivered.rglob("*") if path.is_file()})
                self.assertEqual(changed.read_bytes(), b"repaired evidence")
                self.assertEqual(list(inbox.iterdir()), [delivered])

    def test_local_retry_state_does_not_break_delivery_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = self.make_pack(root)
            inbox = root / "Inbox"
            handoff.send_to_inbox(pack, inbox)
            record = json.loads((pack / "job.json").read_text(encoding="utf-8"))
            record["job"]["url"] = "C:/different/local/private-demo.mp4"
            record["job"]["options"] = {"local_source_path": record["job"]["url"]}
            (pack / "job.json").write_text(json.dumps(record), encoding="utf-8")
            self.assertEqual(handoff.send_to_inbox(pack, inbox)["status"], "already_present")

    def test_inbox_inside_source_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pack = self.make_pack(Path(tempdir))
            inbox = pack / "visual_states" / "all" / "Inbox"
            with self.assertRaisesRegex(evidence.EvidencePackError, "outside"):
                handoff.send_to_inbox(pack, inbox)
            self.assertFalse(inbox.exists())


if __name__ == "__main__":
    unittest.main()
