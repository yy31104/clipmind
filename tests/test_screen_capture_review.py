from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from clipmind import compact, evidence, media, visual_states
from clipmind.asr import Segment, Transcript
from clipmind.media import Frame
from clipmind.sources.base import MediaAsset


class ScreenCaptureReviewTests(unittest.TestCase):
    def test_failed_lookahead_does_not_erase_the_current_local_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = []
            for index, line_count in enumerate((1, 2, 2, 2)):
                image = Image.new("RGB", (640, 360), "#171717")
                drawing = ImageDraw.Draw(image)
                drawing.text((35, 25), "Persistent page", fill="white")
                for line in range(line_count):
                    drawing.text((35, 130 + line * 18),
                                 f"send_message(connection, {line}, data);", fill="white")
                path = root / f"frame-{index}.png"
                image.save(path)
                frames.append(Frame(index, index * 3.0, path,
                                    source_timestamp=index * 3.0 + 0.1))
            expected = [frame.timestamp for frame in media.dedupe(frames)]
            self.assertEqual(expected, [0.0, 3.0])
            signature = media._detail_signature
            failed = False

            def fail_future_read_once(path):
                nonlocal failed
                if path.name == "frame-2.png" and not failed:
                    failed = True
                    raise OSError("synthetic lookahead read failure")
                return signature(path)

            with patch.object(media, "_detail_signature", side_effect=fail_future_read_once):
                retained = media.dedupe(frames)
            self.assertTrue(failed)
            self.assertEqual([frame.timestamp for frame in retained], expected)
            self.assertEqual(retained[1].source_timestamp, 3.1)

    def test_a_longer_assignment_replacing_a_value_is_not_a_build(self):
        frames = [Frame(0, 0, Path("first.jpg"), lines=("limit = 1",)),
                  Frame(1, 12, Path("second.jpg"),
                        lines=("limit = 10", "print(limit)"))]
        self.assertEqual(visual_states.group_progressive_builds(frames), [])

    def test_compact_export_keeps_capture_times_and_legacy_unknowns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            all_dir = original / "visual_states" / "all"
            all_dir.mkdir(parents=True)
            (original / "visual_states" / "preview").mkdir()
            frames = []
            for index, capture in enumerate((0.25, 1.25, None)):
                path = all_dir / f"state-{index}.jpg"
                image = Image.new("RGB", (320, 180), "white")
                text = f"const answer = {index};"
                ImageDraw.Draw(image).text((25, 45), text, fill="black")
                image.save(path)
                frames.append(Frame(index, float(index), path, source_timestamp=capture,
                                    text=text, lines=(text,), ocr_layout=(
                                        {"text": text, "confidence": 0.99,
                                         "bbox": [0.08, 0.25, 0.6, 0.1]},)))
            item = MediaAsset(original / "absent.mp4", {
                "id": "fixture", "title": "Synthetic screen provenance fixture",
                "duration": 3.0, "webpage_url": "https://example.test/fixture",
                "_clipmind_platform": "generic", "_clipmind_strategy": "fixture"})
            speech = Transcript([Segment(0, 3, "A short synthetic example.")], engine="fixture")
            evidence.write_pack(original, item, speech, frames, [], [], candidate_frame_count=3)
            (original / "job.json").write_text(json.dumps({"version": 1, "job": {
                "id": "original", "status": "done", "result": {"asr_engine": "fixture"}}}))
            timeline = original / "visual_timeline.jsonl"
            rows = [json.loads(line) for line in timeline.read_text().splitlines()]
            # A pre-provenance package has no capture-time key, not a measured zero.
            rows[-1].pop("source_timestamp")
            timeline.write_text("".join(json.dumps(row) + "\n" for row in rows))
            before = hashlib.sha256(timeline.read_bytes()).hexdigest()
            report = compact.build_compact_pack(original, root / "reading")
            exported = [json.loads(line) for line in
                        (Path(report["path"]) / "visual_timeline.jsonl").read_text().splitlines()]
            self.assertEqual([row["source_timestamp"] for row in exported], [0.25, 1.25, None])
            self.assertEqual(hashlib.sha256(timeline.read_bytes()).hexdigest(), before)

    def test_new_diagrams_with_a_repeated_title_are_not_speaker_motion(self):
        for title_y in (0.12, 0.02):
            with self.subTest(title_y=title_y), tempfile.TemporaryDirectory() as directory:
                frames = []
                for index in range(3):
                    path = Path(directory) / f"diagram-{index}.png"
                    image = Image.new("RGB", (640, 360), "white")
                    drawing = ImageDraw.Draw(image)
                    drawing.text((40, int(title_y * 360)), "Architecture", fill="black")
                    drawing.rectangle((80 + 100 * index, 150, 180 + 100 * index, 260),
                                      fill="black")
                    image.save(path)
                    frames.append(Frame(index, float(index), path, lines=("Architecture",),
                                        ocr_layout=({"text": "Architecture",
                                                     "bbox": [0.0625, title_y, 0.25, 0.04]},)))
                visual_states.annotate_change_kinds(frames)
                self.assertEqual([frame.change_kind for frame in frames],
                                 ["content", "uncertain", "uncertain"])
                self.assertEqual(len(frames), 3)

    def test_new_diagram_and_shifted_text_are_not_only_a_camera_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = []
            lines = ("System architecture", "Request path")
            for index, x in enumerate((0.1, 0.25)):
                path = Path(directory) / f"page-{index}.png"
                image = Image.new("RGB", (640, 360), "white")
                drawing = ImageDraw.Draw(image)
                for line_index, text in enumerate(lines):
                    drawing.text((int(x * 640), 40 + line_index * 40), text, fill="black")
                if index == 0:
                    drawing.ellipse((80, 155, 230, 310), fill="black")
                else:
                    drawing.rectangle((380, 160, 560, 290), fill="black")
                image.save(path)
                frames.append(Frame(index, float(index), path, lines=lines, ocr_layout=tuple(
                    {"text": text, "bbox": [x, (40 + line_index * 40) / 360, 0.35, 0.04]}
                    for line_index, text in enumerate(lines))))
            visual_states.annotate_change_kinds(frames)
            self.assertEqual([frame.change_kind for frame in frames], ["content", "uncertain"])
            self.assertEqual(len(frames), 2)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs local FFmpeg")
    def test_variable_rate_frames_keep_source_times_instead_of_a_uniform_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            for index, (colour, duration) in enumerate(zip(
                    ("white", "red", "blue", "green"), (0.12, 0.6, 0.2, 0.68))):
                Image.new("RGB", (64, 64), colour).save(root / f"frame-{index}.png")
                rows.extend((f"file 'frame-{index}.png'", f"duration {duration}"))
            rows.append("file 'frame-3.png'")
            (root / "frames.txt").write_text("\n".join(rows) + "\n")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "concat",
                            "-safe", "0", "-i", str(root / "frames.txt"),
                            "-fps_mode", "vfr", "-c:v", "ffv1", str(root / "source.mkv")],
                           check=True)
            sampled = asyncio.run(media.sample_frames(
                root / "source.mkv", root / "samples", fps=2, width=64))
            self.assertEqual(len(sampled), 3)
            for frame, expected in zip(sampled, (0.0, 0.72, 1.6)):
                self.assertIsNotNone(frame.source_timestamp)
                self.assertAlmostEqual(frame.source_timestamp, expected, places=3)
                self.assertAlmostEqual(frame.timestamp, expected, places=3)
            for frame in sampled:
                exact = root / f"source-{frame.index}.jpg"
                asyncio.run(media.extract_still(root / "source.mkv", frame.source_timestamp, exact))
                with Image.open(frame.path) as sampled_image, Image.open(exact) as source_image:
                    self.assertTrue(all(abs(a - b) < 8 for a, b in zip(
                        sampled_image.getpixel((32, 32)), source_image.getpixel((32, 32)))))


if __name__ == "__main__":
    unittest.main()
