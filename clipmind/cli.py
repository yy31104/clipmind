"""Command-line interface for extraction, library access, server, and MCP."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from .config import OUT_DIR, Settings
from .evidence import EvidencePackError
from .jobs import JobStore
from .links import extract_sources, guess_title
from .providers import default_providers
from .sdk import PackLibrary
from .sources import supported_sources


async def main(
    text: str,
    *,
    reprocess: bool = False,
    force: bool = False,
    json_output: bool = False,
) -> int:
    """Analyze sources and stream durable job progress to stdout."""
    urls = extract_sources(text)
    if not urls:
        print("no supported URL or local media file found", file=sys.stderr)
        return 1

    store = JobStore()
    queue = store.subscribe()
    if not json_output:
        print(f"{len(urls)} video(s)\n")
    labels: dict[str, str] = {}
    pending: set[str] = set()
    succeeded = 0
    for index, url in enumerate(urls, start=1):
        cached = None if reprocess else store.reusable(url)
        if cached is not None:
            succeeded += 1
            value = {"status": "reused", "pack_id": cached.id, "path": str(OUT_DIR / cached.id)}
            print(json.dumps(value) if json_output else f"[{index}] reused -> {OUT_DIR / cached.id / 'evidence.md'}\n")
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
            if json_output:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            else:
                print(
                    f"{label} {event.get('progress', 0) * 100:5.1f}%  "
                    f"{event.get('stage', ''):<12} {event.get('note', '')}",
                    flush=True,
                )
            if event.get("status") == "done":
                succeeded += 1
                pending.remove(job_id)
                if not json_output:
                    print(f"{label} done -> {OUT_DIR / job_id / 'evidence.md'}\n")
            elif event.get("status") in {"error", "interrupted"}:
                pending.remove(job_id)
                if not json_output:
                    print(
                        f"{label} FAILED: {event.get('error') or event.get('note')}\n",
                        file=sys.stderr,
                    )
    finally:
        store.unsubscribe(queue)
        await store.close()
    return 0 if succeeded == len(urls) else 1


def doctor() -> dict:
    config = Settings.from_env()
    providers = default_providers(config)
    root = OUT_DIR if OUT_DIR.exists() else OUT_DIR.parent
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "yt_dlp": bool(shutil.which("yt-dlp")),
        "asr": providers.transcript.available(),
        "ocr": providers.text.available(),
        "library_writable": os.access(root, os.W_OK),
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "providers": {
            "asr": providers.transcript.name,
            "ocr": providers.text.name,
        },
        "library": str(OUT_DIR),
        "sources": supported_sources(),
    }


def _print(value: object, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, str):
        print(value)
        return
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipmind",
        description="Local-first multimodal video ingestion for humans and AI agents.",
    )
    commands = parser.add_subparsers(dest="command")

    analyze = commands.add_parser("analyze", help="extract one or more complete Evidence Packs")
    analyze.add_argument("text", nargs="*")
    analyze.add_argument("--reprocess", action="store_true")
    analyze.add_argument("--force", action="store_true", help="process the complete source despite a cost refusal")
    analyze.add_argument("--json", action="store_true", dest="json_output")

    listing = commands.add_parser("list", help="list complete Evidence Packs")
    listing.add_argument("--json", action="store_true")

    show = commands.add_parser("show", help="show Evidence Pack metadata")
    show.add_argument("pack_id")
    show.add_argument("--json", action="store_true")

    search = commands.add_parser("search", help="search titles, transcript, and OCR")
    search.add_argument("query", nargs="+")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")

    transcript = commands.add_parser("transcript", help="print a timestamped transcript")
    transcript.add_argument("pack_id")
    transcript.add_argument("--json", action="store_true")

    timeline = commands.add_parser("timeline", help="print the canonical visual timeline")
    timeline.add_argument("pack_id")
    timeline.add_argument("--json", action="store_true")

    export = commands.add_parser("export", help="export a canonical deterministic ZIP")
    export.add_argument("pack_id")
    export.add_argument("--output", "-o")

    diagnostic = commands.add_parser("doctor", help="check local dependencies and providers")
    diagnostic.add_argument("--json", action="store_true")

    serve = commands.add_parser("serve", help="run the local web application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8420)

    commands.add_parser("mcp", help="run the MCP server over stdio")
    return parser


def entrypoint(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"analyze", "list", "show", "search", "transcript", "timeline", "export", "doctor", "serve", "mcp"}
    if argv and argv[0] not in commands and argv[0] not in {"-h", "--help"}:
        argv.insert(0, "analyze")
    parser = _parser()
    args = parser.parse_args(argv)
    library = PackLibrary()
    try:
        if args.command == "analyze":
            source = " ".join(args.text) or sys.stdin.read()
            return asyncio.run(
                main(
                    source,
                    reprocess=args.reprocess,
                    force=args.force,
                    json_output=args.json_output,
                )
            )
        if args.command == "list":
            values = [pack.summary() for pack in library.list()]
            if args.json:
                _print(values, as_json=True)
            else:
                for item in values:
                    print(f"{item['pack_id']}\t{item['platform']}\t{item['title']}")
            return 0
        if args.command == "show":
            _print(library.get(args.pack_id).summary(), as_json=args.json)
            return 0
        if args.command == "search":
            values = library.search(" ".join(args.query), limit=args.limit)
            _print(values, as_json=args.json)
            return 0
        if args.command == "transcript":
            pack = library.get(args.pack_id)
            if args.json:
                _print(pack.transcript, as_json=True)
            else:
                for item in pack.transcript:
                    print(f"[{float(item.get('start') or 0):.3f}] {item.get('text') or ''}")
            return 0
        if args.command == "timeline":
            _print(library.get(args.pack_id).visual_timeline, as_json=args.json)
            return 0
        if args.command == "export":
            path = library.get(args.pack_id).export(args.output)
            print(path)
            return 0
        if args.command == "doctor":
            report = doctor()
            if args.json:
                _print(report, as_json=True)
            else:
                for name, ready in report["checks"].items():
                    print(f"{'✓' if ready else '✗'} {name}")
                print(f"ASR: {report['providers']['asr']} / OCR: {report['providers']['ocr']}")
                print("Sources: " + ", ".join(item["platform"] for item in report["sources"]))
            return 0 if report["ready"] else 1
        if args.command == "serve":
            import uvicorn

            uvicorn.run("clipmind.server:app", host=args.host, port=args.port)
            return 0
        if args.command == "mcp":
            from .mcp import serve_stdio

            serve_stdio()
            return 0
    except (EvidencePackError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
