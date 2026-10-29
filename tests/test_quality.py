import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from clipmind import cli, evidence
from clipmind.quality import audit_pack
from tests.pack_fixture import make_complete_pack


class PackIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pack = make_complete_pack(Path(self.temp.name))
        for path in (self.pack / "visual_states").rglob("*.jpg"):
            Image.new("RGB", (64, 48), "white").save(path)

    def change_rows(self, name, change):
        path = self.pack / name
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        change(rows)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_valid_pack_and_readonly_cli(self):
        before = {p: p.read_bytes() for p in self.pack.rglob("*") if p.is_file()}
        self.assertTrue(audit_pack(self.pack)["passed"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.entrypoint(["verify", str(self.pack), "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["scope"], "artifact_integrity_not_semantic_accuracy")
        self.assertEqual(before, {p: p.read_bytes() for p in self.pack.rglob("*") if p.is_file()})

    def test_broken_association_fails_even_with_complete_manifest(self):
        self.change_rows("ocr.jsonl", lambda rows: rows[0].update(visual_state_ref="other"))
        report = audit_pack(self.pack)
        self.assertFalse(report["passed"])
        self.assertTrue(any("association" in error for error in report["errors"]))

    def test_identity_and_preview_mismatch_are_detected(self):
        path = self.pack / "source.json"
        source = json.loads(path.read_text(encoding="utf-8"))
        source["source_id"] = "another-video"
        path.write_text(json.dumps(source), encoding="utf-8")
        for image in (self.pack / "visual_states/preview").glob("*.jpg"):
            Image.new("RGB", (64, 48), "red").save(image)
        errors = audit_pack(self.pack)["errors"]
        self.assertTrue(any("identity" in error for error in errors))
        self.assertTrue(any("Preview differs" in error for error in errors))

    def test_crash_recovery_can_ignore_job_state_but_regular_audit_cannot(self):
        (self.pack / "job.json").write_bytes(b"corrupt interrupted state")
        self.assertFalse(audit_pack(self.pack)["passed"])
        self.assertTrue(audit_pack(self.pack, decode_images=False, check_job=False)["passed"])

    def test_missing_image_corrupt_image_and_traversal_rejected(self):
        for value in ("visual_states/all/missing.jpg", "../../private/Cookies"):
            with self.subTest(value=value):
                self.change_rows("visual_timeline.jsonl", lambda rows: rows[0].update(file=value))
                report = audit_pack(self.pack)
                self.assertFalse(report["passed"])
                self.assertNotIn("Cookies", json.dumps(report))
        self.change_rows("visual_timeline.jsonl", lambda rows: rows[0].update(file="visual_states/all/bad.jpg"))
        (self.pack / "visual_states/all/bad.jpg").write_bytes(b"broken")
        self.assertFalse(audit_pack(self.pack)["passed"])

    def test_duplicate_ids_and_nonfinite_time_rejected(self):
        self.change_rows("transcript.jsonl", lambda rows: rows.append(dict(rows[0])))
        self.change_rows("visual_timeline.jsonl", lambda rows: rows[0].update(start=float("nan")))
        report = audit_pack(self.pack)
        self.assertFalse(report["passed"])
        self.assertTrue(any("Duplicate" in value for value in report["errors"]))
        self.assertTrue(any("time" in value for value in report["errors"]))

    def test_bad_png_checksum_returns_failed_audit_instead_of_crashing(self):
        path = self.pack / "visual_states/all/corrupt.png"
        Image.new("RGB", (2, 2), "red").save(path)
        data = bytearray(path.read_bytes())
        start = data.index(b"IDAT")
        length = int.from_bytes(data[start-4:start], "big")
        data[start + 4 + length] ^= 1
        path.write_bytes(data)
        self.change_rows("visual_timeline.jsonl", lambda rows: rows[0].update(file="visual_states/all/corrupt.png"))
        self.assertFalse(audit_pack(self.pack)["passed"])

    def test_excessively_nested_json_is_rejected(self):
        (self.pack / "ocr.jsonl").write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
        self.assertFalse(audit_pack(self.pack)["passed"])

    def test_oversized_integer_timestamp_is_rejected_without_overflow(self):
        self.change_rows("transcript.jsonl", lambda rows: rows[0].update(start=10**400))
        self.assertFalse(audit_pack(self.pack)["passed"])

    def test_corrupt_manifest_shapes_and_utf8_are_controlled_errors(self):
        for value in (b"[]", b'{"schema":[]}', b'\xff\xfe\x00', b'{"schema":{"name":"clipmind-evidence-pack","version":[]},"status":"complete"}'):
            with self.subTest(value=value):
                (self.pack / "manifest.json").write_bytes(value)
                with self.assertRaises(evidence.EvidencePackError):
                    evidence.load_complete_pack(self.pack)
                self.assertFalse(audit_pack(self.pack)["passed"])


if __name__ == "__main__":
    unittest.main()
