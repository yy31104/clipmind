"""Acquire URL or local media through a platform-neutral source adapter.

Ladder (first success wins, and the winning strategy is cached for the batch):
    1. yt-dlp with each configured browser-cookie source
    2. yt-dlp with no cookies at all (when ``-`` is configured)
    3. yt-dlp with a user-supplied cookies.txt

If every rung fails we raise FetchError carrying the last stderr so the UI can
tell the user *why* rather than just "failed".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from pathlib import Path
from urllib.parse import quote

from . import acquisition
from .config import Settings, settings
from .sources import MediaAsset, SourceError, adapter_for


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FetchError(SourceError):
    pass


def _fetch_error(errors: list[str], platform: str = "source") -> FetchError:
    diagnostic = "\n".join(errors)
    lowered = diagnostic.lower()
    logger.warning("%s acquisition failed after all strategies: %s", platform, diagnostic)
    if (
        "private video" in lowered
        or "video is private" in lowered
        or "private account" in lowered
        or "仅自己" in diagnostic
    ):
        return FetchError(
            "private_video",
            f"This {platform} video is private.",
            "Use a public video or change its visibility, then retry.",
        )
    if "fresh cookies" in lowered:
        return FetchError(
            "cookies_stale",
            f"{platform.title()} rejected the browser cookies.",
            f"Open {platform.title()} in Chrome, refresh the page, then copy a fresh share link and retry.",
        )
    if "sign in" in lowered or "login required" in lowered or "log in" in lowered:
        return FetchError(
            "login_required",
            f"{platform.title()} requires a signed-in Chrome session for this video.",
            f"Sign in to {platform.title()} in Chrome, refresh the video, and retry.",
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
            f"This {platform} link is expired or unavailable.",
            f"Copy a fresh share link from {platform.title()} and try again.",
        )
    return FetchError(
        "media_fetch_failed",
        "ClipMind could not retrieve this video.",
        "Check that the URL opens in a browser, then copy a fresh link and retry.",
    )


# Compatibility import used by existing integrations and older tests.
Media = MediaAsset


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
) -> MediaAsset:
    """Materialize one supported source into ``workdir`` with normalized metadata."""
    global _winning_source

    try:
        adapter = adapter_for(url)
    except SourceError as exc:
        raise FetchError(exc.code, exc.user_message, exc.action) from exc

    if adapter.local:
        return await _fetch_local(url, workdir, adapter)

    if not shutil.which("yt-dlp"):
        raise FetchError(
            "missing_dependency",
            "yt-dlp is not installed.",
            "Install ClipMind's dependencies (including yt-dlp), then restart ClipMind.",
        )

    workdir.mkdir(parents=True, exist_ok=True)
    # Ownership is recorded before the first byte lands, so a crash at any
    # later point still leaves a directory restart recovery knows to remove.
    root = acquisition.open_workspace(workdir, strategy="pending")
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
                "-o", str(root / "source.%(ext)s"),
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

        path = _downloaded_path(info, root)
        if path is None:
            errors.append(f"{_describe(source)}: reported success but wrote no file")
            continue

        async with _lock:
            _winning_source = source
        acquisition.record_strategy(workdir, _describe(source))
        info["_clipmind_strategy"] = _describe(source)
        return MediaAsset(
            media_path=path,
            info=adapter.normalize_info(url, info),
        )

    raise _fetch_error(errors[-4:], adapter.platform)


async def _fetch_local(url: str, workdir: Path, adapter) -> MediaAsset:
    source = Path(url.removeprefix("file://")).expanduser().resolve()
    if not source.is_file():
        raise FetchError(
            "local_file_unavailable",
            "The selected local media file is unavailable.",
            "Choose an existing readable media file and retry.",
        )
    workdir.mkdir(parents=True, exist_ok=True)
    # Only the copy inside the owned directory belongs to ClipMind. ``source``
    # is the user's own file and must never become something cleanup deletes.
    root = acquisition.open_workspace(workdir, strategy="local file copy")
    suffix = source.suffix.casefold() or ".media"
    dest = root / f"source{suffix}"
    await asyncio.to_thread(shutil.copy2, source, dest)
    metadata = await _probe_local(dest)
    digest = await asyncio.to_thread(_sha256, dest)
    info = adapter.normalize_info(
        str(source),
        {
            **metadata,
            "id": digest,
            "title": source.stem,
            # Evidence Pack provenance may be shared. Preserve the filename but
            # never publish the user's absolute local directory.
            "webpage_url": f"local:///{quote(source.name)}",
            "_clipmind_strategy": "local file copy",
        },
    )
    return MediaAsset(media_path=dest, info=info)


async def _probe_local(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {"duration": 0.0}
    code, out, _err = await _run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
    )
    if code:
        return {"duration": 0.0}
    try:
        payload = json.loads(out)
        return {"duration": float(payload.get("format", {}).get("duration") or 0.0)}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"duration": 0.0}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _downloaded_path(info: dict, root: Path) -> Path | None:
    for entry in info.get("requested_downloads") or []:
        candidate = entry.get("filepath") or entry.get("_filename")
        if candidate and Path(candidate).exists():
            return Path(candidate)
    files = sorted(root.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    return files[0] if files else None
