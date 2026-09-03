import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind.config import Settings
from clipmind.jobs import Job, JobStore


class JobStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.out_patch = patch("clipmind.jobs.OUT_DIR", Path(self.tempdir.name))
        self.out_patch.start()

    async def asyncTearDown(self) -> None:
        self.out_patch.stop()
        self.tempdir.cleanup()

    async def _events_until_terminal(self, queue: asyncio.Queue) -> list[dict]:
        events: list[dict] = []
        while not events or events[-1]["status"] not in {"done", "error"}:
            events.append(await asyncio.wait_for(queue.get(), timeout=1))
        return events

    def _assert_current_transition_contract(self, events: list[dict]) -> None:
        allowed = {
            "queued": {"queued", "running"},
            "running": {"running", "done", "error", "interrupted"},
            "done": {"done"},
            "error": {"error"},
            "interrupted": {"interrupted"},
        }
        self.assertEqual(events[0]["status"], "queued")
        for previous, current in zip(events, events[1:]):
            self.assertIn(current["status"], allowed[previous["status"]])

        progress = [event["progress"] for event in events]
        self.assertEqual(progress, sorted(progress))

    def _fill_subscriber(self, queue: asyncio.Queue) -> None:
        for index in range(queue.maxsize):
            queue.put_nowait({"id": f"stale-{index}", "status": "running"})

    async def test_overflow_emits_resync_without_detaching_or_blocking(self) -> None:
        store = JobStore()
        queue = store.subscribe()
        self._fill_subscriber(queue)
        job = JobStoreTests._job("current", "running")
        store.jobs[job.id] = job

        self.assertIsNone(store._publish(job))

        self.assertIn(queue, store._subscribers)
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(queue.get_nowait(), {"type": "resync"})
        self.assertEqual(store.listing()[0]["status"], "running")

    async def test_stalled_subscriber_is_isolated_and_recovers(self) -> None:
        store = JobStore()
        stalled = store.subscribe()
        healthy = store.subscribe()
        self._fill_subscriber(stalled)
        first = self._job("first", "running")

        store._publish(first)

        self.assertEqual(stalled.get_nowait(), {"type": "resync"})
        self.assertEqual(healthy.get_nowait()["id"], first.id)
        later = self._job("later", "done")
        store._publish(later)
        self.assertEqual(stalled.get_nowait()["id"], later.id)
        self.assertEqual(healthy.get_nowait()["id"], later.id)

    async def test_terminal_states_remain_recoverable_after_overflow(self) -> None:
        for status in ("done", "error", "interrupted"):
            with self.subTest(status=status):
                store = JobStore()
                queue = store.subscribe()
                self._fill_subscriber(queue)
                job = self._job(status, status)
                store.jobs[job.id] = job

                store._publish(job)

                self.assertEqual(queue.get_nowait(), {"type": "resync"})
                snapshot = {item["id"]: item for item in store.listing()}
                self.assertEqual(snapshot[job.id]["status"], status)

    async def test_unsubscribe_removes_subscriber(self) -> None:
        store = JobStore()
        queue = store.subscribe()

        store.unsubscribe(queue)

        self.assertNotIn(queue, store._subscribers)

    @staticmethod
    def _job(job_id: str, status: str) -> Job:
        return Job(
            id=job_id,
            url=f"https://v.douyin.com/{job_id}",
            title=job_id,
            status=status,
            stage=status,
        )

    async def test_successful_job_emits_queued_running_done(self) -> None:
        async def successful_process(url, workdir, pools, report, **kwargs):
            report("fetching", 0.7, "fetching")
            report("analysing", 0.3, "late lower progress")
            report("writing", 0.95, "writing")
            return {"title": "Resolved title"}

        store = JobStore()
        queue = store.subscribe()
        with patch("clipmind.jobs.process", new=successful_process):
            job = store.submit("https://v.douyin.com/example", "Share title")
            events = await self._events_until_terminal(queue)

        self._assert_current_transition_contract(events)
        self.assertEqual(
            [event["status"] for event in events if event["status"] != "running"],
            ["queued", "done"],
        )
        self.assertIn("running", [event["status"] for event in events])
        self.assertEqual(job.status, "done")
        self.assertEqual(job.stage, "done")
        self.assertEqual(job.progress, 1.0)
        self.assertEqual(job.title, "Resolved title")
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)

    async def test_job_store_passes_its_config_and_providers_to_the_pipeline(self) -> None:
        observed: dict = {}
        configured = Settings(sample_fps=3.0)
        providers = object()

        async def inspect_process(url, workdir, pools, report, **kwargs):
            observed.update(kwargs)
            return {"title": "Done"}

        store = JobStore(config=configured, providers=providers)  # type: ignore[arg-type]
        queue = store.subscribe()
        with patch("clipmind.jobs.process", new=inspect_process):
            job = store.submit("https://example.com/video", "Video")
            await self._events_until_terminal(queue)

        self.assertIs(observed["config"], configured)
        self.assertIs(observed["providers"], providers)
        self.assertEqual(job.status, "done")

    async def test_fatal_failure_emits_queued_running_error(self) -> None:
        async def failing_process(url, workdir, pools, report, **kwargs):
            raise RuntimeError("ingestion unavailable")

        store = JobStore()
        queue = store.subscribe()
        with patch("clipmind.jobs.process", new=failing_process):
            job = store.submit("https://v.douyin.com/broken", "Broken")
            events = await self._events_until_terminal(queue)

        self._assert_current_transition_contract(events)
        self.assertEqual([event["status"] for event in events], ["queued", "running", "error"])
        self.assertEqual(job.status, "error")
        self.assertEqual(job.stage, "error")
        self.assertEqual(job.error, "Processing failed.")
        self.assertEqual(job.error_code, "processing_failed")
        self.assertIn("retry", job.error_action)
        self.assertIsNotNone(job.finished_at)

    async def test_two_independent_jobs_can_enter_process_together(self) -> None:
        entered = 0
        both_entered = asyncio.Event()
        release = asyncio.Event()

        async def gated_process(url, workdir, pools, report, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()
            return {"title": url}

        with patch("clipmind.jobs.process", new=gated_process):
            store = JobStore(config=Settings(max_videos=2))
            queue = store.subscribe()
            first = store.submit("https://v.douyin.com/first", "First")
            second = store.submit("https://v.douyin.com/second", "Second")
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            self.assertEqual(entered, 2)
            self.assertEqual({first.status, second.status}, {"running"})
            release.set()
            terminal_ids: set[str] = set()
            while len(terminal_ids) < 2:
                event = await asyncio.wait_for(queue.get(), timeout=1)
                if event["status"] == "done":
                    terminal_ids.add(event["id"])

        self.assertEqual({first.status, second.status}, {"done"})

    async def test_jobs_over_the_video_limit_remain_queued(self) -> None:
        running = 0
        peak = 0
        first_wave = asyncio.Event()
        release = asyncio.Event()

        async def gated_process(url, workdir, pools, report, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            if running == 2:
                first_wave.set()
            await release.wait()
            running -= 1
            return {"title": url}

        with patch("clipmind.jobs.process", new=gated_process):
            store = JobStore(config=Settings(max_videos=2))
            jobs = [
                store.submit(f"https://v.douyin.com/{index}", str(index))
                for index in range(5)
            ]
            await asyncio.wait_for(first_wave.wait(), timeout=1)
            self.assertEqual(sum(job.status == "running" for job in jobs), 2)
            self.assertEqual(sum(job.status == "queued" for job in jobs), 3)
            release.set()
            await asyncio.gather(*list(store._tasks))

        self.assertEqual(peak, 2)
        self.assertTrue(all(job.status == "done" for job in jobs))


if __name__ == "__main__":
    unittest.main()
