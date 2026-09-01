import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import pipeline
from clipmind.asr import Segment, Transcript
from clipmind.fetch import FetchError, Media
from clipmind.media import Frame
from clipmind.summarize import Summary


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tempdir.name) / "job"
        self.source_path = self.workdir / "source.mp4"
        self.audio_path = self.workdir / "audio.wav"
        self.sample_path = self.workdir / "samples" / "s_00001.jpg"
        self.rendered_ocr_error: str | None = None

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def pools(self) -> pipeline.Pools:
        return pipeline.Pools(
            fetch=asyncio.Semaphore(2),
            asr=asyncio.Semaphore(2),
            ocr=asyncio.Semaphore(2),
            llm=asyncio.Semaphore(2),
        )

    async def fake_fetch(self, url, workdir, on_note=None) -> Media:
        workdir.mkdir(parents=True, exist_ok=True)
        self.source_path.write_bytes(b"source media")
        if on_note:
            on_note("fake fetch")
        return Media(
            video_path=self.source_path,
            info={
                "id": "video-id",
                "title": "Video title",
                "duration": 52,
                "webpage_url": url,
                "_clipmind_strategy": "fake",
            },
        )

    async def fake_extract_audio(self, video, dest) -> Path:
        dest.write_bytes(b"audio")
        return dest

    async def fake_sample_frames(self, video, dest_dir) -> list[Frame]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        self.sample_path.write_bytes(b"sample")
        return [Frame(index=0, timestamp=0.0, path=self.sample_path)]

    def fake_dedupe(self, frames) -> list[Frame]:
        return frames

    async def fake_transcribe(self, audio) -> Transcript:
        return Transcript([Segment(0.0, 1.0, "spoken text")])

    async def fake_annotate(self, frames, semaphore) -> str | None:
        frames[0].lines = ("screen text",)
        frames[0].text = "screen text"
        frames[0].novelty = 8
        return None

    def fake_select(self, frames) -> list[Frame]:
        return frames

    async def fake_promote(self, video, chosen, dest_dir) -> list[Frame]:
        return chosen

    async def fake_summarize(self, transcript, frames, title, duration, semaphore) -> Summary:
        return Summary("## 摘要\n\nA note.", "fake-summary")

    def fake_write_all(self, dest, item, transcript, frames, summary, ocr_error=None) -> dict:
        self.rendered_ocr_error = ocr_error
        return {
            "title": item.title,
            "duration": item.duration,
            "keyframes": [],
            "summary_engine": summary.engine,
        }

    def patched_pipeline(self, **overrides) -> ExitStack:
        replacements = {
            "fetch": self.fake_fetch,
            "extract_audio": self.fake_extract_audio,
            "sample_frames": self.fake_sample_frames,
            "dedupe": self.fake_dedupe,
            "transcribe": self.fake_transcribe,
            "annotate": self.fake_annotate,
            "select": self.fake_select,
            "promote": self.fake_promote,
            "summarize": self.fake_summarize,
            "write_all": self.fake_write_all,
        }
        replacements.update(overrides)
        targets = {
            "fetch": (pipeline, "fetch"),
            "extract_audio": (pipeline.media, "extract_audio"),
            "sample_frames": (pipeline.media, "sample_frames"),
            "dedupe": (pipeline.media, "dedupe"),
            "transcribe": (pipeline.asr, "transcribe"),
            "annotate": (pipeline.keyframes, "annotate"),
            "select": (pipeline.keyframes, "select"),
            "promote": (pipeline.keyframes, "promote"),
            "summarize": (pipeline.summarize, "summarize"),
            "write_all": (pipeline.render, "write_all"),
        }
        stack = ExitStack()
        for name, replacement in replacements.items():
            target, attribute = targets[name]
            stack.enter_context(patch.object(target, attribute, new=replacement))
        stack.enter_context(
            patch.object(pipeline, "settings", SimpleNamespace(keep_source_video=False))
        )
        return stack

    def report(self, stage, progress, note="") -> None:
        pass

    async def test_speech_and_vision_overlap_and_join_before_summary(self) -> None:
        speech_started = asyncio.Event()
        vision_started = asyncio.Event()
        release = asyncio.Event()
        summary_started = asyncio.Event()
        speech_done = False
        vision_done = False

        async def gated_transcribe(audio) -> Transcript:
            nonlocal speech_done
            speech_started.set()
            await release.wait()
            speech_done = True
            return Transcript([Segment(0.0, 1.0, "speech")])

        async def gated_annotate(frames, semaphore) -> str | None:
            nonlocal vision_done
            vision_started.set()
            await release.wait()
            vision_done = True
            return None

        async def checking_summarize(transcript, frames, title, duration, semaphore) -> Summary:
            self.assertTrue(speech_done)
            self.assertTrue(vision_done)
            summary_started.set()
            return Summary("joined", "fake-summary")

        with self.patched_pipeline(
            transcribe=gated_transcribe,
            annotate=gated_annotate,
            summarize=checking_summarize,
        ):
            task = asyncio.create_task(
                pipeline.process("https://v.douyin.com/example", self.workdir, self.pools(), self.report)
            )
            await asyncio.wait_for(
                asyncio.gather(speech_started.wait(), vision_started.wait()),
                timeout=1,
            )
            self.assertFalse(summary_started.is_set())
            release.set()
            result = await task

        self.assertTrue(summary_started.is_set())
        self.assertEqual(result["summary_engine"], "fake-summary")

    async def test_ocr_failure_degrades_without_killing_job(self) -> None:
        async def failed_ocr(frames, semaphore) -> str:
            return "OCR failed on 1/1 frames: injected failure"

        with self.patched_pipeline(annotate=failed_ocr):
            result = await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertEqual(result["title"], "Video title")
        self.assertEqual(
            self.rendered_ocr_error,
            "OCR failed on 1/1 frames: injected failure",
        )

    async def test_fatal_fetch_failure_raises_pipeline_error(self) -> None:
        async def failed_fetch(url, workdir, on_note=None):
            raise FetchError("could not retrieve this video")

        with self.patched_pipeline(fetch=failed_fetch):
            with self.assertRaisesRegex(RuntimeError, "could not retrieve this video"):
                await pipeline.process(
                    "https://v.douyin.com/broken", self.workdir, self.pools(), self.report
                )

    async def test_success_removes_temporary_media(self) -> None:
        with self.patched_pipeline():
            await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertFalse(self.source_path.exists())
        self.assertFalse(self.audio_path.exists())
        self.assertFalse((self.workdir / "samples").exists())

    @unittest.expectedFailure
    async def test_post_download_failure_should_remove_temporary_media(self) -> None:
        """TODO(P0-5): remove expectedFailure after exception-safe cleanup lands."""

        async def failed_summary(transcript, frames, title, duration, semaphore):
            raise RuntimeError("injected post-download failure")

        with self.patched_pipeline(summarize=failed_summary):
            with self.assertRaisesRegex(RuntimeError, "injected post-download failure"):
                await pipeline.process(
                    "https://v.douyin.com/example", self.workdir, self.pools(), self.report
                )

        self.assertFalse(self.source_path.exists(), "source video leaked after failure")
        self.assertFalse(self.audio_path.exists(), "temporary audio leaked after failure")
        self.assertFalse((self.workdir / "samples").exists(), "sample frames leaked after failure")


if __name__ == "__main__":
    unittest.main()
