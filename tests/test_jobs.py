import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind.jobs import JobStore


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

    async def test_successful_job_emits_queued_running_done(self) -> None:
        async def successful_process(url, workdir, pools, report):
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

    async def test_fatal_failure_emits_queued_running_error(self) -> None:
        async def failing_process(url, workdir, pools, report):
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

        async def gated_process(url, workdir, pools, report):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()
            return {"title": url}

        with (
            patch("clipmind.jobs.settings", SimpleNamespace(max_videos=2)),
            patch("clipmind.jobs.process", new=gated_process),
        ):
            store = JobStore()
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

        async def gated_process(url, workdir, pools, report):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            if running == 2:
                first_wave.set()
            await release.wait()
            running -= 1
            return {"title": url}

        with (
            patch("clipmind.jobs.settings", SimpleNamespace(max_videos=2)),
            patch("clipmind.jobs.process", new=gated_process),
        ):
            store = JobStore()
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
