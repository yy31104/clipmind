import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import keyframes, pipeline
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

    async def fake_sample_frames(self, video, dest_dir, *, width=None) -> list[Frame]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "s_00001.jpg"
        path.write_bytes(b"high-resolution sample" if width else b"sample")
        return [Frame(index=0, timestamp=0.0, path=path)]

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

    async def copy_preview(self, video, chosen, dest_dir) -> list[Frame]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for position, frame in enumerate(chosen):
            dest = dest_dir / f"preview-{position}.jpg"
            dest.write_bytes(frame.path.read_bytes())
            frame.path = dest
        return chosen

    async def fake_summarize(self, transcript, frames, title, duration, semaphore) -> Summary:
        return Summary("## 摘要\n\nA note.", "fake-summary")

    def fake_write_all(
        self,
        dest,
        item,
        transcript,
        frames,
        summary,
        ocr_error=None,
        *,
        visual_states=None,
        visual_preview=None,
        build_groups=None,
        candidate_frame_count=None,
        evidence_manifest=None,
        stage_timings=None,
    ) -> dict:
        self.rendered_ocr_error = ocr_error
        return {
            "title": item.title,
            "duration": item.duration,
            "keyframes": [],
            "summary_engine": summary.engine,
            "stage_timings": stage_timings,
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
            patch.object(
                pipeline,
                "settings",
                SimpleNamespace(
                    keep_source_video=False,
                    evidence_width=1280,
                    sample_width=640,
                    sample_fps=2.0,
                    dedupe_threshold=6,
                ),
            )
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
        self.assertFalse(self.source_path.exists())
        self.assertFalse(self.audio_path.exists())
        self.assertFalse((self.workdir / "samples").exists())

    async def test_asr_failure_degrades_and_cleans_temporary_media(self) -> None:
        async def failed_asr(audio) -> Transcript:
            return Transcript([], error="injected ASR failure")

        with self.patched_pipeline(transcribe=failed_asr):
            result = await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertEqual(result["title"], "Video title")
        self.assertFalse(self.source_path.exists())
        self.assertFalse(self.audio_path.exists())
        self.assertFalse((self.workdir / "samples").exists())

    async def test_fatal_fetch_failure_raises_pipeline_error(self) -> None:
        async def failed_fetch(url, workdir, on_note=None):
            raise FetchError(
                "media_fetch_failed",
                "ClipMind could not retrieve this video.",
                "Copy a fresh share link and retry.",
            )

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

    def test_cleanup_preserves_canonical_visual_states(self) -> None:
        canonical = self.workdir / "visual_states" / "all" / "state.jpg"
        canonical.parent.mkdir(parents=True)
        canonical.write_bytes(b"final evidence")
        source_metadata = self.workdir / "source.json"
        source_metadata.write_text('{"source_id":"stable"}', encoding="utf-8")
        self.source_path.write_bytes(b"source")
        self.audio_path.write_bytes(b"audio")
        self.sample_path.parent.mkdir(parents=True)
        self.sample_path.write_bytes(b"candidate")
        evidence_sample = self.workdir / "evidence_samples" / "s_00001.jpg"
        evidence_sample.parent.mkdir()
        evidence_sample.write_bytes(b"high-resolution candidate")

        pipeline.cleanup_temporary(self.workdir)

        self.assertTrue(canonical.exists())
        self.assertTrue(source_metadata.exists())
        self.assertFalse(self.source_path.exists())
        self.assertFalse(self.audio_path.exists())
        self.assertFalse(self.sample_path.parent.exists())
        self.assertFalse(evidence_sample.parent.exists())

    async def test_canonical_and_content_driven_preview_are_not_capped(self) -> None:
        async def many_samples(video, dest_dir, *, width=None) -> list[Frame]:
            dest_dir.mkdir(parents=True, exist_ok=True)
            frames = []
            for index in range(13):
                path = dest_dir / f"sample-{index}.jpg"
                prefix = b"high-" if width else b""
                content = b"opening-logo" if index == 0 else f"state-{index}".encode()
                path.write_bytes(prefix + content)
                frames.append(Frame(index=index, timestamp=index / 2, path=path))
            return frames

        def dedupe_without_opening(frames) -> list[Frame]:
            for frame in frames:
                frame.phash = 0 if frame.index % 2 == 0 else (1 << 64) - 1
            return frames[1:]

        async def annotate_all(frames, semaphore) -> str | None:
            # Preview opening-context logic only receives canonical frames.
            self.assertEqual([frame.index for frame in frames], list(range(1, 13)))
            return None

        real_write_all = pipeline.render.write_all
        real_select = pipeline.keyframes.select
        with (
            self.patched_pipeline(
                sample_frames=many_samples,
                dedupe=dedupe_without_opening,
                annotate=annotate_all,
                select=real_select,
                promote=self.copy_preview,
                write_all=real_write_all,
            ),
            patch.object(
                pipeline.keyframes,
                "settings",
                SimpleNamespace(max_keyframes=2),
            ),
        ):
            result = await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertEqual(result["candidate_frame_count"], 13)
        self.assertEqual(result["canonical_visual_state_count"], 12)
        self.assertEqual(result["preview_frame_count"], 12)
        self.assertEqual(result["compatibility_keyframe_count"], 2)
        self.assertEqual(result["evidence_pack"]["schema"]["version"], "1.0.0")
        self.assertEqual(result["evidence_pack"]["manifest"], "manifest.json")
        self.assertEqual(result["dedupe_failure_count"], 0)
        self.assertEqual(len(result["visual_states"]), 12)
        self.assertEqual(len(result["visual_preview"]), 12)
        self.assertEqual(len(result["keyframes"]), 2)
        persisted = json.loads(
            (self.workdir / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["visual_states"], result["visual_states"])
        self.assertEqual(
            [state["timestamp"] for state in result["visual_states"]],
            sorted(state["timestamp"] for state in result["visual_states"]),
        )
        canonical_files = sorted((self.workdir / "visual_states" / "all").glob("*.jpg"))
        self.assertEqual(len(canonical_files), 12)
        self.assertEqual(
            {path.read_bytes() for path in canonical_files},
            {f"high-state-{index}".encode() for index in range(1, 13)},
        )
        self.assertTrue(
            all((self.workdir / state["file"]).exists() for state in result["visual_states"])
        )
        preview_files = list((self.workdir / "keyframes").glob("*.jpg"))
        self.assertEqual(len(preview_files), 2)
        self.assertNotIn(b"opening-logo", {path.read_bytes() for path in preview_files})
        evidence_preview = list(
            (self.workdir / "visual_states" / "preview").glob("*.jpg")
        )
        self.assertEqual(len(evidence_preview), 12)
        self.assertTrue(
            all((self.workdir / state["file"]).exists() for state in result["visual_preview"])
        )
        self.assertFalse((self.workdir / "samples").exists())
        self.assertFalse((self.workdir / "evidence_samples").exists())

    async def test_dedupe_failure_is_retained_and_serialized(self) -> None:
        async def two_samples(video, dest_dir, *, width=None) -> list[Frame]:
            dest_dir.mkdir(parents=True, exist_ok=True)
            frames = []
            for index in range(2):
                path = dest_dir / f"sample-{index}.jpg"
                prefix = "high-" if width else ""
                path.write_bytes(f"{prefix}state-{index}".encode())
                frames.append(Frame(index=index, timestamp=float(index), path=path))
            return frames

        actual_dedupe = pipeline.media.dedupe

        def dedupe_with_explicit_threshold(frames) -> list[Frame]:
            return actual_dedupe(frames, threshold=0)

        async def annotate_all(frames, semaphore) -> str | None:
            return None

        real_write_all = pipeline.render.write_all
        with (
            self.patched_pipeline(
                sample_frames=two_samples,
                dedupe=dedupe_with_explicit_threshold,
                annotate=annotate_all,
                select=lambda frames: frames[:1],
                promote=self.copy_preview,
                write_all=real_write_all,
            ),
            patch.object(
                pipeline.media,
                "dhash",
                side_effect=[0, OSError("/Users/private/Cookies")],
            ),
        ):
            result = await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertEqual(result["candidate_frame_count"], 2)
        self.assertEqual(result["canonical_visual_state_count"], 2)
        self.assertEqual(result["preview_frame_count"], 2)
        self.assertEqual(result["compatibility_keyframe_count"], 1)
        self.assertEqual(result["dedupe_failure_count"], 1)
        self.assertEqual(
            result["visual_states"][1]["dedupe_warning"],
            "OSError: perceptual hash failed; frame retained",
        )

    async def test_progressive_build_metadata_compacts_only_the_preview(self) -> None:
        async def build_samples(video, dest_dir, *, width=None) -> list[Frame]:
            dest_dir.mkdir(parents=True, exist_ok=True)
            frames = []
            for index in range(3):
                path = dest_dir / f"sample-{index}.jpg"
                prefix = "high-" if width else ""
                path.write_bytes(f"{prefix}state-{index}".encode())
                frames.append(Frame(index=index, timestamp=float(index), path=path))
            return frames

        async def annotate_build(frames, semaphore) -> str | None:
            texts = ("Python", "Python FastAPI", "Python FastAPI PostgreSQL")
            for frame, text in zip(frames, texts):
                frame.text = text
                frame.lines = (text,)
            return None

        with self.patched_pipeline(
            sample_frames=build_samples,
            annotate=annotate_build,
            select=lambda frames: frames[:1],
            promote=self.copy_preview,
            write_all=pipeline.render.write_all,
        ):
            result = await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertEqual(result["canonical_visual_state_count"], 3)
        self.assertEqual(result["preview_frame_count"], 1)
        self.assertEqual(len(result["visual_states"]), 3)
        self.assertEqual(len(result["visual_preview"]), 1)
        self.assertEqual(result["visual_preview"][0]["text"], "Python FastAPI PostgreSQL")
        self.assertEqual(result["build_groups"][0]["id"], "build-00001")
        self.assertEqual(len(result["build_groups"][0]["members"]), 3)
        self.assertEqual(
            [state["build_position"] for state in result["visual_states"]],
            [0, 1, 2],
        )

    async def test_optional_summary_failure_does_not_invalidate_evidence_pack(self) -> None:
        async def failed_summary(transcript, frames, title, duration, semaphore):
            raise RuntimeError("injected post-download failure")

        with self.patched_pipeline(summarize=failed_summary):
            result = await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertEqual(result["title"], "Video title")
        self.assertTrue((self.workdir / "manifest.json").exists())
        self.assertTrue((self.workdir / "evidence.md").exists())
        self.assertFalse(self.source_path.exists(), "source video leaked after failure")
        self.assertFalse(self.audio_path.exists(), "temporary audio leaked after failure")
        self.assertFalse((self.workdir / "samples").exists(), "sample frames leaked after failure")
        self.assertTrue(
            any((self.workdir / "visual_states" / "all").iterdir()),
            "canonical visual evidence was removed after failure",
        )

    async def test_legacy_note_write_failure_does_not_invalidate_evidence_pack(self) -> None:
        def failed_write(*args, **kwargs):
            raise RuntimeError("injected note write failure")

        with self.patched_pipeline(write_all=failed_write):
            result = await pipeline.process(
                "https://v.douyin.com/example", self.workdir, self.pools(), self.report
            )

        self.assertIn("injected note write failure", result["compatibility_error"])
        self.assertTrue((self.workdir / "manifest.json").exists())
        self.assertFalse(self.source_path.exists())
        self.assertFalse(self.audio_path.exists())
        self.assertFalse((self.workdir / "samples").exists())

    async def test_cleanup_failure_does_not_hide_pipeline_failure(self) -> None:
        def failed_evidence(*args, **kwargs):
            raise RuntimeError("original pipeline failure")

        with (
            self.patched_pipeline(),
            patch.object(
                pipeline.evidence,
                "write_pack",
                side_effect=failed_evidence,
            ),
            patch.object(
                pipeline,
                "cleanup_temporary",
                side_effect=RuntimeError("cleanup also failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "original pipeline failure"):
                await pipeline.process(
                    "https://v.douyin.com/example", self.workdir, self.pools(), self.report
                )

    async def test_failed_full_resolution_grab_preserves_final_keyframe(self) -> None:
        sample = self.workdir / "samples" / "sample.jpg"
        sample.parent.mkdir(parents=True)
        sample.write_bytes(b"low-resolution frame")
        frame = Frame(index=0, timestamp=3.5, path=sample)

        async def failed_grab(video, timestamp, dest):
            raise pipeline.media.MediaError("injected frame grab failure")

        with patch.object(keyframes.media, "extract_still", new=failed_grab):
            promoted = await keyframes.promote(
                Path("video.mp4"), [frame], self.workdir / "keyframes"
            )

        self.assertEqual(promoted[0].path, self.workdir / "keyframes" / "00-03.jpg")
        self.assertEqual(promoted[0].path.read_bytes(), b"low-resolution frame")


if __name__ == "__main__":
    unittest.main()
