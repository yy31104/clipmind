"""Audio-labelled remote downloads are dispatched from verified local streams."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

from PIL import Image

from clipmind import acquisition, fetch, media, pipeline
from clipmind.asr import Segment, Transcript
from clipmind.config import Settings
from clipmind.sources.base import SourceAdapter


def probe_result(streams, duration="2.5"):
    return 0, json.dumps({"streams": streams, "format": {"duration": duration}}), ""


class RemoteMediaTypeTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, info, streams, *, duration="2.5"):
        with (
            patch.object(fetch.shutil, "which", return_value="ffprobe"),
            patch.object(fetch, "_run", new=AsyncMock(return_value=probe_result(streams, duration))) as run,
        ):
            result = await fetch._remote_media_info(fetch.AcquiredMedia(Path("owned.mp4"), info))
        return result, run

    async def test_audio_is_verified_without_using_filename_and_preserves_provenance(self):
        info = {"id":"upstream-id", "title":"Remote lesson", "duration":12.75,
                "vcodec":"none", "acodec":"opus", "plugin_value":{"key":"kept"}}
        original = deepcopy(info)
        result, run = await self.resolve(info, [{"codec_type":"audio"}])
        self.assertEqual(result["_clipmind_media_type"], "audio")
        self.assertEqual(result["duration"], 12.75)
        self.assertEqual(result["id"], "upstream-id")
        self.assertEqual(result["plugin_value"], info["plugin_value"])
        self.assertEqual(info, original)
        self.assertIn("stream_disposition=attached_pic", run.await_args.args[0][4])
        self.assertEqual(run.await_args.args[0][-1], "owned.mp4")

    async def test_attached_cover_does_not_make_audio_a_video(self):
        result, _ = await self.resolve({"vcodec":"none"}, [
            {"codec_type":"audio"},
            {"codec_type":"video", "disposition":{"attached_pic":1}},
        ])
        self.assertEqual(result["_clipmind_media_type"], "audio")
        self.assertEqual(result["duration"], 2.5)

    async def test_real_video_cannot_be_hidden_by_audio_metadata_or_cover(self):
        for info in ({"vcodec":"none"}, {"_clipmind_media_type":"audio"},
                     {"vcodec":"none", "requested_formats":[{"vcodec":"h264"}]}):
            with self.subTest(info=info):
                result, _ = await self.resolve(info, [
                    {"codec_type":"audio"},
                    {"codec_type":"video", "disposition":{"attached_pic":1}},
                    {"codec_type":"video", "disposition":{"attached_pic":0}},
                ])
                self.assertEqual(result["_clipmind_media_type"], "video")

    async def test_selected_audio_formats_are_verified_even_with_conflicting_top_label(self):
        for key in ("requested_downloads", "requested_formats"):
            with self.subTest(key=key):
                result, run = await self.resolve({"vcodec":"h264", key:[{"vcodec":"none"}]},
                                                 [{"codec_type":"audio"}])
                self.assertEqual(result["_clipmind_media_type"], "audio")
                run.assert_awaited_once()

    async def test_images_unknown_plugins_and_known_video_keep_existing_contract(self):
        for acquired in (
            fetch.AcquiredMedia(Path("page.png"), {"vcodec":"none"}, (Path("page.png"),)),
            fetch.AcquiredMedia(Path("unknown.bin"), {"id":"plugin-id"}),
            fetch.AcquiredMedia(Path("video.mp4"), {"vcodec":"h264"}),
            fetch.AcquiredMedia(Path("merged.mp4"), {
                "requested_formats":[{"vcodec":"h264"}, {"vcodec":"none"}],
            }),
        ):
            with self.subTest(info=acquired.info), patch.object(fetch, "_run", new=AsyncMock()) as run:
                self.assertEqual(await fetch._remote_media_info(acquired), acquired.info)
                run.assert_not_awaited()

    async def test_missing_probe_is_a_dependency_error(self):
        with (
            patch.object(fetch.shutil, "which", return_value=None),
            patch.object(fetch, "_run", new=AsyncMock()) as run,
        ):
            with self.assertRaises(fetch.FetchError) as caught:
                await fetch._remote_media_info(fetch.AcquiredMedia(Path("audio"), {"vcodec":"none"}))
        self.assertEqual(caught.exception.code, "missing_dependency")
        self.assertIn("ffprobe", caught.exception.user_message)
        run.assert_not_awaited()

    async def test_failed_or_empty_stream_probe_does_not_publish_empty_video(self):
        for response in (
            (1, "", "invalid file"), (0, "{invalid", ""), (0, "[]", ""),
            (0, "{}", ""), probe_result([]), probe_result([None]),
            probe_result([{"codec_type":"data"}]),
            probe_result([{"codec_type":"video", "disposition":{"attached_pic":1}}]),
        ):
            with (
                self.subTest(response=response),
                patch.object(fetch.shutil, "which", return_value="ffprobe"),
                patch.object(fetch, "_run", new=AsyncMock(return_value=response)),
            ):
                with self.assertRaises(fetch.FetchError) as caught:
                    await fetch._remote_media_info(fetch.AcquiredMedia(Path("audio"), {"vcodec":"none"}))
                self.assertEqual(caught.exception.code, "media_probe_failed")

    async def test_probe_cancellation_propagates(self):
        with (
            patch.object(fetch.shutil, "which", return_value="ffprobe"),
            patch.object(fetch, "_run", new=AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await fetch._remote_media_info(fetch.AcquiredMedia(Path("audio"), {"vcodec":"none"}))

    async def test_probe_launch_and_timeout_have_actionable_errors(self):
        for failure, code in ((FileNotFoundError("private path"), "missing_dependency"),
                              (PermissionError("private path"), "missing_dependency"),
                              (asyncio.TimeoutError(), "media_probe_failed")):
            with (
                self.subTest(failure=type(failure).__name__),
                patch.object(fetch.shutil, "which", return_value="ffprobe"),
                patch.object(fetch, "_run", new=AsyncMock(side_effect=failure)),
            ):
                with self.assertRaises(fetch.FetchError) as caught:
                    await fetch._remote_media_info(fetch.AcquiredMedia(Path("audio"), {"vcodec":"none"}))
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private path", caught.exception.user_message)

    async def test_engine_passes_verified_audio_through_plugin_normalization(self):
        class AudioRung:
            key = "audio-fixture"
            def describe(self):
                return "audio fixture"
            async def acquire(self, url, root, config):
                return fetch.AcquiredMedia(root/"owned.mp4", {"id":"upstream", "vcodec":"none"}), None
        engine = fetch.AcquisitionEngine()
        adapter = SourceAdapter("fixture", "example", ("example.test",))
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(engine, "strategies", return_value=[AudioRung()]),
            patch.object(fetch.shutil, "which", return_value="ffprobe"),
            patch.object(fetch, "_run", new=AsyncMock(return_value=probe_result([{"codec_type":"audio"}]))),
        ):
            item = await engine.acquire("https://example.test/audio", Path(temporary)/"job", adapter)
            self.assertEqual(item.media_type, "audio")
            self.assertEqual(item.source_id, "upstream")
            self.assertEqual(item.platform, "example")
            self.assertEqual(item.info["_clipmind_strategy"], "audio fixture")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
class RealRemoteAudioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.audio = self.root/"fixture.wav"
        with wave.open(str(self.audio), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00"*1600)

    def tearDown(self):
        self.temporary.cleanup()

    async def test_real_probe_recognizes_audio_cover_and_video_streams(self):
        cover = self.root/"cover.jpg"
        Image.new("RGB", (64,64), "blue").save(cover)
        covered = self.root/"covered.mp3"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(self.audio), "-i", str(cover), "-map", "0:a", "-map", "1:v",
                        "-c:a", "libmp3lame", "-c:v", "copy", "-disposition:v:0", "attached_pic",
                        str(covered)], check=True, capture_output=True)
        video = self.root/"real-video.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.2", "-an",
                        "-c:v", "mpeg4", str(video)], check=True, capture_output=True)
        for path, expected in ((self.audio,"audio"),(covered,"audio"),(video,"video")):
            with self.subTest(path=path.name):
                info = await fetch._remote_media_info(fetch.AcquiredMedia(path, {"vcodec":"none"}))
                self.assertEqual(info["_clipmind_media_type"], expected)
                local = await fetch._probe_local(path)
                self.assertEqual(local.get("_clipmind_media_type"), "audio" if expected == "audio" else None)
        original = covered.read_bytes()
        item = await fetch.fetch(str(covered), self.root/"local-cover-job", config=Settings())
        self.assertEqual(item.media_type, "audio")
        self.assertEqual(covered.read_bytes(), original)

    async def test_remote_audio_flows_through_pipeline_without_sampling_or_ocr(self):
        original = self.audio.read_bytes()
        source = self.audio
        class AudioRung:
            key = "synthetic-audio"
            def describe(self):
                return "synthetic remote audio"
            async def acquire(self, url, root, config):
                target = root/"source.mp4"
                shutil.copy2(source, target)
                return fetch.AcquiredMedia(target, {"id":"remote-audio", "title":"Audio lesson",
                                                    "vcodec":"none"}), None
        engine = fetch.AcquisitionEngine()
        speech = AsyncMock(return_value=Transcript([Segment(0,0.1,"Speech provider fixture")], engine="test"))
        providers = SimpleNamespace(
            transcript=SimpleNamespace(name="test", transcribe=speech),
            text=SimpleNamespace(name="must-not-run", read_text=lambda path: self.fail("Audio has no OCR")),
            diarization=SimpleNamespace(available=lambda:False),
        )
        workdir = self.root/"pack"
        which = fetch.shutil.which
        with (
            patch.object(fetch.shutil, "which", side_effect=lambda name: "synthetic-yt-dlp" if name == "yt-dlp" else which(name)),
            patch.object(fetch, "_engine", engine),
            patch.object(engine, "strategies", return_value=[AudioRung()]),
            patch.object(media, "sample_frames", side_effect=AssertionError("Audio has no video frames")),
            patch.object(pipeline.visual_states, "annotate", side_effect=AssertionError("Audio has no OCR")),
        ):
            result = await pipeline.process("https://example.test/audio", workdir, pipeline.Pools(),
                                            lambda *args:None, providers=providers, config=Settings())
        speech.assert_awaited_once()
        self.assertEqual(result["canonical_visual_state_count"], 0)
        provenance = json.loads((workdir/"source.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["media_type"], "audio")
        self.assertEqual(provenance["source_id"], "remote-audio")
        self.assertEqual(provenance["url"], "https://example.test/audio")
        self.assertEqual(self.audio.read_bytes(), original)
        self.assertEqual(acquisition.leftovers(workdir), [])


if __name__ == "__main__":
    unittest.main()
