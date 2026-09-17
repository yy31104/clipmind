from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from clipmind import evidence, visual_states
from clipmind.asr import Transcript
from clipmind.fetch import Media
from clipmind.media import Frame
from scripts import rebuild_preview


class PreviewRebuildTests(unittest.TestCase):
    def _make_ocr_failed_pack(self, dest: Path) -> None:
        all_dir = dest / "visual_states" / "all"
        preview_dir = dest / "visual_states" / "preview"
        all_dir.mkdir(parents=True)
        preview_dir.mkdir()
        path = all_dir / "00-00-00000.jpg"
        Image.effect_noise((64, 64), 60).convert("RGB").save(path)
        (preview_dir / path.name).write_bytes(path.read_bytes())
        frame = Frame(
            0, 0.0, path, ocr_warning="RuntimeError: OCR failed; image retained"
        )
        item = Media(
            dest / "source.mp4",
            {
                "id": "fixture",
                "title": "Fixture",
                "duration": 1.0,
                "webpage_url": "https://www.douyin.com/video/fixture",
                "_clipmind_platform": "douyin",
            },
        )
        metadata = {
            "id": "fixture",
            "visual_states": [{"timestamp": 0.0, "file": "visual_states/all/00-00-00000.jpg", "text": ""}],
            "visual_preview": [],
            "preview_frame_count": 1,
        }
        (dest / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (dest / "job.json").write_text(
            json.dumps({"version": 1, "job": {"id": "fixture", "status": "done", "result": dict(metadata)}}),
            encoding="utf-8",
        )
        evidence.write_pack(
            dest,
            item,
            Transcript([]),
            [frame],
            [Frame(**{**frame.__dict__, "path": preview_dir / path.name})],
            [],
            candidate_frame_count=1,
            ocr_error="OCR failed on 1/1 frames",
        )

    def test_rebuilds_only_the_derived_view_and_keeps_a_complete_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            all_dir = dest / "visual_states" / "all"
            preview_dir = dest / "visual_states" / "preview"
            all_dir.mkdir(parents=True)
            preview_dir.mkdir()
            paths = [all_dir / f"00-0{i}-{i:05d}.jpg" for i in range(3)]
            for path, pixels in zip(
                paths,
                (
                    [0, 0, 255, 255],
                    [0, 0, 255, 255],
                    [255, 255, 0, 0],
                ),
            ):
                image = Image.new("L", (4, 4))
                image.putdata(pixels * 4)
                image.save(path)
                (preview_dir / path.name).write_bytes(path.read_bytes())

            frames = [
                Frame(0, 0.0, paths[0], lines=("alpha",), text="alpha"),
                Frame(1, 1.0, paths[1], lines=("alpha beta",), text="alpha beta"),
                Frame(2, 2.0, paths[2], lines=("gamma",), text="gamma"),
            ]
            groups = visual_states.group_progressive_builds(frames)
            preview = [Frame(**{**frame.__dict__, "path": preview_dir / frame.path.name}) for frame in frames]
            item = Media(
                dest / "source.mp4",
                {
                    "id": "fixture",
                    "title": "Fixture",
                    "duration": 3.0,
                    "webpage_url": "https://www.douyin.com/video/fixture",
                },
            )
            metadata = {"id": "fixture", "visual_preview": [], "preview_frame_count": 3}
            (dest / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            (dest / "job.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "job": {
                            "id": "fixture",
                            "status": "done",
                            "result": dict(metadata),
                        },
                    }
                ),
                encoding="utf-8",
            )
            evidence.write_pack(
                dest,
                item,
                Transcript([]),
                frames,
                preview,
                groups,
                candidate_frame_count=3,
            )

            count = rebuild_preview.rebuild(dest)

            manifest = evidence.load_complete_pack(dest)
            timeline = rebuild_preview.read_jsonl(dest / "visual_timeline.jsonl")
            self.assertEqual(count, 2)
            self.assertEqual(manifest["counts"]["canonical_visual_states"], 3)
            self.assertEqual(manifest["counts"]["preview_visual_states"], 2)
            self.assertEqual(
                manifest["configuration"]["preview_algorithm"],
                visual_states.PREVIEW_ALGORITHM,
            )
            self.assertEqual(sum(row["in_preview"] for row in timeline), 2)
            self.assertEqual(len(list(all_dir.glob("*.jpg"))), 3)
            self.assertEqual(len(list(preview_dir.glob("*.jpg"))), 2)
            job = json.loads((dest / "job.json").read_text(encoding="utf-8"))["job"]
            metadata = json.loads(
                (dest / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(job["result"]["preview_frame_count"], 2)
            self.assertEqual(len(job["result"]["visual_preview"]), 2)
            self.assertEqual(
                job["result"]["visual_preview"], metadata["visual_preview"]
            )

    def test_refresh_ocr_repairs_existing_images_without_source_media(self) -> None:
        class Reader:
            def read_text(self, _path: Path) -> list[str]:
                return ["restored screen text"]

        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            self._make_ocr_failed_pack(dest)
            with patch.object(
                rebuild_preview,
                "default_providers",
                return_value=SimpleNamespace(text=Reader()),
            ):
                rebuild_preview.rebuild(dest, refresh_ocr=True)

            manifest = evidence.load_complete_pack(dest)
            ocr = rebuild_preview.read_jsonl(dest / "ocr.jsonl")
            metadata = json.loads((dest / "metadata.json").read_text(encoding="utf-8"))
            evidence_text = (dest / "evidence.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["completeness"]["ocr"], "complete")
        self.assertEqual(manifest["diagnostics"]["ocr_failure_count"], 0)
        self.assertEqual(ocr[0]["text"], "restored screen text")
        self.assertEqual(metadata["visual_states"][0]["text"], "restored screen text")
        self.assertIn("restored screen text", evidence_text)

    def test_refresh_ocr_keeps_the_pack_unchanged_when_every_frame_still_fails(self) -> None:
        class Reader:
            def read_text(self, _path: Path) -> list[str]:
                raise RuntimeError("still unavailable")

        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            self._make_ocr_failed_pack(dest)
            before = {
                name: (dest / name).read_bytes()
                for name in ("manifest.json", "ocr.jsonl", "evidence.md", "job.json")
            }
            with (
                patch.object(
                    rebuild_preview,
                    "default_providers",
                    return_value=SimpleNamespace(text=Reader()),
                ),
                self.assertRaises(RuntimeError),
            ):
                rebuild_preview.rebuild(dest, refresh_ocr=True)

            after = {name: (dest / name).read_bytes() for name in before}

        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
