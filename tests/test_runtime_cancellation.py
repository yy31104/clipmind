"""Cancellation cannot release resources while native readers still use them."""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clipmind import pipeline, visual_states
from clipmind.asr import FasterWhisperProvider, Transcript
from clipmind.async_utils import gather_joined, to_thread_joined
from clipmind.config import Settings
from clipmind.jobs import JobStore
from clipmind.media import Frame
from tests import test_pipeline


async def wait_started(event):
    async def wait():
        while not event.is_set():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), 2)


class CancellationOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_waits_for_native_worker_and_observes_its_error(self):
        started, release, exited = threading.Event(), threading.Event(), threading.Event()

        def worker():
            started.set()
            try:
                if not release.wait(4):
                    raise AssertionError("test failed to release worker")
                raise ValueError("late native failure")
            finally:
                exited.set()

        task = asyncio.create_task(to_thread_joined(worker))
        try:
            await wait_started(started)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
                self.assertFalse(exited.is_set())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertTrue(exited.is_set())

    async def test_ocr_cancel_keeps_slot_until_current_reader_exits_and_skips_queue(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def read_text(path):
            calls.append(path)
            started.set()
            if not release.wait(4):
                raise AssertionError("test failed to release OCR")
            return ["late result"]

        frames = [Frame(index=i, timestamp=float(i), path=Path(f"frame-{i}.png")) for i in range(3)]
        slot = asyncio.Semaphore(1)
        task = asyncio.create_task(visual_states.annotate(
            frames, slot, SimpleNamespace(read_text=read_text)))
        try:
            await wait_started(started)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
                self.assertTrue(slot.locked())
                self.assertEqual(len(calls), 1)
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertFalse(slot.locked())
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(not frame.text for frame in frames))

    async def test_asr_cancel_waits_for_native_inference(self):
        started, release, exited = threading.Event(), threading.Event(), threading.Event()

        def transcribe(audio):
            started.set()
            try:
                if not release.wait(4):
                    raise AssertionError("test failed to release ASR")
                return Transcript([])
            finally:
                exited.set()

        provider = FasterWhisperProvider(Settings())
        with patch.object(FasterWhisperProvider, "available", return_value=True), patch.object(
            FasterWhisperProvider, "_transcribe_sync", side_effect=transcribe
        ):
            task = asyncio.create_task(provider.transcribe(Path("audio.wav")))
            try:
                await wait_started(started)
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
        self.assertTrue(exited.is_set())

    async def test_pipeline_exception_joins_other_analysis_before_media_cleanup(self):
        fixture = test_pipeline.PipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        vision_started, vision_exited = asyncio.Event(), asyncio.Event()
        had_audio_until_vision_exit = []

        async def failing_speech(audio):
            await vision_started.wait()
            raise RuntimeError("injected provider failure")

        async def running_vision(frames, semaphore, recognizer=None):
            vision_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                had_audio_until_vision_exit.append(fixture.audio_path.exists())
                vision_exited.set()

        with fixture.patched_pipeline(transcribe=failing_speech, annotate=running_vision):
            with self.assertRaisesRegex(RuntimeError, "injected provider failure"):
                await asyncio.wait_for(pipeline.process(
                    "https://example.com", fixture.workdir, fixture.pools(), fixture.report), 2)
        self.assertTrue(vision_exited.is_set())
        self.assertEqual(had_audio_until_vision_exit, [True])
        self.assertFalse(fixture.audio_path.exists())
        self.assertFalse((fixture.workdir / "manifest.json").exists())

    async def test_successful_join_preserves_result_order(self):
        async def result(value):
            await asyncio.sleep(0)
            return value
        self.assertEqual(await gather_joined(result("speech"), result("vision")), ["speech", "vision"])

    async def test_store_close_joins_active_worker_without_starting_queued_job(self):
        started, release, exited = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def worker():
            started.set()
            try:
                if not release.wait(4):
                    raise AssertionError("test failed to release worker")
            finally:
                exited.set()

        async def process(url, *args, **kwargs):
            calls.append(url)
            await to_thread_joined(worker)
            return {"title": "finished"}

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory), config=Settings(max_videos=1))
            with patch("clipmind.jobs.process", new=process):
                store.submit("first", "First")
                store.submit("second", "Second")
                await wait_started(started)
                close = asyncio.create_task(store.close())
                try:
                    await asyncio.sleep(.01)
                    self.assertFalse(close.done())
                    self.assertEqual(calls, ["first"])
                finally:
                    release.set()
                    await asyncio.wait_for(close, 2)
            self.assertTrue(exited.is_set())
            self.assertEqual(calls, ["first"])
            self.assertFalse(store._tasks)


if __name__ == "__main__":
    unittest.main()
