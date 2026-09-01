"""In-process job registry: bounded concurrency plus a broadcast for live UI."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import OUT_DIR, settings
from .pipeline import Pools, process


@dataclass
class Job:
    id: str
    url: str
    title: str
    status: str = "queued"       # queued | running | done | error
    stage: str = "queued"
    progress: float = 0.0
    note: str = ""
    error: str | None = None
    result: dict | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return (self.finished_at or time.time()) - self.started_at

    def public(self) -> dict:
        data = asdict(self)
        data["elapsed"] = round(self.elapsed, 1)
        return data


class JobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.pools = Pools()
        self._slots = asyncio.Semaphore(settings.max_videos)
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: set[asyncio.Task] = set()

    # --- pub/sub ----------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, job: Job) -> None:
        payload = job.public()
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A stalled listener must not block the pipeline.
                self._subscribers.discard(queue)

    # --- lifecycle --------------------------------------------------------
    def submit(self, url: str, title: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url, title=title or url)
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._publish(job)
        return job

    async def _run(self, job: Job) -> None:
        async with self._slots:
            job.status = "running"
            job.started_at = time.time()
            self._publish(job)

            def report(stage: str, progress: float, note: str = "") -> None:
                job.stage = stage
                job.progress = max(job.progress, min(float(progress), 1.0))
                job.note = note
                # pipeline reports the resolved title as the 'sampling' note
                if stage == "sampling" and note:
                    job.title = note
                self._publish(job)

            workdir = OUT_DIR / job.id
            try:
                job.result = await process(job.url, workdir, self.pools, report)
                job.title = job.result.get("title") or job.title
                job.status, job.stage, job.progress = "done", "done", 1.0
            except Exception as exc:  # noqa: BLE001
                job.status, job.stage = "error", "error"
                job.error = str(exc)
            finally:
                job.finished_at = time.time()
                self._publish(job)

    def listing(self) -> list[dict]:
        return [j.public() for j in sorted(
            self.jobs.values(), key=lambda j: j.created_at, reverse=True)]

    def workdir(self, job_id: str) -> Path:
        return OUT_DIR / job_id
