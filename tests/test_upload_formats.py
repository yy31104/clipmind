"""Browser uploads must admit the common media formats the local path can decode."""
from __future__ import annotations

import asyncio
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from PIL import Image

from clipmind import acquisition, media, pipeline, server
from clipmind.asr import Segment, Transcript
from clipmind.config import Settings, WEB_DIR
from clipmind.jobs import JobStore


class _FilePicker(HTMLParser):
    accept: set[str]

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("id") == "file-input":
            self.accept = set(attributes["accept"].split(","))


class _Upload:
    def __init__(self, contents):
        self.contents = contents
        self.read = False

    async def stream(self):
        self.read = True
        yield self.contents


class UploadFormatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = Settings(sample_fps=2, sample_width=160, evidence_width=320)
        self.speech_calls = []

        async def speech(audio):
            self.speech_calls.append(audio)
            return Transcript([Segment(0, .2, "Synthetic provider output")], engine="fixture-asr")

        def read_text(path):
            with Image.open(path) as image:
                image.load()
            return []

        self.providers = SimpleNamespace(
            transcript=SimpleNamespace(name="fixture-asr", transcribe=speech),
            text=SimpleNamespace(name="fixture-ocr", read_text=read_text),
            diarization=SimpleNamespace(available=lambda: False, assign=AsyncMock()),
        )

    def tearDown(self):
        self.temporary.cleanup()

    async def upload(self, source, library):
        store = JobStore(library, config=self.config, providers=self.providers)
        with patch.object(server, "store", store), patch.object(store, "_schedule"):
            return store, await server.upload(_Upload(source.read_bytes()), filename=source.name)

    def test_file_picker_exposes_every_accepted_extension(self):
        picker = _FilePicker()
        picker.feed((WEB_DIR / "index.html").read_text(encoding="utf-8"))
        self.assertTrue(server.MEDIA_EXTENSIONS <= picker.accept)
        self.assertTrue({".mpg", ".ts", ".m2ts", ".wmv", ".aiff", ".wma", ".eac3"} <= picker.accept)

    async def test_common_legacy_and_camera_extensions_are_accepted_without_relabeling(self):
        extensions = (".mpeg", ".mpg", ".mpe", ".m2v", ".ts", ".m2ts", ".mts",
                      ".wmv", ".vob", ".3gp", ".3g2", ".wma", ".aif", ".aiff",
                      ".aifc", ".ac3", ".eac3")
        store = JobStore(self.root / "extension-library", config=self.config, providers=self.providers)
        with patch.object(server, "store", store), patch.object(store, "_schedule"):
            for suffix in extensions:
                with self.subTest(suffix=suffix):
                    job = await server.upload(_Upload(b"upload boundary fixture"), filename="Recording" + suffix.upper())
                    self.assertEqual(Path(job["url"]).suffix, suffix)
                    self.assertEqual(job["options"]["uploaded_filename"], "Recording" + suffix.upper())

    async def test_documents_and_executables_still_report_type_error_before_reading(self):
        store = JobStore(self.root / "rejected-library", config=self.config, providers=self.providers)
        with patch.object(server, "store", store):
            for name in ("notes.pdf", "slides.pptx", "script.exe", "notes.txt"):
                request = _Upload(b"not supported media")
                with self.subTest(name=name), self.assertRaises(HTTPException) as caught:
                    await server.upload(request, filename=name)
                self.assertEqual(caught.exception.status_code, 415)
                self.assertIn("file type", caught.exception.detail)
                self.assertNotIn("install", caught.exception.detail.lower())
                self.assertFalse(request.read)
        self.assertFalse((store.storage.root / ".uploads").exists())

    async def test_extended_format_upload_keeps_existing_size_limit(self):
        store = JobStore(self.root / "limited-library", config=Settings(max_upload_mb=0), providers=self.providers)
        with patch.object(server, "store", store), self.assertRaises(HTTPException) as caught:
            await server.upload(_Upload(b"too large"), filename="camera.m2ts")
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(list((store.storage.root / ".uploads").iterdir()), [])

    async def test_cancelled_upload_removes_partial_file_and_creates_no_job(self):
        class CancelledUpload:
            async def stream(self):
                yield b"partial local upload"
                raise asyncio.CancelledError()

        store = JobStore(self.root / "cancelled-library", config=self.config, providers=self.providers)
        with patch.object(server, "store", store), patch.object(store, "submit") as submit:
            with self.assertRaises(asyncio.CancelledError):
                await server.upload(CancelledUpload(), filename="recording.mp4")
            submit.assert_not_called()
        self.assertEqual(list((store.storage.root / ".uploads").iterdir()), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
    async def test_real_transport_legacy_video_and_audio_uploads_reach_the_correct_pipeline(self):
        # Decode representative container families, rather than merely renaming
        # an MP4 or claiming that admitting its extension proves codec support.
        fixtures = (
            ("clip.mpg", ["-c:v", "mpeg2video", "-f", "mpeg"], False),
            ("clip.ts", ["-c:v", "mpeg2video", "-f", "mpegts"], False),
            ("clip.wmv", ["-c:v", "wmv2", "-f", "asf"], False),
            ("speech.aiff", ["-c:a", "pcm_s16be", "-f", "aiff"], True),
            ("speech.wma", ["-c:a", "wmav2", "-f", "asf"], True),
            ("speech.ac3", ["-c:a", "ac3", "-ar", "48000", "-f", "ac3"], True),
        )
        for index, (name, encoding, audio_only) in enumerate(fixtures):
            with self.subTest(name=name):
                source = self.root / name
                generator = "sine=frequency=440:sample_rate=16000:duration=0.3" if audio_only else "color=c=blue:s=160x120:r=25:d=0.6"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                "-f", "lavfi", "-i", generator, *encoding, str(source)],
                               check=True, capture_output=True)
                before = hashlib.sha256(source.read_bytes()).hexdigest()
                store, job = await self.upload(source, self.root / f"library-{index}")
                uploaded = Path(job["url"])
                pack = store.workdir(job["id"])
                speech_before = len(self.speech_calls)
                result = await pipeline.process(str(uploaded), pack, pipeline.Pools.from_settings(self.config),
                                                lambda *args: None, config=self.config,
                                                providers=self.providers, options=job["options"])
                provenance = json.loads((pack / "source.json").read_text(encoding="utf-8"))
                self.assertEqual(len(self.speech_calls) - speech_before, int(audio_only))
                self.assertEqual(provenance["media_type"], "audio" if audio_only else "video")
                self.assertEqual(result["canonical_visual_state_count"] == 0, audio_only)
                self.assertEqual(provenance["audio_status"], "present" if audio_only else "absent")
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
                self.assertEqual(hashlib.sha256(uploaded.read_bytes()).hexdigest(), before)
                self.assertEqual(acquisition.leftovers(pack), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
    async def test_accepted_extension_does_not_turn_corrupt_bytes_into_success(self):
        source = self.root / "broken.ts"
        source.write_bytes(b"not a decodable transport stream")
        store, job = await self.upload(source, self.root / "corrupt-library")
        pack = store.workdir(job["id"])
        with self.assertRaises(media.MediaError) as caught:
            await pipeline.process(job["url"], pack, pipeline.Pools.from_settings(self.config),
                                   lambda *args: None, config=self.config, providers=self.providers)
        self.assertEqual(caught.exception.code, "media_processing_failed")
        self.assertFalse((pack / "manifest.json").exists())
        self.assertEqual(source.read_bytes(), b"not a decodable transport stream")
        self.assertEqual(acquisition.leftovers(pack), [])


if __name__ == "__main__":
    unittest.main()
