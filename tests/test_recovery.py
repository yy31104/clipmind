import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind.jobs import JobStore


class JobRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tempdir.name) / "out"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def terminal_events(
        self, queue: asyncio.Queue, job_ids: set[str]
    ) -> dict[str, dict]:
        terminal: dict[str, dict] = {}
        while terminal.keys() != job_ids:
            event = await asyncio.wait_for(queue.get(), timeout=1)
            if event["id"] in job_ids and event["status"] in {
                "done", "error", "interrupted"
            }:
                terminal[event["id"]] = event
        return terminal

    async def test_running_is_persisted_before_process_starts(self) -> None:
        observed_status: str | None = None

        async def inspect_persisted_state(url, workdir, pools, report):
            nonlocal observed_status
            payload = json.loads((workdir / "job.json").read_text(encoding="utf-8"))
            observed_status = payload["job"]["status"]
            return {"title": "Done"}

        store = JobStore(self.out_dir)
        queue = store.subscribe()
        with patch("clipmind.jobs.process", new=inspect_persisted_state):
            job = store.submit("https://v.douyin.com/order", "Order")
            await self.terminal_events(queue, {job.id})
        await store.close()

        self.assertEqual(observed_status, "running")

    async def test_queued_job_is_requeued_exactly_once_after_restart(self) -> None:
        process_calls = 0

        async def recovered_process(url, workdir, pools, report):
            nonlocal process_calls
            process_calls += 1
            return {"title": "Recovered queued job"}

        with patch("clipmind.jobs.settings", SimpleNamespace(max_videos=0)):
            first = JobStore(self.out_dir)
            original = first.submit("https://v.douyin.com/queued", "Queued")
            await asyncio.sleep(0)
            persisted = json.loads(
                (first.workdir(original.id) / "job.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["job"]["status"], "queued")
            await first.close()

        second = JobStore(self.out_dir)
        queue = second.subscribe()
        with patch("clipmind.jobs.process", new=recovered_process):
            second.start()
            second.start()
            await self.terminal_events(queue, {original.id})
        await second.close()

        self.assertEqual(process_calls, 1)
        self.assertEqual(second.jobs[original.id].status, "done")

    async def test_running_job_becomes_interrupted_and_only_temp_is_cleaned(self) -> None:
        entered = asyncio.Event()

        async def interrupted_process(url, workdir, pools, report):
            (workdir / "samples").mkdir(parents=True)
            (workdir / "keyframes").mkdir()
            (workdir / "visual_states" / "all").mkdir(parents=True)
            (workdir / "source.mp4").write_bytes(b"source")
            (workdir / "source.mp4.part").write_bytes(b"partial")
            (workdir / "audio.wav").write_bytes(b"audio")
            (workdir / "samples" / "candidate.jpg").write_bytes(b"candidate")
            (workdir / "note.md").write_text("final note", encoding="utf-8")
            (workdir / "keyframes" / "final.jpg").write_bytes(b"final")
            (workdir / "visual_states" / "all" / "final.jpg").write_bytes(
                b"canonical"
            )
            entered.set()
            await asyncio.Event().wait()

        first = JobStore(self.out_dir)
        with patch("clipmind.jobs.process", new=interrupted_process):
            original = first.submit("https://v.douyin.com/running", "Running")
            await asyncio.wait_for(entered.wait(), timeout=1)
            await first.close()

        workdir = first.workdir(original.id)
        self.assertTrue((workdir / "source.mp4").exists())

        process_calls = 0

        async def must_not_run(url, workdir, pools, report):
            nonlocal process_calls
            process_calls += 1

        second = JobStore(self.out_dir)
        with patch("clipmind.jobs.process", new=must_not_run):
            second.start()
        recovered = second.jobs[original.id]
        await second.close()

        self.assertEqual(recovered.status, "interrupted")
        self.assertEqual(recovered.stage, "interrupted")
        self.assertEqual(process_calls, 0)
        self.assertFalse((workdir / "source.mp4").exists())
        self.assertFalse((workdir / "source.mp4.part").exists())
        self.assertFalse((workdir / "audio.wav").exists())
        self.assertFalse((workdir / "samples").exists())
        self.assertTrue((workdir / "note.md").exists())
        self.assertTrue((workdir / "keyframes" / "final.jpg").exists())
        self.assertTrue((workdir / "visual_states" / "all" / "final.jpg").exists())

        third = JobStore(self.out_dir)
        with patch("clipmind.jobs.process", new=must_not_run):
            third.start()
        await third.close()
        self.assertEqual(third.jobs[original.id].status, "interrupted")
        self.assertEqual(process_calls, 0)

    async def test_done_and_error_jobs_restore_without_reprocessing(self) -> None:
        async def terminal_process(url, workdir, pools, report):
            if url.endswith("/error"):
                raise RuntimeError("persisted diagnostic")
            (workdir / "note.md").write_text("persisted note", encoding="utf-8")
            (workdir / "transcript.json").write_text("[]", encoding="utf-8")
            return {"title": "Persisted result", "duration": 12}

        first = JobStore(self.out_dir)
        queue = first.subscribe()
        with patch("clipmind.jobs.process", new=terminal_process):
            done = first.submit("https://v.douyin.com/done", "Done")
            error = first.submit("https://v.douyin.com/error", "Error")
            await self.terminal_events(queue, {done.id, error.id})
        await first.close()

        process_calls = 0

        async def must_not_run(url, workdir, pools, report):
            nonlocal process_calls
            process_calls += 1

        second = JobStore(self.out_dir)
        with patch("clipmind.jobs.process", new=must_not_run):
            second.start()
        await second.close()

        self.assertIsNot(second.jobs[done.id], done)
        self.assertEqual(second.jobs[done.id].status, "done")
        self.assertEqual(second.jobs[done.id].result["duration"], 12)
        self.assertEqual(
            (second.workdir(done.id) / "note.md").read_text(encoding="utf-8"),
            "persisted note",
        )
        self.assertEqual(second.jobs[error.id].status, "error")
        self.assertEqual(second.jobs[error.id].error, "persisted diagnostic")
        self.assertEqual(process_calls, 0)

    async def test_existing_note_directory_is_loaded_as_legacy_done_job(self) -> None:
        workdir = self.out_dir / "legacy-job"
        workdir.mkdir(parents=True)
        metadata = {
            "id": "douyin-video-id",
            "title": "Existing note",
            "url": "https://www.douyin.com/video/123",
            "duration": 42,
            "keyframes": [],
        }
        (workdir / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        (workdir / "note.md").write_text("existing note", encoding="utf-8")
        (workdir / "transcript.json").write_text("[]", encoding="utf-8")

        store = JobStore(self.out_dir)
        store.start()
        await store.close()

        recovered = store.jobs["legacy-job"]
        self.assertEqual(recovered.status, "done")
        self.assertEqual(recovered.title, "Existing note")
        self.assertEqual(recovered.result, metadata)


if __name__ == "__main__":
    unittest.main()
