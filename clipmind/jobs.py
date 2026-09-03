"""In-process job registry: bounded concurrency plus a broadcast for live UI."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import evidence
from .config import OUT_DIR, Settings, settings
from .links import normalize_url, source_id_from_url
from .index import EvidenceIndex
from .pipeline import Pools, cleanup_temporary, process
from .providers import ProviderBundle, default_providers
from .storage import JobStorage


STATUSES = {"queued", "running", "done", "error", "interrupted"}
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


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
    error_code: str | None = None
    error_action: str | None = None
    error_details: dict | None = None
    options: dict = field(default_factory=dict)
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
        if self.status == "error" and not self.error_code:
            data.update(
                error="A previous processing attempt failed.",
                error_code="legacy_error",
                error_action="Reprocess the video to get an actionable diagnosis.",
            )
        data["elapsed"] = round(self.elapsed, 1)
        return data

    def record(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict) -> Job:
        names = {item.name for item in fields(cls)}
        return cls(**{name: value for name, value in record.items() if name in names})


class JobStore:
    def __init__(
        self,
        out_dir: Path | None = None,
        *,
        config: Settings | None = None,
        providers: ProviderBundle | None = None,
    ) -> None:
        self.config = config or settings
        self.providers = providers or default_providers(self.config)
        self.jobs: dict[str, Job] = {}
        self.storage = JobStorage(out_dir if out_dir is not None else OUT_DIR)
        self.index = EvidenceIndex(self.storage.root / ".evidence-index.sqlite3")
        self.pools = Pools.from_settings(self.config)
        self._slots = asyncio.Semaphore(self.config.max_videos)
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
                while True:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                queue.put_nowait({"type": "resync"})

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
            elif job.status == "done":
                self._sync_index(job)

    def submit(self, url: str, title: str, *, options: dict | None = None) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            url=url,
            title=title or url,
            options=dict(options or {}),
        )
        self._persist(job)
        self.jobs[job.id] = job
        self._schedule(job)
        self._publish(job)
        return job

    def reusable(self, url: str) -> Job | None:
        """Find the newest complete Evidence Pack for the same known source."""
        key = normalize_url(url)
        source_id = source_id_from_url(url)
        candidates = sorted(
            (job for job in self.jobs.values() if job.status == "done"),
            key=lambda job: job.finished_at or 0,
            reverse=True,
        )
        for job in candidates:
            same_url = normalize_url(job.url) == key
            same_source = bool(
                source_id
                and str((job.result or {}).get("id") or "") == source_id
            )
            if not same_url and not same_source:
                continue
            try:
                evidence.load_complete_pack(self.workdir(job.id))
            except evidence.EvidencePackError:
                continue
            return job
        return None

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
                    job.url,
                    self.workdir(job.id),
                    self.pools,
                    report,
                    config=self.config,
                    providers=self.providers,
                    options=job.options,
                )
                job.title = job.result.get("title") or job.title
                job.status, job.stage, job.progress = "done", "done", 1.0
                self._sync_index(job)
            except asyncio.CancelledError:
                # Leave the durable state as running; restart recovery owns the
                # transition to interrupted and must not accidentally requeue it.
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Job %s failed", job.id)
                job.status, job.stage = "error", "error"
                job.error = getattr(exc, "user_message", "Processing failed.")
                job.error_code = getattr(exc, "code", "processing_failed")
                job.error_action = getattr(
                    exc,
                    "action",
                    "Check the local server log, then retry.",
                )
                job.error_details = getattr(exc, "details", None)
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

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        for job in self.jobs.values():
            if job.status == "done":
                self._sync_index(job)
        return self.index.search(query, limit=limit)

    def _sync_index(self, job: Job) -> None:
        try:
            self.index.sync(job.id, self.workdir(job.id))
        except Exception:  # noqa: BLE001 - search is a rebuildable derived view
            logger.exception("Could not update the search index for job %s", job.id)
