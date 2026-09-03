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
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, settings


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FetchError(RuntimeError):
    def __init__(self, code: str, user_message: str, action: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.action = action


def _fetch_error(errors: list[str]) -> FetchError:
    diagnostic = "\n".join(errors)
    lowered = diagnostic.lower()
    logger.warning("Douyin acquisition failed after all strategies: %s", diagnostic)
    if (
        "private video" in lowered
        or "video is private" in lowered
        or "private account" in lowered
        or "仅自己" in diagnostic
    ):
        return FetchError(
            "private_video",
            "This Douyin video is private.",
            "Use a public video or change its visibility, then retry.",
        )
    if "fresh cookies" in lowered:
        return FetchError(
            "cookies_stale",
            "Douyin rejected the browser cookies.",
            "Open Douyin in Chrome, refresh the page, then copy a fresh share link and retry.",
        )
    if "sign in" in lowered or "login required" in lowered or "log in" in lowered:
        return FetchError(
            "login_required",
            "Douyin requires a signed-in Chrome session for this video.",
            "Sign in to Douyin in Chrome, refresh the video, and retry.",
        )
    if (
        "could not copy chrome cookie" in lowered
        or "failed to decrypt" in lowered
        or "permission" in lowered
        or "operation not permitted" in lowered
    ):
        return FetchError(
            "cookies_unavailable",
            "ClipMind could not read Chrome cookies.",
            "Keep Chrome installed and readable, or configure CLIPMIND_COOKIE_FILE.",
        )
    if "unsupported url" in lowered or "not available" in lowered or "removed" in lowered:
        return FetchError(
            "link_unavailable",
            "This Douyin share link is expired or unavailable.",
            "Copy a fresh share link from Douyin and try again.",
        )
    return FetchError(
        "media_fetch_failed",
        "ClipMind could not retrieve this video.",
        "Check that the link opens in Chrome, then copy a fresh share link and retry.",
    )


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


def _cookie_args(source: str, config: Settings = settings) -> list[str]:
    if source == "-":
        return []
    if source == "file":
        if not config.cookie_file:
            raise FetchError(
                "cookies_unavailable",
                "The configured cookie file is unavailable.",
                "Set CLIPMIND_COOKIE_FILE to a readable Netscape cookie file.",
            )
        return ["--cookies", config.cookie_file]
    return ["--cookies-from-browser", source]


def _ordered_sources(config: Settings = settings) -> list[str]:
    sources = list(config.cookie_sources)
    if config.cookie_file:
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


async def fetch(
    url: str,
    workdir: Path,
    on_note=None,
    *,
    config: Settings = settings,
) -> Media:
    """Download the video into ``workdir`` and return it with its metadata."""
    global _winning_source

    if not shutil.which("yt-dlp"):
        raise FetchError(
            "missing_dependency",
            "yt-dlp is not installed.",
            "Install it with `brew install yt-dlp`, then restart ClipMind.",
        )

    workdir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    for source in _ordered_sources(config):
        if on_note:
            on_note(f"trying {_describe(source)}")
        try:
            cookie_args = _cookie_args(source, config)
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
                "-f", config.fetch_format,
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

    raise _fetch_error(errors[-4:])


def _downloaded_path(info: dict, workdir: Path) -> Path | None:
    for entry in info.get("requested_downloads") or []:
        candidate = entry.get("filepath") or entry.get("_filename")
        if candidate and Path(candidate).exists():
            return Path(candidate)
    files = sorted(workdir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    return files[0] if files else None
