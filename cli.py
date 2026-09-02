"""Command line front end: python cli.py "<pasted share text or urls>" """
from __future__ import annotations

import asyncio
import sys

from clipmind.config import OUT_DIR
from clipmind.jobs import JobStore
from clipmind.links import extract_urls, guess_title


async def main(text: str) -> int:
    urls = extract_urls(text)
    if not urls:
        print("no Douyin links found in that text", file=sys.stderr)
        return 1

    store = JobStore()
    queue = store.subscribe()
    print(f"{len(urls)} video(s)\n")
    labels: dict[str, str] = {}
    pending: set[str] = set()
    for index, url in enumerate(urls, start=1):
        job = store.submit(url, guess_title(text, url) or url)
        labels[job.id] = f"[{index}]"
        pending.add(job.id)

    succeeded = 0
    try:
        while pending:
            event = await queue.get()
            job_id = event.get("id")
            if job_id not in pending:
                continue
            label = labels[job_id]
            print(
                f"{label} {event.get('progress', 0) * 100:5.1f}%  "
                f"{event.get('stage', ''):<12} {event.get('note', '')}",
                flush=True,
            )
            if event.get("status") == "done":
                succeeded += 1
                pending.remove(job_id)
                print(f"{label} done -> {OUT_DIR / job_id / 'evidence.md'}\n")
            elif event.get("status") in {"error", "interrupted"}:
                pending.remove(job_id)
                print(f"{label} FAILED: {event.get('error') or event.get('note')}\n", file=sys.stderr)
    finally:
        store.unsubscribe(queue)
        await store.close()
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or sys.stdin.read())))
