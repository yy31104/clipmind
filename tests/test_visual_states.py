from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clipmind import visual_states
from clipmind.media import Frame


class CanonicalVisualStateTests(unittest.TestCase):
    def test_retain_all_is_untruncated_chronological_and_collision_safe(self) -> None:
        timestamps = [5.9, 0.2, 0.8, 1.1, 1.7, 2.0, 2.4, 3.2, 3.7, 4.1, 4.9, 5.1]

        with tempfile.TemporaryDirectory() as tempdir:
            workdir = Path(tempdir)
            samples = workdir / "samples"
            samples.mkdir()
            frames: list[Frame] = []
            original_paths: list[Path] = []
            for index, timestamp in enumerate(timestamps):
                path = samples / f"sample-{index}.jpg"
                path.write_bytes(f"state-{index}".encode())
                original_paths.append(path)
                frames.append(Frame(index=index, timestamp=timestamp, path=path))

            retained = visual_states.retain_all(
                frames,
                workdir / "visual_states" / "all",
            )

            self.assertEqual(len(retained), 12)
            self.assertEqual(
                [frame.timestamp for frame in retained],
                sorted(timestamps),
            )
            self.assertEqual(len({frame.path for frame in retained}), 12)
            self.assertTrue(
                all(frame.path.parent == workdir / "visual_states" / "all" for frame in retained)
            )
            self.assertEqual(
                {frame.path.read_bytes() for frame in retained},
                {f"state-{index}".encode() for index in range(12)},
            )
            self.assertTrue(all(not path.exists() for path in original_paths))

    def test_progressive_build_is_grouped_without_removing_canonical_states(self) -> None:
        frames = [
            Frame(0, 0.0, Path("a.jpg"), lines=("Python",)),
            Frame(1, 1.0, Path("b.jpg"), lines=("Python FastAPI",)),
            Frame(2, 2.0, Path("c.jpg"), lines=("Python FastAPI PostgreSQL",)),
        ]

        groups = visual_states.group_progressive_builds(frames)
        preview = visual_states.derive_preview(frames)

        self.assertEqual(len(frames), 3, "grouping must not truncate canonical evidence")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].id, "build-00001")
        self.assertEqual(groups[0].frames, tuple(frames))
        self.assertIs(groups[0].representative, frames[-1])
        self.assertEqual([frame.build_position for frame in frames], [0, 1, 2])
        self.assertEqual(preview, [frames[-1]])

    def test_disappearing_or_replaced_text_breaks_the_build_group(self) -> None:
        frames = [
            Frame(0, 0.0, Path("a.jpg"), lines=("Python",)),
            Frame(1, 1.0, Path("b.jpg"), lines=("Python FastAPI",)),
            Frame(2, 2.0, Path("c.jpg"), lines=("Python PostgreSQL",)),
        ]

        groups = visual_states.group_progressive_builds(frames)
        preview = visual_states.derive_preview(frames)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].frames, tuple(frames[:2]))
        self.assertEqual(len(frames), 3, "replaced information remains canonical")
        self.assertIn("FastAPI", " ".join(frames[1].lines))
        self.assertIn("PostgreSQL", " ".join(frames[2].lines))
        self.assertEqual(preview, [frames[2]])

    def test_preview_has_no_fixed_frame_budget(self) -> None:
        frames = [
            Frame(
                index,
                float(index),
                Path(f"{index}.jpg"),
                phash=0 if index % 2 == 0 else (1 << 64) - 1,
                lines=(f"state {index}",),
            )
            for index in range(15)
        ]

        visual_states.group_progressive_builds(frames, window=0.5)

        self.assertEqual(visual_states.derive_preview(frames), frames)

    def test_preview_selects_latest_when_it_remains_similarly_readable(self) -> None:
        richest = Frame(0, 0.0, Path("a.jpg"), phash=0, lines=("alpha beta",))
        completed = Frame(1, 1.0, Path("b.jpg"), phash=1, lines=("alpha gamma",))

        preview = visual_states.derive_preview(
            [richest, completed], scene_floor=10, activity_margin=0
        )

        self.assertEqual(preview, [completed])

    def test_preview_keeps_richest_frame_when_latest_visual_is_text_poor(self) -> None:
        document = Frame(0, 0.0, Path("a.jpg"), phash=0, lines=("a detailed document",))
        fading = Frame(1, 1.0, Path("b.jpg"), phash=1, lines=("end",))

        preview = visual_states.derive_preview(
            [document, fading], scene_floor=10, activity_margin=0
        )

        self.assertEqual(preview, [document])

    def test_preview_splits_same_layout_when_visible_text_is_replaced(self) -> None:
        first = Frame(0, 0.0, Path("a.jpg"), phash=0, lines=("ALPHA PLAN",))
        second = Frame(1, 1.0, Path("b.jpg"), phash=127, lines=("BETA SYSTEM",))

        preview = visual_states.derive_preview([first, second])

        self.assertEqual(preview, [first, second])

    def test_preview_does_not_treat_matching_speech_captions_as_slides(self) -> None:
        first = Frame(0, 0.0, Path("a.jpg"), phash=0, lines=("ALPHA BLOCK",))
        second = Frame(1, 1.0, Path("b.jpg"), phash=127, lines=("SYSTEM VIEW",))

        preview = visual_states.derive_preview(
            [first, second],
            spoken_intervals=(
                (0.0, 0.8, "ALPHA BLOCK"),
                (0.8, 2.0, "SYSTEM VIEW"),
            ),
        )

        self.assertEqual(preview, [second])

    def test_dedupe_warning_is_never_hidden_by_preview_clustering(self) -> None:
        normal = Frame(0, 0.0, Path("a.jpg"), phash=0)
        uncertain = Frame(
            1,
            1.0,
            Path("b.jpg"),
            phash=0,
            dedupe_warning="hash failed; retained",
        )

        self.assertEqual(visual_states.derive_preview([normal, uncertain]), [normal, uncertain])

    def test_materialize_preview_copies_and_does_not_repoint_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workdir = Path(tempdir)
            canonical_dir = workdir / "visual_states" / "all"
            canonical_dir.mkdir(parents=True)
            source = canonical_dir / "00-01-00000.jpg"
            source.write_bytes(b"canonical")
            frame = Frame(0, 1.0, source)

            preview = visual_states.materialize_preview(
                [frame], workdir / "visual_states" / "preview"
            )

            self.assertEqual(frame.path, source)
            self.assertTrue(source.exists())
            self.assertEqual(preview[0].path.read_bytes(), b"canonical")
            self.assertNotEqual(preview[0].path, frame.path)

    def test_retain_all_can_promote_high_resolution_evidence_by_sample_index(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workdir = Path(tempdir)
            low = workdir / "samples" / "s_00001.jpg"
            high = workdir / "evidence_samples" / "s_00001.jpg"
            low.parent.mkdir()
            high.parent.mkdir()
            low.write_bytes(b"low")
            high.write_bytes(b"high")
            frame = Frame(0, 0.0, low)

            retained = visual_states.retain_all(
                [frame],
                workdir / "visual_states" / "all",
                evidence_sources={0: high},
            )

            self.assertEqual(retained[0].path.read_bytes(), b"high")
            self.assertTrue(low.exists(), "low-resolution detector input remains temporary")
            self.assertFalse(high.exists())


if __name__ == "__main__":
    unittest.main()
