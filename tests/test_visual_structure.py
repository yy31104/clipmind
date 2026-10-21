from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from PIL import Image

from clipmind import visual_states
from clipmind.media import Frame


class VisualStructureTests(unittest.TestCase):
    MEASUREMENTS = {
        "content_hint", "scene_id", "scene_boundary",
        "scene_boundary_reason", "scene_change_score",
    }

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def frame(self, index: int, timestamp: float, **kwargs) -> Frame:
        path = self.root / f"canonical-{index}.jpg"
        Image.new("RGB", (48, 32), (index % 255, 80, 110)).save(path)
        return Frame(index, timestamp, path, **kwargs)

    def measurements(self, frames: list[Frame]) -> list[dict]:
        return [{key: getattr(frame, key) for key in self.MEASUREMENTS} for frame in frames]

    def unchanged_evidence(self, frames: list[Frame]) -> tuple:
        # Structure annotation may only alter the fields named above. In
        # particular, it must not reorder a caller's list or rewrite a JPEG.
        return (
            [id(frame) for frame in frames],
            [{key: value for key, value in asdict(frame).items()
              if key not in self.MEASUREMENTS} for frame in frames],
            {frame.path: frame.path.read_bytes() for frame in frames},
        )

    def test_annotations_match_legacy_preview_without_selecting_or_changing_evidence(self) -> None:
        caption = self.frame(11, 0.0, phash=0, lines=("hello world",),
                             ocr_char_count=10, transcript_overlap=1.0)
        code = self.frame(23, 1.0, phash=(1 << 64) - 1,
                          lines=("async def load():", "return client.fetch()"),
                          ocr_char_count=35)
        document = self.frame(37, 2.0, phash=0,
                              lines=tuple(f"research note number {n}" for n in range(8)),
                              ocr_char_count=140)
        frames = [document, caption, code]
        legacy = [replace(frame) for frame in frames]
        before = self.unchanged_evidence(frames)
        options = {"spoken_intervals": ((0.0, .9, "hello world"),),
                   "scene_floor": 18.0, "activity_margin": 4.0, "scene_ceiling": 30.0}

        self.assertIsNone(visual_states.annotate_structure(frames, **options))
        visual_states.derive_preview(legacy, **options)

        self.assertEqual(self.measurements(frames), self.measurements(legacy))
        self.assertEqual([caption.content_hint, code.content_hint, document.content_hint],
                         ["caption", "code_ui", "document_slide"])
        self.assertEqual([caption.scene_id, code.scene_id, document.scene_id],
                         ["scene-00001", "scene-00002", "scene-00003"])
        self.assertEqual([caption.scene_boundary_reason, code.scene_boundary_reason,
                          document.scene_boundary_reason],
                         ["initial", "text_replacement", "text_replacement"])
        self.assertEqual([caption.scene_change_score, code.scene_change_score,
                          document.scene_change_score], [None, 1.0, 1.0])
        self.assertEqual(before, self.unchanged_evidence(frames))

    def test_warning_boundaries_and_progressive_scene_propagation_preserve_every_frame(self) -> None:
        frames = [
            self.frame(0, 0.0, lines=("Python",)),
            self.frame(1, 1.0, lines=("Python FastAPI",)),
            self.frame(2, 2.0, lines=("Python FastAPI PostgreSQL",)),
            self.frame(3, 3.0, lines=("unrelated warning",), dedupe_warning="hash unavailable"),
            self.frame(4, 4.0, phash=1, lines=("another independent item",)),
        ]
        groups = visual_states.group_progressive_builds(frames)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].frames, tuple(frames[:3]))
        legacy = [replace(frame) for frame in frames]
        before = self.unchanged_evidence(frames)

        self.assertIsNone(visual_states.annotate_structure(frames))
        visual_states.derive_preview(legacy)

        self.assertEqual(self.measurements(frames), self.measurements(legacy))
        self.assertEqual([frame.scene_id for frame in frames],
                         ["scene-00001"] * 3 + ["scene-00002", "scene-00003"])
        self.assertEqual([frame.scene_boundary for frame in frames],
                         [False, False, True, True, True])
        self.assertEqual([frame.scene_boundary_reason for frame in frames],
                         [None, None, "initial", "hash_warning", "hash_recovery"])
        self.assertIsNone(frames[3].scene_change_score)
        self.assertIsNone(frames[4].scene_change_score)
        self.assertEqual(before, self.unchanged_evidence(frames))

    def test_empty_and_single_state_have_no_selection_side_effect(self) -> None:
        self.assertIsNone(visual_states.annotate_structure([]))
        frame = self.frame(29, 7.5, scene_id="stale", scene_boundary_reason="stale",
                           scene_change_score=.9, content_hint="stale")
        frames = [frame]
        before = self.unchanged_evidence(frames)

        self.assertIsNone(visual_states.annotate_structure(frames))

        self.assertEqual(self.measurements(frames), [{
            "content_hint": "visual", "scene_id": "scene-00001",
            "scene_boundary": True, "scene_boundary_reason": "initial",
            "scene_change_score": None,
        }])
        self.assertEqual(before, self.unchanged_evidence(frames))


if __name__ == "__main__":
    unittest.main()
