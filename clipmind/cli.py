"""Command-line interface for extraction, library access, server, and MCP."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import OUT_DIR, Settings
from .evidence import EvidencePackError
from .demo import build_sample_video
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


def _deep_asr_smoke() -> dict:
    environment = dict(os.environ)
    environment["HF_HUB_OFFLINE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "clipmind.doctor_smoke"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            env=environment,
            check=False,
        )
        report = json.loads(result.stdout.strip())
        if not isinstance(report, dict) or not isinstance(report.get("passed"), bool):
            raise ValueError("invalid smoke test report")
        return report
    except subprocess.TimeoutExpired:
        return {"passed": False, "reason": "ASR smoke test timed out after 90 seconds"}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"passed": False, "reason": "ASR smoke test did not return a valid report"}


def doctor(*, deep: bool = False) -> dict:
    config = Settings.from_env()
    providers = default_providers(config)
    root = OUT_DIR if OUT_DIR.exists() else OUT_DIR.parent
    asr_importable = providers.transcript.available()
    asr = {
        "provider": providers.transcript.name,
        "model": (
            config.faster_whisper_model if providers.transcript.name == "faster-whisper"
            else config.asr_model
        ),
        "device": (
            config.faster_whisper_device if providers.transcript.name == "faster-whisper"
            else "native"
        ),
        "compute_type": (
            config.faster_whisper_compute_type if providers.transcript.name == "faster-whisper"
            else "provider-default"
        ),
        "check_scope": "import_only",
        "runtime_test": "not_run",
    }
    if deep and providers.transcript.name == "faster-whisper" and asr_importable:
        smoke = _deep_asr_smoke()
        asr["check_scope"] = "offline_smoke_test"
        asr["runtime_test"] = "passed" if smoke["passed"] else "failed"
        asr["runtime_detail"] = smoke["reason"]
        if not smoke["passed"] and any(
            name in smoke["reason"].lower() for name in ("cublas", "cudnn")
        ):
            asr["runtime_action"] = (
                "Install CUDA 12 cuBLAS and cuDNN 9, or set "
                "CLIPMIND_FASTER_WHISPER_DEVICE=cpu."
            )
    elif deep and providers.transcript.name != "faster-whisper":
        asr["runtime_detail"] = "deep smoke test is available for faster-whisper only"
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "yt_dlp": bool(shutil.which("yt-dlp")),
        "asr": asr_importable and (not deep or asr["runtime_test"] != "failed"),
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
        "asr": asr,
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
    export.add_argument("--compact", action="store_true", help="keep full transcript with selected screenshots")
    export.add_argument("--text-only", action="store_true", help="export text without any screenshots")

    demo = commands.add_parser(
        "demo", help="extract a generated sample video; no network or account"
    )
    demo.add_argument("--json", dest="json_output", action="store_true")

    diagnostic = commands.add_parser("doctor", help="check local dependencies and providers")
    diagnostic.add_argument("--json", action="store_true")
    diagnostic.add_argument("--deep", action="store_true", help="run an offline ASR smoke test with a cached model")

    verify = commands.add_parser("verify", help="audit saved pack files, images, timing and references without network access")
    verify.add_argument("pack", help="library pack ID or Evidence Pack directory")
    verify.add_argument("--json", action="store_true")

    serve = commands.add_parser("serve", help="run the local web application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8420)

    commands.add_parser("wechat-login", help="open Tencent Yuanbao for local WeChat Channels sign-in")

    commands.add_parser("mcp", help="run the MCP server over stdio")
    return parser



def normalize_argv(argv: list[str]) -> list[str]:
    """Let a bare link or media path stand in for `analyze`.

    Deciding by asking whether the argument *is* a source, rather than
    checking it against a hand-listed set of command names, keeps this from
    silently swallowing any subcommand added later.
    """
    if argv and extract_sources(argv[0]):
        return ["analyze", *argv]
    return list(argv)


def entrypoint(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = normalize_argv(argv)
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
        if args.command == "demo":
            with tempfile.TemporaryDirectory(prefix="clipmind-demo-") as tempdir:
                video = build_sample_video(Path(tempdir))
                if not args.json_output:
                    print("Extracting a generated four-slide sample.\n")
                code = asyncio.run(main(str(video), json_output=args.json_output))
            if code == 0 and not args.json_output:
                print(
                    "\nNothing was downloaded and no account was used.\n"
                    "Next: `clipmind list` to see it, or `clipmind serve` to browse."
                )
            return code
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
            pack = library.get(args.pack_id)
            if args.compact or args.text_only:
                from .compact import export_compact_zip
                path = export_compact_zip(pack.root, Path(args.output) if args.output else None,
                                          image_policy="text" if args.text_only else "content")
            else:
                path = pack.export(args.output)
            print(path)
            return 0
        if args.command == "doctor":
            report = doctor(deep=args.deep)
            if args.json:
                _print(report, as_json=True)
            else:
                for name, ready in report["checks"].items():
                    label = name
                    if name == "asr":
                        label = "asr import" if report["asr"]["runtime_test"] == "not_run" else "asr runtime"
                    print(f"{'✓' if ready else '✗'} {label}")
                print(f"ASR: {report['providers']['asr']} / OCR: {report['providers']['ocr']}")
                asr = report["asr"]
                print(f"ASR model: {asr['model']} / device: {asr['device']} / compute: {asr['compute_type']}")
                if asr["runtime_test"] == "not_run":
                    print("ASR check: import only; model and runtime untested. Use `clipmind doctor --deep`.")
                else:
                    print(f"ASR runtime: {asr['runtime_test']} ({asr['runtime_detail']})")
                    if asr.get("runtime_action"):
                        print(f"ASR action: {asr['runtime_action']}")
                print("Sources: " + ", ".join(item["platform"] for item in report["sources"]))
            return 0 if report["ready"] else 1
        if args.command == "verify":
            from .quality import audit_pack

            target = Path(args.pack).expanduser()
            if not target.is_dir():
                target = library.get(args.pack).root
            report = audit_pack(target)
            _print(report, as_json=args.json)
            return 0 if report["passed"] else 1
        if args.command == "serve":
            import uvicorn

            uvicorn.run("clipmind.server:app", host=args.host, port=args.port)
            return 0
        if args.command == "wechat-login":
            from .sources.wechat_browser import BrowserUnavailable, login

            print("在打开的腾讯元宝官方网页中自行扫码登录，完成后关闭该窗口。不要向 ClipMind 提供 Cookie 或密码。")
            try:
                asyncio.run(login())
            except BrowserUnavailable as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print("浏览器窗口已关闭。现在可以重新处理微信视频号分享链接。")
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
