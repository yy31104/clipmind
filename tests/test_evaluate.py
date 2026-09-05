from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from clipmind import acquisition, evidence
from scripts import evaluate


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def make_complete_pack(root: Path, *, source_sha256: str | None = None) -> Path:
    pack = root / "pack-1"
    for artifact in evidence.PACK_ARTIFACTS:
        path = pack / artifact
        if artifact.endswith("/"):
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    timeline = {
        "id": "visual-00001",
        "start": 2.0,
        "end": 8.0,
        "file": "visual_states/all/00-02-00001.jpg",
        "preview_file": "visual_states/preview/00-02-00001.jpg",
        "ocr_ref": "ocr-00001",
        "transcript_refs": ["transcript-00001"],
        "in_preview": True,
    }
    transcript = {"id": "transcript-00001", "start": 1.0, "end": 4.0, "text": "lesson"}
    ocr = {
        "id": "ocr-00001",
        "visual_state_ref": "visual-00001",
        "timestamp": 2.0,
        "text": "lesson",
        "lines": ["lesson"],
    }
    write_json(pack / "source.json", {"source_id": "video-1"})
    write_json(pack / "job.json", {"job": {"id": "pack-1", "status": "done"}})
    (pack / "transcript.jsonl").write_text(json.dumps(transcript) + "\n", encoding="utf-8")
    (pack / "ocr.jsonl").write_text(json.dumps(ocr) + "\n", encoding="utf-8")
    (pack / "visual_timeline.jsonl").write_text(json.dumps(timeline) + "\n", encoding="utf-8")
    Image.new("RGB", (16, 16), "white").save(pack / timeline["file"])
    Image.new("RGB", (16, 16), "white").save(pack / timeline["preview_file"])
    source = {"platform": "douyin", "id": "video-1"}
    if source_sha256 is not None:
        source["content_sha256"] = source_sha256
    write_json(
        pack / "manifest.json",
        {
            "schema": {"name": evidence.SCHEMA_NAME, "version": evidence.SCHEMA_VERSION},
            "source": source,
            "status": "complete",
            "artifacts": list(evidence.PACK_ARTIFACTS),
            "counts": {"canonical_visual_states": 1},
            "completeness": {
                "transcript": "complete",
                "ocr": "complete",
                "visual_states": "complete",
            },
            "configuration": {"dedupe_threshold": 6},
        },
    )
    return pack


