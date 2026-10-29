"""Local-only image and silent-video contracts; no model downloads or sites."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw, ImageFont

from clipmind import acquisition, evidence, image_media, media, pipeline
from clipmind.asr import Segment, Transcript
from clipmind.config import Settings
from clipmind.sources import MediaAsset


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class ImageEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workdir = self.root / "pack"
        self.events = []
        self.asr_calls = []
        self.ocr_calls = []

        async def speech(audio):
            self.asr_calls.append(audio)
            raise AssertionError("Image posts and silent video must not invoke ASR")

        def read_image(path):
            with Image.open(path) as image:
                image.load()
            self.ocr_calls.append(path.name)
            return [f"Read page {len(self.ocr_calls)}"]

        self.providers = SimpleNamespace(
            transcript=SimpleNamespace(name="must-not-run", transcribe=speech),
            text=SimpleNamespace(name="fixture-ocr", read_text=read_image),
            diarization=SimpleNamespace(available=lambda: True,
                                        assign=AsyncMock(side_effect=AssertionError("No speech to diarize"))),
        )

    def tearDown(self):
        self.temp.cleanup()

    def report(self, stage, progress, note=""):
        self.events.append((stage, progress, note))

    def make_image(self, name="page.png", text="VALUABLE DOCUMENT", *, color="white"):
        path = self.root / name
        image = Image.new("RGB", (640, 360), color)
        draw = ImageDraw.Draw(image)
        draw.text((30, 50), text, fill="black", font=ImageFont.load_default(size=30))
        image.save(path)
        return path

    def config(self, **kwargs):
        return Settings(sample_fps=1, sample_width=320, evidence_width=640, **kwargs)

    async def run_images(self, originals, *, config=None, options=None, platform="douyin"):
        async def fetch_images(url, dest, **kwargs):
            owned = acquisition.open_workspace(dest, strategy="image fixture")
            paths = []
            for index, original in enumerate(originals):
                target = owned / f"page-{index}{original.suffix}"
                shutil.copy2(original, target)
                paths.append(target)
            return MediaAsset(info={
                "id": "image-fixture", "title": "Image document",
                "duration": 0, "_clipmind_platform": platform,
                "_clipmind_strategy": "image fixture",
            }, image_paths=paths)

        with (
            patch.object(pipeline, "fetch", side_effect=fetch_images),
            patch.object(media, "sample_frames", side_effect=AssertionError("Images are not video")),
            patch.object(media, "extract_audio", side_effect=AssertionError("Images have no acquired audio")),
        ):
            return await pipeline.process(
                "https://www.douyin.com/note/123", self.workdir,
                pipeline.Pools(), self.report, providers=self.providers,
                config=config or self.config(), options=options,
            )

    async def test_image_collection_keeps_every_original_page_without_made_up_time(self):
        first = self.make_image("first.png", "return value > 1")
        second = self.make_image("second.webp", "return value > 2")
        third = self.make_image("third.jpg", "PHOTO", color="silver")
        originals = [first, second, third, first]
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in originals]
        result = await self.run_images(originals)

        self.assertEqual(result["canonical_visual_state_count"], 4)
        self.assertEqual(result["preview_frame_count"], 4)
        self.assertEqual(len(self.ocr_calls), 4)
        self.assertEqual(self.asr_calls, [])
        self.providers.diarization.assign.assert_not_called()
        self.assertEqual((self.workdir / "transcript.jsonl").read_bytes(), b"")
        timeline = rows(self.workdir / "visual_timeline.jsonl")
        self.assertEqual([row["source_image_index"] for row in timeline], [0, 1, 2, 3])
        self.assertTrue(all(row["start"] == row["end"] == 0 for row in timeline))
        self.assertTrue(all(row["time_basis"] == "image_order" and not row["timestamp_available"] for row in timeline))
        self.assertEqual(hashes, [hashlib.sha256((self.workdir / row["file"]).read_bytes()).hexdigest() for row in timeline])
        self.assertEqual(hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in originals])
        self.assertEqual([Path(row["file"]).suffix for row in timeline], [".png", ".webp", ".jpg", ".png"])
        manifest = json.loads((self.workdir / "manifest.json").read_text(encoding="utf-8"))
        source = json.loads((self.workdir / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["media_type"], "image_collection")
        self.assertFalse(source["duration_available"])
        self.assertEqual(source["duration"], 0)
        self.assertEqual(manifest["completeness"]["transcript"], "unavailable")
        self.assertEqual(manifest["completeness"]["word_timing"], "unavailable")
        self.assertEqual(manifest["diagnostics"]["audio_status"], "not_acquired")
        self.assertIsNone(manifest["diagnostics"]["asr_error"])
        self.assertEqual(manifest["completeness"]["ocr"], "complete")
        self.assertEqual(acquisition.leftovers(self.workdir), [])
        self.assertIn("## Image 4", (self.workdir / "evidence.md").read_text(encoding="utf-8"))

    async def test_budget_refuses_before_ocr_without_truncating_or_deleting_originals(self):
        originals = [self.make_image("one.png"), self.make_image("two.png")]
        with self.assertRaises(pipeline.preflight.CostLimitExceeded):
            await self.run_images(originals, config=self.config(max_canonical_states=1))
        self.assertEqual(self.ocr_calls, [])
        self.assertFalse((self.workdir / "manifest.json").exists())
        self.assertFalse((self.workdir / "visual_states" / "all").exists())
        self.assertTrue(all(path.is_file() for path in originals))
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    async def test_local_still_never_reports_a_speech_model_failure(self):
        await self.run_images([self.make_image()], platform="local")
        manifest = json.loads((self.workdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["diagnostics"]["audio_status"], "not_applicable")
        self.assertEqual(manifest["completeness"]["transcript"], "unavailable")

    async def test_real_local_still_path_bypasses_ffmpeg_and_preserves_original(self):
        source = self.make_image("local-page.png")
        original = source.read_bytes()
        with (
            patch.object(media, "sample_frames", side_effect=AssertionError("Still images need no FFmpeg")),
            patch.object(media, "extract_audio", side_effect=AssertionError("Still images need no audio extraction")),
        ):
            result = await pipeline.process(
                str(source), self.workdir, pipeline.Pools(), self.report,
                providers=self.providers, config=self.config(),
            )
        self.assertEqual(result["canonical_visual_state_count"], 1)
        self.assertEqual(result["preview_frame_count"], 1)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(rows(self.workdir / "visual_timeline.jsonl")[0]["source_image_index"], 0)
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    @unittest.skipUnless(shutil.which("tesseract"), "Optional Tesseract executable unavailable")
    async def test_real_local_image_with_installed_tesseract_keeps_ocr_separate_from_speech(self):
        from clipmind.ocr import TesseractTextRecognizer
        config = self.config(tesseract_languages="eng", ocr_provider="tesseract")
        recognizer = TesseractTextRecognizer(config)
        if not recognizer.available():
            self.skipTest("Optional Tesseract Python binding or English language data unavailable")
        source = self.make_image("ocr-page.png", "SCREENSHOT DOCUMENT")
        self.providers.text = recognizer
        result = await pipeline.process(
            str(source), self.workdir, pipeline.Pools(), self.report,
            providers=self.providers, config=config,
        )
        text = rows(self.workdir / "ocr.jsonl")[0]["text"]
        self.assertIn("DOCUMENT", text.upper())
        self.assertEqual((self.workdir / "transcript.jsonl").read_bytes(), b"")
        self.assertEqual(result["preview_frame_count"], 1)
        self.assertEqual(self.asr_calls, [])
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    async def test_original_image_bytes_count_towards_pack_budget(self):
        path = self.root / "large-page.png"
        Image.effect_noise((640, 360), 100).convert("RGB").save(path)
        self.assertGreater(path.stat().st_size * 2, 200_000)
        with self.assertRaises(pipeline.preflight.CostLimitExceeded) as raised:
            await self.run_images([path], config=self.config(max_estimated_pack_mb=0.2))
        self.assertIn("pack_mb", raised.exception.details["exceeded"])
        self.assertGreater(raised.exception.details["estimated_pack_mb"], 0.2)
        self.assertEqual(self.ocr_calls, [])
        self.assertTrue(path.is_file())

    async def test_one_corrupt_page_refuses_partial_pack_and_preserves_sources(self):
        good = self.make_image()
        corrupt = self.root / "broken.png"
        corrupt.write_bytes(b"this is not an image")
        with self.assertRaisesRegex(media.MediaError, "Source image 2"):
            await self.run_images([good, corrupt])
        self.assertTrue(good.is_file())
        self.assertTrue(corrupt.is_file())
        self.assertFalse((self.workdir / "manifest.json").exists())
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    async def test_truncated_jpeg_header_cannot_publish_a_complete_image_pack(self):
        good = self.make_image()
        truncated = self.make_image("truncated.jpg")
        truncated.write_bytes(truncated.read_bytes()[:-8])
        original = truncated.read_bytes()
        # JPEG.verify() does not decode pixels, so this input passed the old
        # header-only gate and was merely tagged as a failed perceptual hash.
        with Image.open(truncated) as image:
            image.verify()
        with self.assertRaisesRegex(media.MediaError, "Source image 2"):
            await self.run_images([good, truncated])
        self.assertEqual(truncated.read_bytes(), original)
        self.assertEqual(self.ocr_calls, [])
        self.assertFalse((self.workdir / "manifest.json").exists())
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    def test_readable_page_survives_a_hash_backend_failure(self):
        source = self.make_image()
        with patch.object(image_media, "dhash", side_effect=RuntimeError("backend failed")):
            frames = image_media.candidates([source])
        self.assertEqual(len(frames), 1)
        self.assertIn("RuntimeError", frames[0].dedupe_warning)

    async def test_video_output_failure_is_not_mistaken_for_no_audio(self):
        with patch.object(media, "_ffmpeg", side_effect=media.MediaError("Output file: Permission denied")):
            with self.assertRaisesRegex(media.MediaError, "Permission denied"):
                await media.extract_audio(self.root / "video.mp4", self.root / "audio.wav")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable")
    async def test_very_short_real_audio_is_not_reported_as_absent(self):
        source = self.root / "ten-milliseconds.wav"
        with wave.open(str(source), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 160)
        output = self.root / "extracted.wav"
        result = await media.extract_audio(source, output)
        self.assertEqual(result, output)
        self.assertLess(output.stat().st_size, 1024)
        with wave.open(str(output), "rb") as handle:
            self.assertEqual(handle.getnframes(), 160)

    async def test_missing_audio_output_is_an_error_not_a_silent_source(self):
        with patch.object(media, "_ffmpeg", new=AsyncMock()):
            with self.assertRaisesRegex(media.MediaError, "without a readable output"):
                await media.extract_audio(self.root / "empty.mp4", self.root / "audio.wav")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
    async def test_real_local_audio_transcribes_without_invoking_visual_stages(self):
        source = self.root / "audio-only.wav"
        with wave.open(str(source), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)
        original = source.read_bytes()

        async def transcribe(audio):
            self.asr_calls.append(audio)
            return Transcript([Segment(0, 0.1, "Provider contract fixture")], engine="test-asr")

        self.providers.transcript.transcribe = transcribe
        # No diarizer configured for this contract fixture.
        self.providers.diarization.available = lambda: False
        with (
            patch.object(media, "sample_frames", side_effect=AssertionError("Audio has no frames")),
            patch.object(pipeline.visual_states, "annotate", side_effect=AssertionError("Audio has no OCR input")),
        ):
            result = await pipeline.process(
                str(source), self.workdir, pipeline.Pools(), self.report,
                providers=self.providers, config=self.config(),
            )
        self.assertEqual(len(self.asr_calls), 1)
        self.assertEqual(result["canonical_visual_state_count"], 0)
        self.assertEqual(result["preview_frame_count"], 0)
        self.assertEqual(self.ocr_calls, [])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(rows(self.workdir / "transcript.jsonl")), 1)
        self.assertEqual(rows(self.workdir / "ocr.jsonl"), [])
        manifest = json.loads((self.workdir / "manifest.json").read_text(encoding="utf-8"))
        provenance = json.loads((self.workdir / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["media_type"], "audio")
        # The canonical extraction is complete with zero expected images;
        # source type and diagnostics explicitly say the visual modality is
        # not applicable, without extending the v1 manifest's status enum.
        self.assertEqual(manifest["completeness"]["visual_states"], "complete")
        self.assertEqual(manifest["completeness"]["ocr"], "unavailable")
        self.assertEqual(manifest["completeness"]["transcript"], "complete")
        self.assertEqual(manifest["diagnostics"]["visual_status"], "not_applicable")
        self.assertTrue((self.workdir / "visual_states" / "all").is_dir())
        self.assertTrue((self.workdir / "visual_states" / "preview").is_dir())
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
    async def test_short_animated_gif_retains_real_first_frame_without_speech(self):
        source = self.root / "short.gif"
        Image.new("RGB", (64, 48), "red").save(
            source, save_all=True, append_images=[Image.new("RGB", (64, 48), "blue")],
            duration=40, loop=0,
        )
        original = source.read_bytes()
        result = await pipeline.process(
            str(source), self.workdir, pipeline.Pools(), self.report,
            providers=self.providers, config=self.config(),
        )
        self.assertGreater(result["canonical_visual_state_count"], 0)
        self.assertGreater(result["preview_frame_count"], 0)
        self.assertEqual(self.asr_calls, [])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(result["visual_states"][0]["timestamp"], 0)
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    async def test_decoder_with_no_output_does_not_publish_empty_success(self):
        with patch.object(media, "_ffmpeg", new=AsyncMock()):
            with self.assertRaisesRegex(media.MediaError, "No readable visual frame"):
                await media.sample_frames(self.root / "empty.mp4", self.root / "samples")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
    async def test_real_local_silent_slideshow_produces_visual_pack_without_asr(self):
        first = self.make_image("slide_01.png", "return value > 1")
        self.make_image("slide_02.png", "return value > 2", color="silver")
        video = self.root / "silent.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", "1",
            "-i", str(self.root / "slide_%02d.png"), "-an", "-c:v", "mpeg4",
            "-pix_fmt", "yuv420p", str(video),
        ], check=True, capture_output=True)
        source_hash = hashlib.sha256(video.read_bytes()).hexdigest()
        result = await pipeline.process(
            str(video), self.workdir, pipeline.Pools(), self.report,
            providers=self.providers, config=self.config(dedupe_threshold=0),
        )
        self.assertGreater(result["canonical_visual_state_count"], 0)
        self.assertGreater(result["preview_frame_count"], 0)
        self.assertGreater(len(self.ocr_calls), 0)
        self.assertEqual(self.asr_calls, [])
        self.providers.diarization.assign.assert_not_called()
        manifest = json.loads((self.workdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["diagnostics"]["audio_status"], "absent")
        self.assertIsNone(manifest["diagnostics"]["asr_error"])
        self.assertEqual(manifest["completeness"]["transcript"], "unavailable")
        self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(acquisition.leftovers(self.workdir), [])
        self.assertTrue(first.is_file())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
    async def test_video_with_audio_but_no_detected_speech_keeps_visual_primary(self):
        photo = self.make_image("still.png", "", color="silver")
        video = self.root / "no-speech.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
            "-i", str(photo), "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-t", "1", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
        ], check=True, capture_output=True)

        async def no_speech(audio):
            self.asr_calls.append(audio)
            return Transcript([], engine="test-asr")

        self.providers.transcript.transcribe = no_speech
        self.providers.text.read_text = lambda path: []
        result = await pipeline.process(
            str(video), self.workdir, pipeline.Pools(), self.report,
            providers=self.providers, config=self.config(),
        )
        self.assertEqual(len(self.asr_calls), 1)
        self.providers.diarization.assign.assert_not_called()
        self.assertGreater(result["preview_frame_count"], 0)
        source = json.loads((self.workdir / "source.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.workdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(source["audio_status"], "present")
        self.assertEqual(source["speech_status"], "not_detected")
        self.assertTrue(source["visual_primary"])
        self.assertEqual(manifest["configuration"]["preview_algorithm"], "visual-only-primary-v1")
        self.assertEqual(manifest["completeness"]["transcript"], "complete")


if __name__ == "__main__":
    unittest.main()
