"""In-process job registry: bounded concurrency plus a broadcast for live UI."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import OUT_DIR, settings
from .pipeline import Pools, cleanup_temporary, process
from .storage import JobStorage


STATUSES = {"queued", "running", "done", "error", "interrupted"}


@dataclass
class Job:
    id: str
    url: str
    title: str
    status: str = "queued"       # queued | running | done | error | interrupted
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

    def record(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict) -> Job:
        names = {item.name for item in fields(cls)}
        return cls(**{name: value for name, value in record.items() if name in names})


class JobStore:
    def __init__(self, out_dir: Path | None = None) -> None:
        self.jobs: dict[str, Job] = {}
        self.storage = JobStorage(out_dir if out_dir is not None else OUT_DIR)
        self.pools = Pools()
        self._slots = asyncio.Semaphore(settings.max_videos)
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: set[asyncio.Task] = set()
        self._started = False
        for record in self.storage.load():
            try:
                job = Job.from_record(record)
            except (TypeError, ValueError):
                continue
            if job.status in STATUSES:
                self.jobs[job.id] = job

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
    def start(self) -> None:
        """Apply restart recovery once the application event loop is running."""
        if self._started:
            return
        self._started = True
        for job in list(self.jobs.values()):
            if job.status == "queued":
                self._schedule(job)
            elif job.status == "running":
                cleanup_temporary(self.workdir(job.id), keep_source=False)
                job.status = job.stage = "interrupted"
                job.note = "interrupted by application restart"
                job.error = job.error or "processing was interrupted by application restart"
                job.finished_at = time.time()
                self._persist(job)
                self._publish(job)

    def submit(self, url: str, title: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url, title=title or url)
        self._persist(job)
        self.jobs[job.id] = job
        self._schedule(job)
        self._publish(job)
        return job

    def _schedule(self, job: Job) -> None:
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job) -> None:
        async with self._slots:
            job.status = "running"
            job.started_at = time.time()
            job.finished_at = None
            self._persist(job)
            self._publish(job)

            def report(stage: str, progress: float, note: str = "") -> None:
                job.stage = stage
                job.progress = max(job.progress, min(float(progress), 1.0))
                job.note = note
                # pipeline reports the resolved title as the 'sampling' note
                if stage == "sampling" and note:
                    job.title = note
                self._persist(job)
                self._publish(job)

            try:
                job.result = await process(
                    job.url, self.workdir(job.id), self.pools, report
                )
                job.title = job.result.get("title") or job.title
                job.status, job.stage, job.progress = "done", "done", 1.0
            except asyncio.CancelledError:
                # Leave the durable state as running; restart recovery owns the
                # transition to interrupted and must not accidentally requeue it.
                raise
            except Exception as exc:  # noqa: BLE001
                job.status, job.stage = "error", "error"
                job.error = str(exc)
            job.finished_at = time.time()
            self._persist(job)
            self._publish(job)

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _persist(self, job: Job) -> None:
        self.storage.save(job.id, job.record())

    def listing(self) -> list[dict]:
        return [j.public() for j in sorted(
            self.jobs.values(), key=lambda j: j.created_at, reverse=True)]

    def workdir(self, job_id: str) -> Path:
        return self.storage.workdir(job_id)