class EvaluateTests(unittest.TestCase):
    def test_exact_canonical_count_fails_above_the_old_quality_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workdir = make_complete_pack(Path(tempdir))
            case = {
                "id": "exact-count-probe",
                "source_id": "video-1",
                "content_type": "fixture",
                "expected": {
                    "minimum_transcript_segments": 1,
                    "minimum_visual_states": 1,
                    "canonical_visual_state_count": 2,
                    "maximum_preview_ratio": 1.0,
                    "requires_ocr": True,
                },
            }

            result = evaluate.evaluate_case(case, workdir)
            report = evaluate.evaluate_suite(
                {"version": 2, "cases": [case]},
                Path(tempdir),
                mode="existing",
            )

        self.assertTrue(result["checks"]["visual_state_minimum"])
        self.assertFalse(result["checks"]["canonical_visual_state_count_exact"])
        self.assertFalse(result["passed"])
        self.assertIsNone(result["metrics"]["candidate_frame_count"])
        self.assertEqual(result["metrics"]["dedupe_policy"]["threshold"], 6)
        self.assertEqual(report["mode"], "existing")
        self.assertEqual(report["pack_origin"], "existing_completed_pack")
        self.assertFalse(report["passed"])

    def test_reextract_exact_count_only_applies_to_matching_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workdir = make_complete_pack(Path(tempdir), source_sha256="actual-sha")
            base_case = {
                "id": "source-identity-probe",
                "source_id": "video-1",
                "content_type": "fixture",
                "expected": {
                    "minimum_transcript_segments": 1,
                    "minimum_visual_states": 1,
                    "canonical_visual_state_count": 2,
                    "maximum_preview_ratio": 1.0,
                    "requires_ocr": True,
                },
            }

            matching = json.loads(json.dumps(base_case))
            matching["expected"]["source_content_sha256"] = "actual-sha"
            changed = json.loads(json.dumps(base_case))
            changed["expected"]["source_content_sha256"] = "different-sha"
            unavailable = json.loads(json.dumps(base_case))

            matching_result = evaluate.evaluate_case(
                matching, workdir, mode="reextract"
            )
            changed_result = evaluate.evaluate_case(
                changed, workdir, mode="reextract"
            )
            unavailable_result = evaluate.evaluate_case(
                unavailable, workdir, mode="reextract"
            )

        self.assertEqual(matching_result["source_identity"]["status"], "match")
        self.assertFalse(matching_result["checks"]["canonical_visual_state_count_exact"])
        self.assertFalse(matching_result["passed"])
        self.assertEqual(changed_result["source_identity"]["status"], "changed")
        self.assertIsNone(changed_result["checks"]["canonical_visual_state_count_exact"])
        self.assertTrue(changed_result["passed"])
        self.assertEqual(unavailable_result["source_identity"]["status"], "unavailable")
        self.assertIsNone(
            unavailable_result["checks"]["canonical_visual_state_count_exact"]
        )
        self.assertTrue(unavailable_result["passed"])

    def test_reextract_submits_every_case_to_an_empty_job_store(self) -> None:
        observed: dict[str, object] = {}

        class FakeStore:
            def __init__(self, out_dir: Path, *, config) -> None:
                observed["out_dir"] = out_dir
                observed["keep_source_video"] = config.keep_source_video
                self.out_dir = out_dir
                self._tasks: set[asyncio.Task] = set()
                self._counter = 0

            def submit(self, url: str, title: str) -> SimpleNamespace:
                observed.setdefault("submissions", []).append((url, title))
                self._counter += 1
                job_id = f"job-{self._counter}"
                workdir = self.workdir(job_id)
                workdir.mkdir(parents=True)
                # Write where acquisition actually puts media, through the
                # shipped entry point, so this fixture cannot silently drift
                # away from the layout the evaluator has to read.
                root = acquisition.open_workspace(workdir, strategy="fake")
                (root / "source.mp4").write_bytes(url.encode())
                task = asyncio.create_task(asyncio.sleep(0))
                self._tasks.add(task)
                return SimpleNamespace(
                    id=job_id,
                    status="done",
                    error=None,
                    error_code=None,
                    error_action=None,
                )

            async def close(self) -> None:
                return None

            def workdir(self, job_id: str) -> Path:
                return self.out_dir / job_id

        cases = [
            {"id": "one", "source_id": "source-1", "url": "https://example.com/1"},
            {"id": "two", "source_id": "source-2", "url": "https://example.com/2"},
        ]
        with tempfile.TemporaryDirectory() as tempdir:
            out_dir = Path(tempdir) / "fresh"
            with patch.object(evaluate, "JobStore", FakeStore):
                failures, source_hashes = asyncio.run(
                    evaluate.reextract_cases(cases, out_dir)
                )

        self.assertEqual(failures, [])
        self.assertEqual(observed["out_dir"], out_dir)
        self.assertTrue(observed["keep_source_video"])
        self.assertEqual(
            observed["submissions"],
            [
                ("https://example.com/1", "Real-world evaluation: one"),
                ("https://example.com/2", "Real-world evaluation: two"),
            ],
        )
        self.assertEqual(
            source_hashes,
            {
                "source-1": hashlib.sha256(b"https://example.com/1").hexdigest(),
                "source-2": hashlib.sha256(b"https://example.com/2").hexdigest(),
            },
        )
        self.assertFalse(any(out_dir.rglob("source.mp4")))

    def test_reextract_refuses_a_nonempty_output_library(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            out_dir = Path(tempdir)
            (out_dir / "old-pack").mkdir()
            with patch.object(evaluate, "JobStore") as store:
                with self.assertRaisesRegex(ValueError, "must be empty"):
                    asyncio.run(evaluate.reextract_cases([], out_dir))

        store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
