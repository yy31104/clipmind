"""Acquire media for a share link, trying progressively more privileged routes.

Ladder (first success wins, and the winning strategy is cached for the batch):
    1. yt-dlp with the configured browser's cookies  (handles Douyin's
       "fresh cookies needed" gate using the session you already have)
    2. yt-dlp with no cookies at all
    3. yt-dlp with a user-supplied cookies.txt

If every rung fails we raise FetchError carrying the last stderr so the UI can
tell the user *why* rather than just "failed".
"""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import settings


class FetchError(RuntimeError):
    pass


@dataclass
class Media:
    video_path: Path
    info: dict

    @property
    def video_id(self) -> str:
        return str(self.info.get("id") or "unknown")

    @property
    def title(self) -> str:
        return (self.info.get("title") or "").strip() or self.video_id

    @property
    def duration(self) -> float:
        try:
            return float(self.info.get("duration") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def uploader(self) -> str | None:
        return self.info.get("uploader") or self.info.get("channel")

    @property
    def webpage_url(self) -> str | None:
        return self.info.get("webpage_url")


# Remembering what worked saves a doomed round-trip per video in a batch.
_winning_source: str | None = None
_lock = asyncio.Lock()


def _cookie_args(source: str) -> list[str]:
    if source == "-":
        return []
    if source == "file":
        if not settings.cookie_file:
            raise FetchError("no cookie file configured")
        return ["--cookies", settings.cookie_file]
    return ["--cookies-from-browser", source]


def _ordered_sources() -> list[str]:
    sources = list(settings.cookie_sources)
    if settings.cookie_file:
        sources.append("file")
    if _winning_source and _winning_source in sources:
        sources.remove(_winning_source)
        sources.insert(0, _winning_source)
    return sources


def _describe(source: str) -> str:
    return {"-": "no cookies", "file": "cookie file"}.get(source, f"{source} cookies")


async def _run(args: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def fetch(url: str, workdir: Path, on_note=None) -> Media:
    """Download the video into ``workdir`` and return it with its metadata."""
    global _winning_source

    if not shutil.which("yt-dlp"):
        raise FetchError("yt-dlp is not installed (brew install yt-dlp)")

    workdir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    for source in _ordered_sources():
        if on_note:
            on_note(f"trying {_describe(source)}")
        try:
            cookie_args = _cookie_args(source)
        except FetchError as exc:
            errors.append(str(exc))
            continue

        code, out, err = await _run(
            [
                "yt-dlp",
                "--no-warnings",
                "--no-playlist",
                "--no-progress",
                "--no-simulate",
                "--dump-single-json",
                "-f", settings.fetch_format,
                "-o", str(workdir / "source.%(ext)s"),
                *cookie_args,
                url,
            ]
        )
        if code != 0 or not out.strip():
            errors.append(f"{_describe(source)}: {(err or out).strip().splitlines()[-1] if (err or out).strip() else 'failed'}")
            continue

        try:
            info = json.loads(out.splitlines()[-1])
        except json.JSONDecodeError as exc:
            errors.append(f"{_describe(source)}: bad metadata ({exc})")
            continue

        path = _downloaded_path(info, workdir)
        if path is None:
            errors.append(f"{_describe(source)}: reported success but wrote no file")
            continue

        async with _lock:
            _winning_source = source
        info["_clipmind_strategy"] = _describe(source)
        return Media(video_path=path, info=info)

    raise FetchError(
        "could not retrieve this video.\n  " + "\n  ".join(errors[-4:])
    )


def _downloaded_path(info: dict, workdir: Path) -> Path | None:
    for entry in info.get("requested_downloads") or []:
        candidate = entry.get("filepath") or entry.get("_filename")
        if candidate and Path(candidate).exists():
            return Path(candidate)
    files = sorted(workdir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    return files[0] if files else None
