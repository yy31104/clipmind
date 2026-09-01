"""Command line front end: python cli.py "<pasted share text or urls>" """
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from clipmind.config import OUT_DIR
from clipmind.links import extract_urls
from clipmind.pipeline import Pools, process


async def main(text: str) -> int:
    urls = extract_urls(text)
    if not urls:
        print("no Douyin links found in that text", file=sys.stderr)
        return 1

    pools = Pools()
    print(f"{len(urls)} video(s)\n")

    async def one(index: int, url: str):
        started = time.time()
        label = f"[{index}]"

        def report(stage, progress, note=""):
            print(f"{label} {progress * 100:5.1f}%  {stage:<12} {note}", flush=True)

        try:
            meta = await process(url, OUT_DIR / f"cli-{index}", pools, report)
            print(f"{label} done in {time.time() - started:.1f}s -> "
                  f"{OUT_DIR / f'cli-{index}' / 'note.md'}\n")
            return meta
        except Exception as exc:  # noqa: BLE001
            print(f"{label} FAILED: {exc}\n", file=sys.stderr)
            return None

    results = await asyncio.gather(*(one(i, u) for i, u in enumerate(urls, 1)))
    return 0 if any(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or sys.stdin.read())))
