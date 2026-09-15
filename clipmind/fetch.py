"""Acquire URL or local media through a platform-neutral source adapter.

Ladder (first success wins):
    1. yt-dlp with each configured browser-cookie source
    2. yt-dlp with no cookies at all (when ``-`` is configured)
    3. yt-dlp with a user-supplied cookies.txt

``AcquisitionEngine`` owns the ordering and remembers which rung last worked, so
a batch does not repeat a doomed round-trip. That memory is keyed per platform,
and per host for generic URLs: what worked is a property of one site, not of the
process.

If every rung fails we raise a FetchError classified across every attempt, so
the UI can tell the user *why* rather than just "failed". The raw diagnostic is
logged, never shown: it can carry local paths.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import acquisition, subprocesses
from .config import Settings, settings
from .sources import MediaAsset, SourceError, adapter_for
from .sources import douyin_browser


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FetchError(SourceError):
    pass


@dataclass(frozen=True)
class AttemptFailure:
    """Why one rung could not acquire, kept apart from how we labelled it.

    ``reason`` is what the tool actually said. Keeping our own label out of it
    matters: every browser rung is labelled "<browser> cookies", so matching a
    joined string would read a platform's "permission denied" as a cookie
    problem for the whole ladder.
    """

    strategy: str
    label: str
    reason: str

    @property
    def diagnostic(self) -> str:
        return f"{self.label}: {self.reason}" if self.label else self.reason


# Most actionable first. This is the order the previous if-chain applied, kept
# so classification does not shift while it gains structure.
_RANKED_CODES = (
    "live_stream_unsupported",
    "private_video",
    "source_metadata_unavailable",
    "cookies_stale",
    "login_required",
    "cookies_unavailable",
    "link_unavailable",
    "media_fetch_failed",
)
_RANK = {code: index for index, code in enumerate(_RANKED_CODES)}


def _message_for(code: str, platform: str) -> tuple[str, str]:
    return {
        "live_stream_unsupported": (
            "This is a live or scheduled stream, not a finished video.",
            "ClipMind analyses complete recordings. Submit the replay link after the stream has ended.",
        ),
        "source_metadata_unavailable": (
            "抖音视频信息获取失败，尚未取得视频文件。",
            "浏览器可播放不代表当前下载器能够获取。此错误不能确认 cookies 过期，请勿反复刷新登录或重复提交。",
        ),
        "private_video": (
            f"This {platform} video is private.",
            "Use a public video or change its visibility, then retry.",
        ),
        "cookies_stale": (
            f"{platform.title()} rejected the browser cookies.",
            f"Open {platform.title()} in Chrome, refresh the page, then copy a fresh share link and retry.",
        ),
        "login_required": (
            f"{platform.title()} requires a signed-in Chrome session for this video.",
            f"Sign in to {platform.title()} in Chrome, refresh the video, and retry.",
        ),
        "cookies_unavailable": (
            "ClipMind could not read Chrome cookies.",
            "Keep Chrome installed and readable, or configure CLIPMIND_COOKIE_FILE.",
        ),
        "link_unavailable": (
            f"This {platform} link is expired or unavailable.",
            f"Copy a fresh share link from {platform.title()} and try again.",
        ),
    }.get(
        code,
        (
            "ClipMind could not retrieve this video.",
            "Check that the URL opens in a browser, then copy a fresh link and retry.",
        ),
    )


def classify_generic(failure: AttemptFailure) -> str | None:
    """Transport and tool-level classification every adapter shares."""
    reason = failure.reason
    lowered = reason.lower()
    # Scheduled streams and premieres fail during extraction, before any
    # metadata reaches the filter below, with the platform's own wording.
    if (
        "live event will begin" in lowered
        or "live event is scheduled" in lowered
        or "live event has not yet started" in lowered
        or "premieres in" in lowered
    ):
        return "live_stream_unsupported"
    if (
        "private video" in lowered
        or "video is private" in lowered
        or "private account" in lowered
        or "仅自己" in reason
    ):
        return "private_video"
    if "fresh cookies" in lowered:
        return "cookies_stale"
    if "sign in" in lowered or "login required" in lowered or "log in" in lowered:
        return "login_required"
    if "could not copy chrome cookie" in lowered or "failed to decrypt" in lowered:
        return "cookies_unavailable"
    # A bare "permission denied" is only a cookie problem when the tool says
    # cookies. Platforms use the same word for their own access refusals.
    if (
        "permission" in lowered or "operation not permitted" in lowered
    ) and "cookie" in lowered:
        return "cookies_unavailable"
    if "unsupported url" in lowered or "not available" in lowered or "removed" in lowered:
        return "link_unavailable"
    return None


def _code_for(failure: AttemptFailure, adapter) -> str | None:
    """Adapter knowledge first, shared rules second; neither may raise."""
    hook = getattr(adapter, "classify_failure", None) if adapter is not None else None
    if callable(hook):
        try:
            code = hook(failure)
        except Exception:  # noqa: BLE001 - a plugin must not break classification
            logger.exception(
                "Source adapter %s failed while classifying a failure",
                getattr(adapter, "name", "unknown"),
            )
            code = None
        if isinstance(code, str) and code in _RANK:
            return code
        if code is not None:
            logger.warning(
                "Source adapter %s returned an unknown failure code %r",
                getattr(adapter, "name", "unknown"),
                code,
            )
    return classify_generic(failure)


def classify_failures(
    failures: list[AttemptFailure],
    *,
    adapter=None,
    platform: str = "source",
) -> FetchError:
    """Pick the most actionable classification across *every* attempt.

    Ranked rather than positional: the rung that explains the failure is often
    not the last one tried, and with more strategies it can fall outside any
    fixed window entirely.
    """
    diagnostic = "\n".join(failure.diagnostic for failure in failures)
    logger.warning("%s acquisition failed after all strategies: %s", platform, diagnostic)
    best: str | None = None
    for failure in failures:
        code = _code_for(failure, adapter)
        if code is None:
            continue
        if best is None or _RANK[code] < _RANK[best]:
            best = code
    message, action = _message_for(best or "media_fetch_failed", platform)
    return FetchError(best or "media_fetch_failed", message, action)


def _fetch_error(errors: list[str], platform: str = "source") -> FetchError:
    """Compatibility entry for callers that only have diagnostic strings."""
    return classify_failures(
        [AttemptFailure(strategy="", label="", reason=error) for error in errors],
        platform=platform,
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
        return ["--cookies", str(Path(config.cookie_file).expanduser().resolve())]
    return ["--cookies-from-browser", source]


def _describe(source: str) -> str:
    return {"-": "no cookies", "file": "cookie file"}.get(source, f"{source} cookies")


# An analysis job needs an end: a live stream would download until it stopped,
# holding a queue slot the whole time. yt-dlp evaluates this after reading
# metadata and before choosing formats, so a live or scheduled stream costs no
# media bytes and no extra request. `!=?` lets sources that report no status
# through.
LIVE_FILTER = "!is_live & live_status !=? is_upcoming"


def _is_live(info: dict) -> bool:
    return info.get("is_live") is True or info.get("live_status") in {"is_live", "is_upcoming"}


async def _run(args: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    code, out, err = await subprocesses.run(args, cwd=cwd)
    return code, out.decode(errors="replace"), err.decode(errors="replace")


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

    def _failed(self, reason: str) -> AttemptFailure:
        return AttemptFailure(strategy=self.key, label=self.describe(), reason=reason)

    async def acquire(
        self, url: str, root: Path, config: Settings
    ) -> tuple[AcquiredMedia | None, AttemptFailure | None]:
        """Acquire, or report why this rung could not, so the ladder continues.

        Only the failures the ladder is meant to survive become diagnostics.
        Cancellation and genuine environment errors still propagate: swallowing
        them would turn a stopped job into a silent rung failure and strand the
        cleanup that cancellation is supposed to trigger.
        """
        root = root.resolve()
        try:
            cookie_args = _cookie_args(self.source, config)
        except FetchError as exc:
            return None, self._failed(str(exc))

        code, out, err = await _run(
            [
                "yt-dlp",
                "--ignore-config",
                "--no-config-locations",
                "--no-warnings",
                "--no-playlist",
                "--no-progress",
                "--no-simulate",
                "--dump-single-json",
                "--match-filter", LIVE_FILTER,
                "-f", config.fetch_format,
                "-o", str(root / "source.%(ext)s"),
                *cookie_args,
                url,
            ],
            # Explicit settings only. A user config can specify absolute output
            # paths or exec hooks; changing cwd alone is not an ownership guard.
            cwd=root,
        )
        if code != 0 or not out.strip():
            detail = (err or out).strip()
            return None, self._failed(detail.splitlines()[-1] if detail else "failed")

        try:
            info = json.loads(out.splitlines()[-1])
        except json.JSONDecodeError as exc:
            return None, self._failed(f"bad metadata ({exc})")

        if _is_live(info):
            # A property of the source, not of this rung: every other rung would
            # find the same stream, and the filter has kept its media off disk.
            raise FetchError(
                "live_stream_unsupported",
                *_message_for("live_stream_unsupported", "source"),
            )

        path = _downloaded_path(info, root)
        if path is None:
            return None, self._failed("reported success but wrote no file")
        return AcquiredMedia(path=path, info=info), None


@dataclass(frozen=True)
class DouyinBrowserRung:
    """Last rung for Douyin: let the page name its own address, then fetch it.

    Not a retry of the ladder above it. Douyin signs its detail endpoint from
    inside the page, so no cookie source can reach it -- yt-dlp's own extractor
    leaves that signature as a TODO. This rung is the only one that can succeed,
    and it holds a browser open only until the address is known. The media then
    lands in the acquisition-owned directory like any other rung's, so the same
    cleanup contract removes it.
    """

    @property
    def key(self) -> str:
        return "douyin-browser"

    def describe(self) -> str:
        return "browser session"

    def _failed(self, reason: str) -> AttemptFailure:
        return AttemptFailure(strategy=self.key, label=self.describe(), reason=reason)

    async def acquire(
        self, url: str, root: Path, config: Settings
    ) -> tuple[AcquiredMedia | None, AttemptFailure | None]:
        root = root.resolve()
        try:
            resolved = await douyin_browser.resolve(
                url, timeout=config.douyin_browser_timeout
            )
            media = await douyin_browser.download(resolved.urls, root / "source.mp4")
        except douyin_browser.BrowserUnavailable as exc:
            return None, self._failed(f"no usable browser: {exc}")
        except douyin_browser.ResolveFailed as exc:
            return None, self._failed(str(exc))
        return AcquiredMedia(path=media, info=dict(resolved.info)), None


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

    def strategies(self, key: str, config: Settings) -> list:
        sources = list(config.cookie_sources)
        if config.cookie_file:
            sources.append("file")
        remembered = self._winning.get(key)
        if remembered and remembered in sources:
            sources.remove(remembered)
            sources.insert(0, remembered)
        rungs: list = [CookieRung(source) for source in sources]
        # Douyin's endpoint is unreachable without a browser, so this is the rung
        # that decides the outcome rather than a fallback for flakiness. It stays
        # last so a cheaper rung still wins if Douyin ever opens the endpoint up.
        if key == "adapter:douyin" and config.douyin_browser_enabled:
            rungs.append(DouyinBrowserRung())
        return rungs

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
        failures: list[AttemptFailure] = []

        for strategy in self.strategies(key, config):
            if on_note:
                on_note(f"trying {strategy.describe()}")
            acquired, failure = await strategy.acquire(url, root, config)
            if acquired is None:
                # Every attempt is kept. The rung that explains the failure is
                # often not the last one, and a fixed window can drop it.
                failures.append(failure or strategy._failed("failed"))
                continue

            async with self._lock:
                self._winning[key] = strategy.key
            acquisition.record_strategy(workdir, strategy.describe())
            acquired.info["_clipmind_strategy"] = strategy.describe()
            return MediaAsset(
                media_path=acquired.path,
                info=adapter.normalize_info(url, acquired.info),
            )

        raise classify_failures(failures, adapter=adapter, platform=adapter.platform)


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


@dataclass(frozen=True)
class ProbeResult:
    """What a source looks like from outside, without acquiring it.

    ``reachable`` means metadata resolved just now. It is not a promise that
    acquisition will succeed, and nothing here is derived from media bytes.
    """

    status: str  # reachable | unavailable | unknown
    platform: str
    source_id: str | None = None
    title: str | None = None
    duration: float | None = None
    strategy: str | None = None
    failure_code: str | None = None
    network_bytes: int = 0
    network_requests: int = 0

    @property
    def user_message(self) -> str:
        """Explain the result without changing its source-reachability status."""
        if self.status == "reachable":
            return "Source metadata resolved. This does not guarantee acquisition will succeed."
        if self.status == "unavailable":
            return "This probe reported the source as unavailable."
        if self.failure_code == "probe_budget_exceeded":
            return (
                "ClipMind stopped this probe at a configured resource limit. "
                "Source reachability remains unknown."
            )
        return "ClipMind could not determine whether this source is reachable."


# Only these say something about the source. The rest say something about us --
# our cookies, our network, our clock -- and must not be reported as a verdict
# on the video.
_PROBE_UNAVAILABLE = frozenset({"private_video", "link_unavailable", "login_required"})
# A single video's metadata is kilobytes. Far past that means we are reading
# something a probe was never meant to read.
_PROBE_OUTPUT_LIMIT = 4 * 1024 * 1024


class BudgetExceeded(Exception):
    """A child produced more output than the probe agreed to read."""


async def _run_budgeted(
    args: list[str],
    timeout: float,
    *,
    output_limit: int,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess under a wall clock and an output cap it cannot exceed.

    Both streams are read incrementally, because ``communicate()`` buffers
    everything the child writes and only then hands it over -- a cap applied
    afterwards has already been paid for. Crossing the cap kills the child
    immediately, and both pipes keep draining afterwards so the child can never
    block on a write nobody is reading.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=os.name == "posix",
    )
    overflowed = False
    total = 0

    def stop() -> None:
        subprocesses.kill(proc)

    async def read(stream) -> bytes:
        nonlocal overflowed, total
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > output_limit:
                overflowed = True
                stop()
                chunks.clear()
                continue
            chunks.append(chunk)

    async def collect():
        out, err = await asyncio.gather(read(proc.stdout), read(proc.stderr))
        await proc.wait()
        return out, err

    reader = asyncio.create_task(collect())
    try:
        out, err = await asyncio.wait_for(asyncio.shield(reader), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        stop()
        await reader
        raise
    if overflowed:
        raise BudgetExceeded()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _probe_args(url: str, cookie_args: list[str], config: Settings) -> list[str]:
    # No output template, no format selection, and simulate left on: there is
    # nowhere for media to land and nothing asking for it.
    #
    # The user's yt-dlp config is ignored, which is not a preference: a config
    # carrying --write-info-json or --no-simulate turns a probe into something
    # that writes to disk, outside any acquisition directory that would own the
    # result. A promise of "no media, no files" cannot be left to configuration.
    return [
        "yt-dlp",
        "--ignore-config",
        "--no-config-locations",
        "--no-warnings",
        "--no-playlist",
        "--no-progress",
        "--skip-download",
        "--dump-single-json",
        "--socket-timeout", str(config.probe_socket_timeout),
        "--retries", "1",
        *cookie_args,
        url,
    ]


async def probe(url: str, *, config: Settings = settings) -> ProbeResult:
    """Ask what a source is, within a budget, without acquiring it.

    Metadata only. No media is downloaded, no transcription or OCR runs, no job
    directory is created -- this function takes no workdir, so there is nothing
    for a temporary file to belong to -- and a probe that fails never falls back
    to acquisition. When the answer cannot be established the status is
    ``unknown`` rather than a guess in either direction.

    Bounded by elapsed time, HTTP requests, response body bytes read and child
    output. HTTP/TLS headers and kernel socket buffering are not body bytes.
    Unsupported transports or compressed responses are refused, never retried
    through an unbounded backend. yt-dlp still supplies the metadata parsers.
    """
    try:
        adapter = adapter_for(url)
    except SourceError as exc:
        return ProbeResult(status="unavailable", platform="source", failure_code=exc.code)

    if adapter.local:
        path = Path(url.removeprefix("file://")).expanduser()
        if not path.is_file():
            return ProbeResult(
                status="unavailable",
                platform=adapter.platform,
                failure_code="local_file_unavailable",
            )
        return ProbeResult(
            status="reachable",
            platform=adapter.platform,
            title=path.stem,
            strategy="local file",
        )

    if not shutil.which("yt-dlp"):
        return ProbeResult(
            status="unknown", platform=adapter.platform, failure_code="missing_dependency"
        )

    if getattr(sys, "frozen", False):
        # A frozen desktop executable cannot launch a Python worker script.
        # Probe is currently a library-only capability; never fall back to an
        # unbounded CLI backend in a build that cannot execute this boundary.
        return ProbeResult(status="unknown", platform=adapter.platform,
                           failure_code="probe_transport_unsupported")

    deadline = time.monotonic() + max(float(config.probe_timeout), 1.0)
    failures: list[AttemptFailure] = []
    # Belt and braces for the promise above: even with config ignored, the child
    # runs somewhere it cannot pollute, and that somewhere is removed either way.
    sandbox = Path(tempfile.mkdtemp(prefix="clipmind-probe-"))

    try:
        return await _probe_strategies(
            url, adapter, config, deadline, failures, sandbox
        )
    finally:
        stray = list(sandbox.iterdir())
        if stray:
            logger.warning("Probe wrote %d unexpected files; removing temporary workspace", len(stray))
        shutil.rmtree(sandbox)


async def _probe_strategies(
    url: str,
    adapter,
    config: Settings,
    deadline: float,
    failures: list[AttemptFailure],
    sandbox: Path,
) -> ProbeResult:
    bytes_used = requests_used = 0

    def result(**values):
        return ProbeResult(platform=adapter.platform, network_bytes=bytes_used,
                           network_requests=requests_used, **values)

    for strategy in _engine.strategies(_engine.affinity_key(url, adapter), config):
        # A rung that needs a browser is an acquisition strategy, not a way to
        # answer "what is this" inside a probe budget. Probing stays cheap, and
        # never launches a browser to decide whether one would have been needed.
        if not isinstance(strategy, CookieRung):
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        byte_limit = config.probe_max_bytes - bytes_used
        request_limit = config.probe_max_requests - requests_used
        if byte_limit <= 0 or request_limit <= 0:
            return result(status="unknown", failure_code="probe_budget_exceeded")
        try:
            cookie_args = _cookie_args(strategy.source, config)
        except FetchError as exc:
            failures.append(strategy._failed(str(exc)))
            continue

        try:
            code, out, err = await _run_budgeted(
                [sys.executable, str(Path(__file__).with_name("probe_worker.py")),
                 str(byte_limit), str(request_limit), *_probe_args(url, cookie_args, config)[1:]],
                remaining,
                output_limit=_PROBE_OUTPUT_LIMIT,
                cwd=sandbox,
            )
        except asyncio.TimeoutError:
            return result(status="unknown", failure_code="probe_timeout")
        except BudgetExceeded:
            return result(status="unknown", failure_code="probe_budget_exceeded")

        if code != 0 or not out.strip():
            # Without the worker's accounting envelope we cannot safely spend
            # the same quota again on the next rung.
            return result(status="unknown", failure_code="probe_invalid_response")
        try:
            envelope = json.loads(out.splitlines()[-1])
        except json.JSONDecodeError as exc:
            failures.append(strategy._failed(f"bad metadata ({exc})"))
            continue

        if not isinstance(envelope, dict):
            return result(status="unknown", failure_code="probe_invalid_response")
        usage = [envelope.get("network_bytes"), envelope.get("network_requests")]
        if any(type(value) is not int or value < 0 for value in usage):
            return result(status="unknown", failure_code="probe_invalid_response")
        bytes_used += usage[0]
        requests_used += usage[1]
        if bytes_used > config.probe_max_bytes or requests_used > config.probe_max_requests:
            return result(status="unknown", failure_code="probe_budget_exceeded")
        refusal = envelope.get("failure_code")
        if refusal:
            return result(status="unknown", failure_code=refusal if isinstance(refusal, str) and refusal in {
                "probe_budget_exceeded", "probe_transport_unsupported", "probe_invalid_response"
            } else "probe_invalid_response")
        info = envelope.get("metadata")
        if info is None:
            failures.append(strategy._failed(str(envelope.get("error") or "missing metadata")))
            continue

        # Valid JSON is not usable metadata. ``null``, a list, or an object that
        # identifies nothing all parse; none of them answer what this source is.
        if not isinstance(info, dict) or not any(
            isinstance(info.get(field), str) and info[field].strip()
            for field in ("id", "title", "webpage_url")
        ):
            failures.append(strategy._failed("metadata identified no source"))
            continue

        identity = getattr(adapter, "source_id", None)
        try:
            source_id = identity(url) if callable(identity) else None
        except Exception:
            source_id = None
        if not isinstance(source_id, str):
            source_id = None
        duration = info.get("duration")
        return result(
            status="reachable",
            source_id=source_id or (info.get("id") if isinstance(info.get("id"), str) else None),
            title=info.get("title") if isinstance(info.get("title"), str) else None,
            duration=float(duration) if type(duration) in {int, float}
            and math.isfinite(duration) and duration >= 0 else None,
            strategy=strategy.describe(),
        )

    if not failures:
        return result(status="unknown", failure_code="probe_timeout")
    error = classify_failures(failures, adapter=adapter, platform=adapter.platform)
    status = "unavailable" if error.code in _PROBE_UNAVAILABLE else "unknown"
    return result(status=status, failure_code=error.code)


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
