"""Transcript novelty: how much on-screen text the speech does not carry."""
from __future__ import annotations

import unittest
from pathlib import Path

from clipmind import visual_states
from clipmind.media import Frame


def frame(index: int, *, timestamp: float | None = None, phash: int = 0,
          lines: tuple[str, ...] = ()) -> Frame:
    return Frame(
        index=index,
        timestamp=float(index) if timestamp is None else timestamp,
        path=Path(f"f-{index}.jpg"),
        phash=phash,
        lines=lines,
    )


class AlignmentTests(unittest.TestCase):
    def test_a_caption_repeating_the_speech_is_not_novel(self) -> None:
        spoken = ((0.0, 3.0, '\u6211\u4eec\u600e\u4e48\u76f8\u4fe1\u4f60\u80fd\u625b\u4f4f\u5de5\u4f5c\u91cc\u7684\u56f0\u96be'),)
        captioned = frame(0, timestamp=1.0, lines=('\u6211\u4eec\u600e\u4e48\u76f8\u4fe1\u4f60\u80fd\u625b\u4f4f\u5de5\u4f5c\u91cc\u7684\u56f0\u96be',))

        total, novel, overlap = visual_states.transcript_alignment(captioned, spoken)

        self.assertGreater(total, 0)
        self.assertEqual(novel, 0)
        self.assertEqual(overlap, 1.0)

    def test_text_absent_from_the_speech_is_novel(self) -> None:
        spoken = ((0.0, 3.0, '\u6211\u4eec\u6765\u770b\u4e00\u4e0b\u8fd9\u4e2a'),)
        document = frame(0, timestamp=1.0, lines=('\u5feb\u901f\u6392\u5e8f\u539f\u7406', '\u54c8\u5e0c\u51b2\u7a81\u89e3\u51b3\u65b9\u6848'))

        total, novel, overlap = visual_states.transcript_alignment(document, spoken)

        self.assertEqual(novel, total)
        self.assertEqual(overlap, 0.0)

    def test_alignment_ignores_case_and_width_differences(self) -> None:
        spoken = ((0.0, 2.0, "FastAPI and Postgres"),)
        shown = frame(0, timestamp=1.0, lines=('\uff46\uff41\uff53\uff54\uff41\uff50\uff49 postgres',))

        _, novel, overlap = visual_states.transcript_alignment(shown, spoken)

        self.assertEqual(novel, 0)
        self.assertEqual(overlap, 1.0)

    def test_repeated_on_screen_terms_are_not_covered_by_one_mention(self) -> None:
        spoken = ((0.0, 2.0, "retry"),)
        shown = frame(0, timestamp=1.0, lines=("retry retry retry",))

        total, novel, _ = visual_states.transcript_alignment(shown, spoken)

        # Three occurrences, one spoken: two remain novel.
        self.assertEqual(total, 15)
        self.assertEqual(novel, 10)

    def test_speech_outside_the_window_does_not_cover_the_frame(self) -> None:
        spoken = ((300.0, 302.0, '\u54c8\u5e0c\u51b2\u7a81'),)
        shown = frame(0, timestamp=1.0, lines=('\u54c8\u5e0c\u51b2\u7a81',))

        _, novel, overlap = visual_states.transcript_alignment(shown, spoken)

        self.assertGreater(novel, 0)
        self.assertEqual(overlap, 0.0)

    def test_a_frame_without_text_reports_zero_rather_than_dividing(self) -> None:
        self.assertEqual(
            visual_states.transcript_alignment(frame(0), ((0.0, 1.0, '\u8bf4\u8bdd'),)),
            (0, 0, 0.0),
        )

    def test_annotation_attaches_measurements_to_every_frame(self) -> None:
        spoken = ((0.0, 4.0, '\u7b2c\u4e00\u53e5'),)
        frames = [
            frame(0, timestamp=0.0, lines=('\u7b2c\u4e00\u53e5',)),
            frame(1, timestamp=2.0, lines=('\u5b8c\u5168\u4e0d\u540c\u7684\u753b\u9762\u6587\u5b57',)),
        ]

        visual_states.annotate_transcript_alignment(frames, spoken)

        self.assertEqual(frames[0].transcript_novelty, 0)
        self.assertGreater(frames[1].transcript_novelty, 0)
        self.assertEqual(frames[0].ocr_char_count, 3)


class PreviewSafetyNetTests(unittest.TestCase):
    """A brief document inside one long visual scene must not be dropped."""

    def _talking_head_with_one_document(self) -> list[Frame]:
        # Visually near-identical frames: one continuous scene by dHash.
        frames = [
            frame(i, timestamp=float(i * 10), phash=i, lines=('\u5728\u8bb2\u8bdd',))
            for i in range(8)
        ]
        # Same visual neighbourhood, but the screen shows an unspoken document.
        frames[4] = frame(
            4,
            timestamp=40.0,
            phash=4,
            lines=('\u5206\u5e03\u5f0f\u4e00\u81f4\u6027', '\u4e24\u9636\u6bb5\u63d0\u4ea4', '\u5411\u91cf\u65f6\u949f', '\u79df\u7ea6\u4e0e\u5fc3\u8df3\u68c0\u6d4b'),
        )
        return frames

    def test_unspoken_document_enters_preview_despite_one_scene(self) -> None:
        frames = self._talking_head_with_one_document()
        spoken = tuple((f.timestamp, f.timestamp + 5, '\u5728\u8bb2\u8bdd') for f in frames)
        visual_states.annotate_transcript_alignment(frames, spoken)

        preview = visual_states.derive_preview(frames, spoken_intervals=spoken)

        self.assertIn(4, {f.index for f in preview})

    def test_the_safety_net_does_not_fire_on_captions_alone(self) -> None:
        frames = [
            frame(i, timestamp=float(i * 10), phash=i, lines=(f"\u7b2c{i}\u53e5\u8bdd",))
            for i in range(8)
        ]
        spoken = tuple((f.timestamp, f.timestamp + 5, f"\u7b2c{i}\u53e5\u8bdd")
                       for i, f in enumerate(frames))
        visual_states.annotate_transcript_alignment(frames, spoken)

        preview = visual_states.derive_preview(frames, spoken_intervals=spoken)

        self.assertLess(len(preview), len(frames))

    def test_novelty_never_removes_a_canonical_state(self) -> None:
        """Alignment is a measurement; canonical retention must ignore it."""
        frames = self._talking_head_with_one_document()
        spoken = tuple((f.timestamp, f.timestamp + 5, '\u5728\u8bb2\u8bdd') for f in frames)
        visual_states.annotate_transcript_alignment(frames, spoken)

        self.assertEqual(len(frames), 8)
        self.assertTrue(all(f.ocr_char_count >= 0 for f in frames))

    def test_preview_stays_chronological_after_the_safety_net(self) -> None:
        frames = self._talking_head_with_one_document()
        spoken = tuple((f.timestamp, f.timestamp + 5, '\u5728\u8bb2\u8bdd') for f in frames)
        visual_states.annotate_transcript_alignment(frames, spoken)

        preview = visual_states.derive_preview(frames, spoken_intervals=spoken)

        stamps = [f.timestamp for f in preview]
        self.assertEqual(stamps, sorted(stamps))


if __name__ == "__main__":
    unittest.main()
