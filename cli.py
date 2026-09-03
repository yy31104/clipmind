"""Command line front end: python cli.py "<pasted share text or urls>" """
from __future__ import annotations

import argparse
import asyncio
import sys

from clipmind.config import OUT_DIR
from clipmind.jobs import JobStore
from clipmind.links import extract_sources, guess_title


async def main(
    text: str,
    *,
    reprocess: bool = False,
    force: bool = False,
) -> int:
    urls = extract_sources(text)
    if not urls:
        print("no supported URL or local media file found", file=sys.stderr)
        return 1

    store = JobStore()
    queue = store.subscribe()
    print(f"{len(urls)} video(s)\n")
    labels: dict[str, str] = {}
    pending: set[str] = set()
    succeeded = 0
    for index, url in enumerate(urls, start=1):
        cached = None if reprocess else store.reusable(url)
        if cached is not None:
            succeeded += 1
            print(f"[{index}] reused -> {OUT_DIR / cached.id / 'evidence.md'}\n")
            continue
        job = store.submit(
            url,
            guess_title(text, url) or url,
            options={"force": force},
        )
        labels[job.id] = f"[{index}]"
        pending.add(job.id)

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
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="*")
    parser.add_argument("--reprocess", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="process a complete source even when the preflight budget is exceeded",
    )
    args = parser.parse_args()
    source = " ".join(args.text) or sys.stdin.read()
    raise SystemExit(
        asyncio.run(main(source, reprocess=args.reprocess, force=args.force))
    )
