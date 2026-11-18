from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clipmind import screen_rank, screen_rank_features, visual_states
from clipmind.media import Frame
from scripts.classify_screen_ablation import _legacy_timeline
from scripts.screen_evaluation import boundary_inventory


def frames():
    return [
        Frame(0, 0., Path("initial.jpg"), change_kind="content"),
        Frame(1, 2., Path("speaker.jpg"), change_kind="speaker"),
        Frame(2, 3., Path("content.jpg"), change_kind="content"),
        Frame(3, 3.5, Path("warning.jpg"), ocr_warning="fixture"),
    ]


class ScreenRankTests(unittest.TestCase):
    def test_competition_preserves_close_warnings_and_breaks_ties_in_time_order(self):
        times = [0., 2., 3., 4.1, 4.5, 5.3, 7.0]
        scores = {1: .8, 2: .8, 5: .9, 6: .2}
        self.assertEqual(screen_rank._select(times, scores, {3, 4}, .25), {1, 3, 4})

    def test_competition_includes_one_second_boundary_despite_subtraction_rounding(self):
        self.assertEqual(screen_rank._select([0., .1, 1.1], {1: .8, 2: .9}, set(), .5), {2})

    def test_rank_keeps_warnings_and_semantic_types(self):
        timeline = frames()
        with patch.object(screen_rank_features, "features", return_value=iter(((1, (.8,)), (2, (.3,))))), \
             patch.object(screen_rank, "_score", side_effect=(.8, .3)):
            self.assertTrue(screen_rank.annotate_information_boundaries(timeline, 5.))
        self.assertEqual([f.information_boundary for f in timeline], [None, True, False, True])
        self.assertEqual([f.change_kind for f in timeline], ["content", "speaker", "content", "uncertain"])
        self.assertEqual([f.timestamp for f in timeline], [0., 2., 3., 3.5])

    def test_missing_raster_falls_back_without_partial_decisions(self):
        with TemporaryDirectory() as tmp:
            timeline = frames()
            for f in timeline:
                f.path = Path(tmp) / f.path
            with self.assertLogs(screen_rank.__name__, level="WARNING"):
                self.assertFalse(screen_rank.annotate_information_boundaries(timeline, 5.))
        self.assertEqual([f.information_boundary for f in timeline], [None]*4)

    def test_original_inventory_uses_explicit_decision_with_warning_override(self):
        timeline = [
            {"start": 2., "change_kind": "speaker", "information_boundary": True},
            {"start": 4., "change_kind": "content", "information_boundary": False},
            {"start": 6., "change_kind": "speaker", "information_boundary": False, "ocr_warning": "bad"},
            {"start": 8., "change_kind": "speaker"},
            {"start": 10., "change_kind": "content", "information_boundary": "false"},
        ]
        args = dict(start=0.,end=12.)
        canonical = boundary_inventory([2., 4., 6.], timeline, **args)
        info = boundary_inventory([2., 4., 6.], timeline, **args, information_only=True)
        self.assertEqual(canonical["score"]["matches"], [[2.,2.],[4.,4.],[6.,6.]])
        self.assertEqual(info["score"]["matches"], [[2.,2.],[6.,6.]])
        self.assertEqual(info["score"]["reference_count"], 3)
        self.assertEqual(info["score"]["prediction_count"], 3)

    def test_legacy_ablation_discards_previous_rank_flags_but_preserves_ocr_warning(self):
        timeline = frames()
        states = [{"start":f.timestamp,"information_boundary":True} for f in timeline]
        legacy = _legacy_timeline(states,timeline)
        self.assertTrue(all("information_boundary" not in row for row in legacy))
        self.assertEqual(legacy[-1]["ocr_warning"],"fixture")
        self.assertEqual(visual_states.INFORMATION_BOUNDARY_ALGORITHM,"anchored-display-v5")

    def test_pack_records_the_algorithm_and_keeps_optional_false(self):
        from clipmind import evidence
        from clipmind.asr import Transcript
        from clipmind.fetch import Media

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            timeline = frames()
            for frame in timeline:
                frame.path = root / frame.path
                frame.path.write_bytes(b"fixture")
            item = Media(video_path=root/"input.mp4", info={"id":"fixture","duration":5.})
            args = (root,item,Transcript([]),timeline,[],[])
            fallback = evidence.write_pack(*args,candidate_frame_count=4)
            self.assertEqual(fallback["configuration"]["information_boundary_algorithm"],
                             visual_states.INFORMATION_BOUNDARY_ALGORITHM)
            self.assertNotIn('"information_boundary"',(root/"visual_timeline.jsonl").read_text())
            timeline[1].information_boundary = False
            ranked = evidence.write_pack(*args,candidate_frame_count=4,boundary_algorithm=screen_rank.ALGORITHM)
            self.assertEqual(ranked["configuration"]["information_boundary_algorithm"],screen_rank.ALGORITHM)
            self.assertIn('"information_boundary":false',(root/"visual_timeline.jsonl").read_text().replace(" ",""))
