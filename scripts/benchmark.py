#!/usr/bin/env python3
"""Reproducible local benchmark for bounded batch scheduling."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import jobs as jobs_module  # noqa: E402


async def run_batch(*, count: int, concurrency: int, delay: float) -> dict:
    running = 0
    peak = 0
    started = asyncio.Event()

    async def simulated_process(url, workdir, pools, report):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        if peak == min(count, concurrency):
            started.set()
        try:
            await asyncio.sleep(delay)
            return {"title": url, "duration": delay}
        finally:
            running -= 1

    with tempfile.TemporaryDirectory(prefix="clipmind-bench-") as tempdir:
        with (
            patch.object(
                jobs_module,
                "settings",
                SimpleNamespace(max_videos=concurrency),
            ),
            patch.object(jobs_module, "process", new=simulated_process),
        ):
            store = jobs_module.JobStore(Path(tempdir) / "out")
            before = time.perf_counter()
            submitted = [
                store.submit(f"https://v.douyin.com/bench-{index}", f"Job {index}")
                for index in range(count)
            ]
            await asyncio.wait_for(started.wait(), timeout=1)
            queued_at_capacity = sum(job.status == "queued" for job in submitted)
            await asyncio.gather(*list(store._tasks))
            elapsed = time.perf_counter() - before
            await store.close()
    return {
        "seconds": round(elapsed, 4),
        "peak_running": peak,
        "queued_at_capacity": queued_at_capacity,
    }


async def benchmark(count: int, concurrency: int, delay: float) -> dict:
    sequential = await run_batch(count=count, concurrency=1, delay=delay)
    parallel = await run_batch(count=count, concurrency=concurrency, delay=delay)
    return {
        "benchmark": "bounded-job-scheduler",
        "configuration": {
            "job_count": count,
            "parallel_limit": concurrency,
            "simulated_job_seconds": delay,
        },
        "sequential": sequential,
        "parallel": parallel,
        "batch_parallel_speedup": round(
            sequential["seconds"] / max(parallel["seconds"], 0.0001), 3
        ),
        "checks": {
            "parallel_limit_respected": parallel["peak_running"] <= concurrency,
            "overflow_was_queued": parallel["queued_at_capacity"] == max(
                count - concurrency, 0
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(benchmark(args.jobs, args.concurrency, args.delay))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
