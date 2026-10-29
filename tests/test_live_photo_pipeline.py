"""Real local FFmpeg motion fixtures; no sites, ASR models or user packages."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from clipmind import acquisition, image_media, media, pipeline
from clipmind.config import Settings
from clipmind.sources import MediaAsset


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable")
class LivePhotoPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workdir = self.root / "pack"
        self.originals = []
        for index in range(3):
            path = self.root / f"original-{index}.png"
            Image.new("RGB", (64, 48), (index * 100, 40, 80)).save(path)
            self.originals.append(path)
        self.motion = self.root / "original-motion.mp4"
        await asyncio.to_thread(subprocess.run, [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=4:duration=1.5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=1.5",
            "-t", "1.5", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(self.motion),
        ], check=True, capture_output=True)
        self.ocr_names = []
        self.events = []

        def read_image(path):
            with Image.open(path) as image:
                image.load()
            self.ocr_names.append(path.name)
            return [path.stem]

        self.providers = SimpleNamespace(
            transcript=SimpleNamespace(transcribe=AsyncMock(side_effect=AssertionError("No LivePhoto ASR was acquired"))),
            text=SimpleNamespace(read_text=read_image),
            diarization=SimpleNamespace(available=lambda: True,
                                        assign=AsyncMock(side_effect=AssertionError("No LivePhoto speech to diarize"))),
        )
        self.config = Settings(sample_fps=2, sample_width=96, evidence_width=256)

    def originals_snapshot(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [*self.originals, self.motion]}

    def fake_fetch(self, motions):
        async def fetch(url, workdir, **kwargs):
            owned = acquisition.open_workspace(workdir, strategy="synthetic LivePhoto fixture")
            images, copied_motions = [], []
            for index, original in enumerate(self.originals):
                image = owned / f"page-{index}.png"
                shutil.copy2(original, image)
                images.append(image)
                motion = motions[index]
                if motion is None:
                    copied_motions.append(None)
                else:
                    copied = owned / f"page-{index}-motion.mp4"
                    shutil.copy2(motion, copied)
                    copied_motions.append(copied)
            return MediaAsset(info={
                "id": "livephoto-fixture", "title": "Synthetic LivePhoto lesson",
                "_clipmind_platform": "douyin", "_clipmind_media_type": "image_collection",
                "_clipmind_image_audio": "not_acquired", "duration": 0,
            }, image_paths=images, image_motion_paths=copied_motions)
        return fetch

    def report(self, *event):
        self.events.append(event)

    async def process(self, motions=None, *, config=None, options=None):
        motions = motions or (self.motion, None, self.motion)
        with (
            patch.object(pipeline, "fetch", side_effect=self.fake_fetch(motions)),
            patch.object(media, "extract_audio", side_effect=AssertionError("Do not claim unacquired LivePhoto audio")),
        ):
            return await pipeline.process(
                "https://www.douyin.com/note/123", self.workdir, pipeline.Pools(), self.report,
                config=config or self.config, providers=self.providers, options=options,
            )

    def assert_clean(self):
        self.assertEqual(acquisition.leftovers(self.workdir), [])
        self.assertFalse((self.workdir / "samples").exists())
        self.assertFalse((self.workdir / "evidence_samples").exists())
        self.assertFalse((self.workdir / "audio.wav").exists())
        self.providers.transcript.transcribe.assert_not_awaited()
        self.providers.diarization.assign.assert_not_awaited()

    async def test_original_pages_and_all_motion_samples_keep_distinct_order_and_provenance(self):
        before = self.originals_snapshot()
        result = await self.process()
        timeline = rows(self.workdir / "visual_timeline.jsonl")
        ocr = rows(self.workdir / "ocr.jsonl")
        self.assertEqual([row["visual_state_index"] for row in timeline], list(range(9)))
        self.assertEqual([row["source_image_index"] for row in timeline], [0, 0, 0, 0, 1, 2, 2, 2, 2])
        self.assertEqual([row.get("source_motion_timestamp") for row in timeline],
                         [None, 0, 0.5, 1, None, None, 0, 0.5, 1])
        self.assertEqual([row["visual_state_index"] for row in ocr], list(range(9)))
        self.assertEqual([row.get("source_motion_timestamp") for row in ocr],
                         [row.get("source_motion_timestamp") for row in timeline])
        self.assertTrue(all(row["start"] == row["end"] == 0 for row in timeline))
        self.assertTrue(all(not row["timestamp_available"] for row in timeline))
        self.assertEqual(result["canonical_visual_state_count"], 9)
        self.assertEqual(result["preview_frame_count"], 9)
        self.assertEqual(len(self.ocr_names), 9)
        self.assertEqual((self.workdir / "transcript.jsonl").read_bytes(), b"")
        for row in timeline:
            path = self.workdir / row["file"]
            if row.get("source_motion_timestamp") is None:
                self.assertEqual(path.read_bytes(), self.originals[row["source_image_index"]].read_bytes())
                self.assertEqual(path.name, f'image-{row["source_image_index"] + 1:05d}.png')
            else:
                with Image.open(path) as image:
                    self.assertEqual(image.width, 256)  # Canonical is not the cheap 96 px sample.
                self.assertIn("-motion-", path.name)
        source = json.loads((self.workdir / "source.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.workdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(source["source_image_count"], 3)
        self.assertEqual(source["source_motion_count"], 2)
        self.assertEqual(source["duration"], 0)
        self.assertFalse(source["duration_available"])
        self.assertEqual(source["audio_status"], "not_acquired")
        self.assertEqual(source["motion_audio_status"], "not_acquired")
        self.assertEqual(source["motion_time_basis"], "clip_relative_seconds")
        self.assertEqual(source["motion_sample_fps"], 2)
        self.assertEqual(manifest["completeness"]["transcript"], "unavailable")
        self.assertEqual(manifest["diagnostics"]["motion_sampling"], "complete")
        self.assertIn("Motion at 00:00.500 (clip-relative)", (self.workdir / "evidence.md").read_text(encoding="utf-8"))
        self.assertEqual(before, self.originals_snapshot())
        self.assert_clean()

    async def test_budget_counts_motion_states_and_stops_before_high_resolution_or_ocr(self):
        actual_sample = media.sample_frames
        widths = []

        async def only_cheap(*args, **kwargs):
            widths.append(kwargs.get("width"))
            self.assertEqual(kwargs.get("width"), 96)
            return await actual_sample(*args, **kwargs)

        config = Settings(sample_fps=2, sample_width=96, evidence_width=256, max_canonical_states=3)
        with patch.object(media, "sample_frames", side_effect=only_cheap):
            with self.assertRaises(pipeline.preflight.CostLimitExceeded) as raised:
                await self.process(config=config)
        self.assertEqual(widths, [96, 96])
        self.assertEqual(raised.exception.details["estimated_canonical_states"], 9)
        self.assertEqual(self.ocr_names, [])
        self.assertFalse((self.workdir / "manifest.json").exists())
        self.assertFalse((self.workdir / "visual_states" / "all").exists())
        self.assert_clean()

    async def test_nonintegral_short_clip_keeps_final_sampling_interval(self):
        tail = self.root / "two-point-six-seven-seconds.mp4"
        await asyncio.to_thread(subprocess.run, [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=30:duration=2.666667",
            "-an", "-c:v", "mpeg4", str(tail),
        ], check=True, capture_output=True)
        await self.process((tail, None, None))
        dynamic = [row["source_motion_timestamp"]
                   for row in rows(self.workdir / "visual_timeline.jsonl")
                   if "source_motion_timestamp" in row]
        self.assertEqual(dynamic, [0, 0.5, 1, 1.5, 2, 2.5])
        self.assert_clean()

    async def test_explicit_synthetic_force_preserves_all_motion_evidence_without_truncation(self):
        config = Settings(sample_fps=2, sample_width=96, evidence_width=256, max_canonical_states=3)
        result = await self.process(config=config, options={"force": True})
        self.assertEqual(result["canonical_visual_state_count"], 9)
        self.assertTrue(result["preflight"]["forced"])
        self.assertFalse(result["preflight"]["within_budget"])
        self.assert_clean()

    async def test_one_corrupt_motion_rejects_whole_collection_after_valid_page_decodes(self):
        corrupt = self.root / "corrupt-motion.mp4"
        corrupt.write_bytes(b"not a video stream")
        before = self.originals_snapshot()
        with self.assertRaisesRegex(image_media.ImageMotionError, "第 2 页"):
            await self.process((self.motion, corrupt, None))
        self.assertEqual(self.ocr_names, [])
        self.assertFalse((self.workdir / "manifest.json").exists())
        self.assertEqual(before, self.originals_snapshot())
        self.assertEqual(corrupt.read_bytes(), b"not a video stream")
        self.assert_clean()

    async def test_cancellation_cleans_written_motion_samples_without_deleting_originals(self):
        actual_sample = media.sample_frames
        sampled = asyncio.Event()
        before = self.originals_snapshot()

        async def pause_after_decode(*args, **kwargs):
            frames = await actual_sample(*args, **kwargs)
            self.assertTrue(frames)
            sampled.set()
            await asyncio.Future()

        with patch.object(media, "sample_frames", side_effect=pause_after_decode):
            task = asyncio.create_task(self.process())
            await asyncio.wait_for(sampled.wait(), timeout=10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse((self.workdir / "manifest.json").exists())
        self.assertEqual(before, self.originals_snapshot())
        self.assert_clean()

    async def test_low_and_high_motion_state_mismatch_never_publishes_partial_pack(self):
        actual_sample = image_media.sample_collection

        async def mismatch(*args, **kwargs):
            frames = await actual_sample(*args, **kwargs)
            return frames[:-1] if kwargs.get("width") == 256 else frames

        with patch.object(image_media, "sample_collection", side_effect=mismatch):
            with self.assertRaisesRegex(media.MediaError, "did not match"):
                await self.process()
        self.assertFalse((self.workdir / "manifest.json").exists())
        self.assertEqual(self.ocr_names, [])
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
