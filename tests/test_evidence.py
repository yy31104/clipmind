from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import evidence, pipeline, visual_states
from clipmind.asr import Segment, Transcript
from clipmind.fetch import Media
from clipmind.media import Frame


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class EvidencePackTests(unittest.TestCase):
    def make_fixture(self, dest: Path):
        all_dir = dest / "visual_states" / "all"
        preview_dir = dest / "visual_states" / "preview"
        all_dir.mkdir(parents=True)
        preview_dir.mkdir(parents=True)
        first_path = all_dir / "00-01-00000.jpg"
        second_path = all_dir / "00-05-00001.jpg"
        first_path.write_bytes(b"first")
        second_path.write_bytes(b"second")
        preview_path = preview_dir / second_path.name
        preview_path.write_bytes(b"second")

        frames = [
            Frame(0, 1.0, first_path, text="Python", lines=("Python",)),
            Frame(1, 5.0, second_path, text="Python FastAPI", lines=("Python FastAPI",)),
        ]
        groups = visual_states.group_progressive_builds(frames)
        preview = [Frame(**{**frames[1].__dict__, "path": preview_path})]
        transcript = Transcript(
            [
                Segment(0.0, 2.0, "first segment"),
                Segment(4.0, 6.0, "second segment"),
            ],
            language="en",
        )
        item = Media(
            video_path=dest / "source.mp4",
            info={
                "id": "douyin-123",
                "title": "Fixture",
                "uploader": "Author",
                "duration": 10.0,
                "webpage_url": "https://www.douyin.com/video/123",
                "_clipmind_strategy": "test",
                "_clipmind_platform": "douyin",
                "_clipmind_source_adapter": "douyin",
            },
        )
        return item, transcript, frames, preview, groups

    def test_writes_versioned_complete_and_deterministic_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            item, transcript, frames, preview, groups = self.make_fixture(dest)
            stable_settings = SimpleNamespace(
                sample_fps=2.0,
                sample_width=640,
                evidence_width=1280,
                dedupe_threshold=6,
            )
            with patch.object(evidence, "settings", stable_settings):
                manifest = evidence.write_pack(
                    dest,
                    item,
                    transcript,
                    frames,
                    preview,
                    groups,
                    candidate_frame_count=20,
                    timings={"ocr_seconds": 1.25},
                )
                artifact_names = [
                    "source.json",
                    "transcript.jsonl",
                    "transcript.md",
                    "ocr.jsonl",
                    "visual_timeline.jsonl",
                    "evidence.md",
                    "manifest.json",
                ]
                first_bytes = {
                    name: (dest / name).read_bytes() for name in artifact_names
                }
                evidence.write_pack(
                    dest,
                    item,
                    transcript,
                    frames,
                    preview,
                    groups,
                    candidate_frame_count=20,
                    timings={"ocr_seconds": 1.25},
                )

            self.assertEqual(
                first_bytes,
                {name: (dest / name).read_bytes() for name in artifact_names},
            )
            self.assertEqual(manifest["schema"]["version"], "1.2.0")
            self.assertEqual(manifest["source"]["platform"], "douyin")
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["timings"], {"ocr_seconds": 1.25})
            self.assertEqual(manifest["counts"]["canonical_visual_states"], 2)
            self.assertEqual(manifest["counts"]["preview_visual_states"], 1)
            self.assertEqual(
                manifest["configuration"]["preview_algorithm"],
                "adaptive-scene-text-v1",
            )
            self.assertEqual(manifest["completeness"]["transcript"], "complete")
            self.assertEqual(manifest["completeness"]["ocr"], "complete")
            schema = json.loads(
                (Path(__file__).parents[1] / "schemas" / "evidence-pack-v1.schema.json")
                .read_text(encoding="utf-8")
            )
            self.assertTrue(set(schema["required"]).issubset(manifest))
            self.assertTrue(set(manifest).issubset(schema["properties"]))
            accepted = schema["properties"]["schema"]["properties"]["version"]["enum"]
            self.assertIn(manifest["schema"]["version"], accepted)
            # The published schema must keep accepting the version it accepted
            # before, or existing packs stop validating.
            self.assertIn("1.0.0", accepted)

            transcript_rows = jsonl(dest / "transcript.jsonl")
            ocr_rows = jsonl(dest / "ocr.jsonl")
            timeline = jsonl(dest / "visual_timeline.jsonl")
            self.assertEqual(
                [row["id"] for row in transcript_rows],
                ["transcript-00001", "transcript-00002"],
            )
            self.assertEqual(ocr_rows[1]["visual_state_ref"], "visual-00002")
            self.assertEqual(
                timeline[0]["transcript_refs"],
                ["transcript-00001", "transcript-00002"],
            )
            self.assertEqual(timeline[1]["transcript_refs"], ["transcript-00002"])
            self.assertFalse(timeline[0]["in_preview"])
            self.assertTrue(timeline[1]["in_preview"])
            self.assertEqual(timeline[1]["build_group_id"], "build-00001")

            evidence_view = (dest / "evidence.md").read_text(encoding="utf-8")
            self.assertIn("visual_states/all/00-01-00000.jpg", evidence_view)
            self.assertIn("first segment", evidence_view)
            self.assertLess(evidence_view.index("00:01.000"), evidence_view.index("00:04.000"))

            (dest / "source.mp4").write_bytes(b"temporary media")
            pipeline.cleanup_temporary(dest)
            self.assertTrue((dest / "source.json").exists())
            self.assertFalse((dest / "source.mp4").exists())

    def test_records_missing_modalities_instead_of_claiming_full_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            item, _transcript, frames, preview, groups = self.make_fixture(dest)
            transcript = Transcript([], error="no audio track")
            frames[0].ocr_warning = "OCRError: Vision request failed"
            frames[0].text = ""
            frames[0].lines = ()

            manifest = evidence.write_pack(
                dest,
                item,
                transcript,
                frames,
                preview,
                groups,
                candidate_frame_count=20,
                ocr_error="OCR failed on 1/2 frames",
            )

            self.assertEqual(manifest["completeness"]["transcript"], "unavailable")
            self.assertEqual(manifest["completeness"]["ocr"], "partial")
            self.assertEqual(jsonl(dest / "ocr.jsonl")[0]["error"], frames[0].ocr_warning)
            self.assertIn("Transcript unavailable", (dest / "transcript.md").read_text())

    def test_manifest_is_not_written_when_an_artifact_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            item, transcript, frames, preview, groups = self.make_fixture(dest)

            with patch.object(
                evidence,
                "_write_jsonl",
                side_effect=RuntimeError("injected artifact failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected artifact failure"):
                    evidence.write_pack(
                        dest,
                        item,
                        transcript,
                        frames,
                        preview,
                        groups,
                        candidate_frame_count=20,
                    )

            self.assertFalse((dest / "manifest.json").exists())



class SchemaCompatibilityTests(unittest.TestCase):
    def test_packs_written_by_the_previous_schema_stay_readable(self) -> None:
        """1.1.0 only adds fields, so 1.0.0 packs must not become unusable."""
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            for artifact in evidence.PACK_ARTIFACTS:
                (dest / artifact).parent.mkdir(parents=True, exist_ok=True)
                (dest / artifact).write_text("{}", encoding="utf-8")
            (dest / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": {"name": evidence.SCHEMA_NAME, "version": "1.0.0"},
                        "status": "complete",
                    }
                ),
                encoding="utf-8",
            )

            manifest = evidence.load_complete_pack(dest)

        self.assertEqual(manifest["schema"]["version"], "1.0.0")

    def test_schema_accepts_contributor_defined_source_platforms(self) -> None:
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas" / "evidence-pack-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        platform = schema["properties"]["source"]["properties"]["platform"]

        self.assertEqual(platform["type"], "string")
        self.assertEqual(platform["minLength"], 1)
        self.assertNotIn("enum", platform)

    def test_an_unknown_schema_version_is_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            for artifact in evidence.PACK_ARTIFACTS:
                (dest / artifact).parent.mkdir(parents=True, exist_ok=True)
                (dest / artifact).write_text("{}", encoding="utf-8")
            (dest / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": {"name": evidence.SCHEMA_NAME, "version": "9.9.9"},
                        "status": "complete",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(evidence.EvidencePackError):
                evidence.load_complete_pack(dest)


if __name__ == "__main__":
    unittest.main()
