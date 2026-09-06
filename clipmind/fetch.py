"""Acquire URL or local media through a platform-neutral source adapter.

Ladder (first success wins):
    1. yt-dlp with each configured browser-cookie source
    2. yt-dlp with no cookies at all (when ``-`` is configured)
    3. yt-dlp with a user-supplied cookies.txt

``AcquisitionEngine`` owns the ordering and remembers which rung last worked, so
a batch does not repeat a doomed round-trip. That memory is keyed per platform,
and per host for generic URLs: what worked is a property of one site, not of the
process.

If every rung fails we raise FetchError carrying the last stderr so the UI can
tell the user *why* rather than just "failed".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

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


def _describe(source: str) -> str:
    return {"-": "no cookies", "file": "cookie file"}.get(source, f"{source} cookies")


async def _run(args: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


@dataclass(frozen=True)
class AcquiredMedia:
    """What a strategy produced: a local file plus the metadata it came with."""

    path: Path
    info: dict


@dataclass(frozen=True)
class CookieRung:
    """One rung of the ladder: yt-dlp with one particular cookie source."""

    source: str

    @property
    def key(self) -> str:
        return self.source

    def describe(self) -> str:
        return _describe(self.source)

    async def acquire(
        self, url: str, root: Path, config: Settings
    ) -> tuple[AcquiredMedia | None, str | None]:
        """Acquire, or report why this rung could not, so the ladder continues.

        Only the failures the ladder is meant to survive become diagnostics.
        Cancellation and genuine environment errors still propagate: swallowing
        them would turn a stopped job into a silent rung failure and strand the
        cleanup that cancellation is supposed to trigger.
        """
        try:
            cookie_args = _cookie_args(self.source, config)
        except FetchError as exc:
            return None, str(exc)

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
            detail = (err or out).strip()
            reason = detail.splitlines()[-1] if detail else "failed"
            return None, f"{self.describe()}: {reason}"

        try:
            info = json.loads(out.splitlines()[-1])
        except json.JSONDecodeError as exc:
            return None, f"{self.describe()}: bad metadata ({exc})"

        path = _downloaded_path(info, root)
        if path is None:
            return None, f"{self.describe()}: reported success but wrote no file"
        return AcquiredMedia(path=path, info=info), None


class AcquisitionEngine:
    """Ordered strategies, plus a memory of which one last worked.

    The memory is keyed rather than shared. "What worked" is a property of one
    site, not of the process: the generic adapter covers the whole internet, so
    a single answer would let one host reorder every other host's ladder and
    spend a doomed round-trip on it.
    """

    def __init__(self) -> None:
        self._winning: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def affinity_key(url: str, adapter) -> str:
        if getattr(adapter, "generic", False):
            host = (urlsplit(url).hostname or "").casefold()
            if host:
                return f"host:{host}"
        return f"adapter:{adapter.name}"

    def strategies(self, key: str, config: Settings) -> list[CookieRung]:
        sources = list(config.cookie_sources)
        if config.cookie_file:
            sources.append("file")
        remembered = self._winning.get(key)
        if remembered and remembered in sources:
            sources.remove(remembered)
            sources.insert(0, remembered)
        return [CookieRung(source) for source in sources]

    async def acquire(
        self,
        url: str,
        workdir: Path,
        adapter,
        on_note=None,
        *,
        config: Settings = settings,
    ) -> MediaAsset:
        workdir.mkdir(parents=True, exist_ok=True)
        # Ownership is recorded before the first byte lands, so a crash at any
        # later point still leaves a directory restart recovery knows to remove.
        root = acquisition.open_workspace(workdir, strategy="pending")
        key = self.affinity_key(url, adapter)
        errors: list[str] = []

        for strategy in self.strategies(key, config):
            if on_note:
                on_note(f"trying {strategy.describe()}")
            acquired, diagnostic = await strategy.acquire(url, root, config)
            if acquired is None:
                errors.append(diagnostic or f"{strategy.describe()}: failed")
                continue

            async with self._lock:
                self._winning[key] = strategy.key
            acquisition.record_strategy(workdir, strategy.describe())
            acquired.info["_clipmind_strategy"] = strategy.describe()
            return MediaAsset(
                media_path=acquired.path,
                info=adapter.normalize_info(url, acquired.info),
            )

        raise _fetch_error(errors[-4:], adapter.platform)


_engine = AcquisitionEngine()


async def fetch(
    url: str,
    workdir: Path,
    on_note=None,
    *,
    config: Settings = settings,
) -> MediaAsset:
    """Materialize one supported source into ``workdir`` with normalized metadata."""
    try:
        adapter = adapter_for(url)
    except SourceError as exc:
        raise FetchError(exc.code, exc.user_message, exc.action) from exc

    if adapter.local:
        return await _fetch_local(url, workdir, adapter)

    # Checked here rather than inside the rung: a missing dependency is worth
    # saying once, not once per rung behind a ladder of failures.
    if not shutil.which("yt-dlp"):
        raise FetchError(
            "missing_dependency",
            "yt-dlp is not installed.",
            "Install ClipMind's dependencies (including yt-dlp), then restart ClipMind.",
        )

    return await _engine.acquire(url, workdir, adapter, on_note, config=config)


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
