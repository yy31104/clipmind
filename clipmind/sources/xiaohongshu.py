"""Bounded public Xiaohongshu notes, with exact identity and ordered pages.

This path reads public page state, not personal browser cookies. Share tokens
are used only in transit; normalized provenance never contains signed queries.
An authentication redirect is a limit of this path, not an expired-link claim.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from .base import MediaAsset, SourceAdapter, SourceError

_PAGE_HOSTS = {"www.xiaohongshu.com", "xiaohongshu.com"}
_SHORT_HOSTS = {"xhslink.com", "xhslink.cn"}
_ID_PATH = re.compile(r"/(?:explore|discovery/item)/([0-9a-f]{24})/?\Z", re.I)
_SHORT_PATH = re.compile(r"/(?:o/)?[A-Za-z0-9_-]{4,80}/?\Z")
_STATE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_PAGES = 1000


def _error(code: str, message: str) -> SourceError:
    return SourceError(code, message,
        "此路径只读取公开页面，不读取个人 Chrome 登录。可提供可访问的单篇分享链接或上传本地视频／原图；无需导出 Cookie。")


def _url(value: object):
    if not isinstance(value, str) or not value or any(ord(c) <= 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 80, 443)):
            return None
        return parsed
    except ValueError:
        return None


def note_id(source: str) -> str | None:
    parsed = _url(source)
    if parsed is None or (parsed.hostname or "").casefold() not in _PAGE_HOSTS:
        return None
    found = _ID_PATH.fullmatch(parsed.path)
    return found.group(1).lower() if found else None


def _page_url(value: str) -> str:
    parsed = _url(value)
    if parsed is None:
        raise _error("unsupported_source", "小红书链接格式无效。")
    host = (parsed.hostname or "").casefold()
    if host in _PAGE_HOSTS and parsed.path.rstrip("/") in {"/login", "/login/qr"}:
        raise _error("login_required", "小红书将此链接转到登录页，公开提取路径未取得正文或媒体。")
    if not ((host in _PAGE_HOSTS and _ID_PATH.fullmatch(parsed.path))
            or (host in _SHORT_HOSTS and _SHORT_PATH.fullmatch(parsed.path))):
        raise _error("unsupported_source", "此路径仅支持小红书单篇笔记和分享短链接。")
    # Upgrade known public hosts; never send share tokens over cleartext HTTP.
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


def _media_url(value: object) -> str | None:
    parsed = _url(value)
    if parsed is None:
        return None
    host = (parsed.hostname or "").casefold()
    if not (host == "xhscdn.com" or host.endswith(".xhscdn.com")):
        return None
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


class XiaohongshuAdapter(SourceAdapter):
    def source_id(self, source: str) -> str | None:
        return note_id(source)

    def canonicalize_source(self, source: str) -> str:
        identity = note_id(source)
        if identity:
            return f"https://www.xiaohongshu.com/explore/{identity}"
        parsed = _url(source)
        if parsed is not None and (parsed.hostname or "").casefold() in _SHORT_HOSTS:
            return urlunsplit(("https", parsed.hostname.casefold(), parsed.path.rstrip("/"), "", ""))
        return super().canonicalize_source(source)

    def normalize_info(self, source: str, info: dict) -> dict:
        identity = str(info.get("id") or "")
        expected = note_id(source)
        if not re.fullmatch(r"[0-9a-f]{24}", identity) or (expected and expected != identity):
            raise _error("source_identity_mismatch", "小红书返回的笔记与请求不一致，已拒绝使用推荐内容。")
        safe = {key: info[key] for key in (
            "id", "title", "description", "uploader", "duration", "_clipmind_media_type",
            "_clipmind_audio_status", "_clipmind_strategy") if key in info}
        safe["webpage_url"] = f"https://www.xiaohongshu.com/explore/{identity}"
        safe["_clipmind_platform"] = self.platform
        safe["_clipmind_source_adapter"] = self.name
        return safe


ADAPTER = XiaohongshuAdapter(name="xiaohongshu", platform="xiaohongshu",
    domains=("xiaohongshu.com", "xhslink.com", "xhslink.cn"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Stopped(Exception):
    pass


class _Transport:
    """Shared deadline, body-byte and request limits across retries/redirects."""
    def __init__(self, *, timeout: float, max_bytes: int, max_requests: int, stop=None):
        if (type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0
                or type(max_bytes) is not int or max_bytes < 0
                or type(max_requests) is not int or max_requests < 0):
            raise _error("source_limit_exceeded", "小红书获取预算必须为有效的有限数值。")
        self.deadline = time.monotonic() + timeout
        self.max_bytes = max_bytes
        self.max_requests = max_requests
        self.bytes = self.requests = 0
        self.stop = stop or threading.Event()
        self.opener = urllib.request.build_opener(_NoRedirect())

    def check(self):
        if self.stop.is_set():
            raise _Stopped()
        if time.monotonic() >= self.deadline:
            raise _error("source_timeout", "小红书获取超过时间限制，未生成不完整资料包。")

    def read(self, url: str, *, metadata: bool, destination: Path | None = None, on_progress=None):
        current = url
        expected = note_id(url) if metadata else None
        for _ in range(6):
            self.check()
            if self.requests >= self.max_requests:
                raise _error("source_limit_exceeded", "小红书获取超过请求数量限制。")
            checked = _page_url(current) if metadata else _media_url(current)
            if checked is None:
                raise _error("source_media_unavailable", "小红书媒体地址不在受支持的来源域名内。")
            found = note_id(checked) if metadata else None
            if found and expected and found != expected:
                raise _error("source_identity_mismatch", "小红书重定向到另一篇笔记，已拒绝替代来源。")
            expected = expected or found
            self.requests += 1
            try:
                response = self.opener.open(urllib.request.Request(checked, headers=_HEADERS),
                    timeout=min(15., max(.1, self.deadline - time.monotonic())))
            except urllib.error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    exc.close()
                    if not location:
                        raise _error("source_metadata_unavailable", "小红书重定向没有目标地址。") from None
                    current = urljoin(checked, location)
                    continue
                exc.close()
                code = "source_access_denied" if exc.code in {401, 403, 429} else "source_metadata_unavailable"
                raise _error(code, f"小红书返回 HTTP {exc.code}，尚未取得请求的媒体。") from None
            except (OSError, urllib.error.URLError):
                raise _error("source_network_error", "连接小红书来源失败，尚未取得完整媒体。") from None
            with response:
                final = _page_url(response.url) if metadata else _media_url(response.url)
                if final != checked:
                    raise _error("source_identity_mismatch", "小红书响应地址未通过来源校验。")
                if response.status != 200:
                    raise _error("source_media_unavailable", "小红书未返回完整内容，已拒绝部分响应。")
                if response.headers.get("Content-Range") is not None:
                    raise _error("source_media_unavailable", "小红书返回了分段内容，无法确认媒体完整性。")
                if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise _error("source_media_unavailable", "小红书返回不受支持的压缩传输。")
                try:
                    length = response.headers.get("Content-Length")
                    declared = int(length) if length is not None else None
                    if declared is not None and declared < 0:
                        raise ValueError()
                except ValueError:
                    raise _error("source_media_unavailable", "小红书响应长度无效。") from None
                available = self.max_bytes - self.bytes
                if declared is not None and declared > available:
                    raise _error("source_limit_exceeded", "小红书内容超过本次传输大小限制。")
                chunks, received = [], 0
                handle = destination.open("wb") if destination is not None else None
                try:
                    while True:
                        self.check()
                        # read(n) can aggregate many slow socket reads before
                        # returning; read1 lets deadline/cancellation checks run
                        # after each available block instead of waiting for n.
                        read = getattr(response, "read1", response.read)
                        block = read(min(65536, self.max_bytes - self.bytes + 1))
                        if not block:
                            break
                        self.bytes += len(block)
                        received += len(block)
                        if self.bytes > self.max_bytes or (declared is not None and received > declared):
                            raise _error("source_limit_exceeded", "小红书响应超过声明或配置的大小限制。")
                        if handle is not None:
                            handle.write(block)
                        else:
                            chunks.append(block)
                        if on_progress is not None:
                            on_progress(received, declared)
                    if received == 0 or (declared is not None and received != declared):
                        raise _error("source_media_unavailable", "小红书媒体传输不完整，未发布资料包。")
                finally:
                    if handle is not None:
                        handle.close()
                return checked, b"".join(chunks)
        raise _error("source_limit_exceeded", "小红书链接重定向次数过多。")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate metadata key")
        result[key] = value
    return result


@dataclass(frozen=True)
class ResolvedNote:
    info: dict
    urls: tuple[str, ...] = ()
    image_urls: tuple[tuple[str, ...], ...] = ()


def parse_page(body: bytes, page_url: str) -> ResolvedNote:
    """Parse only the exact noteDetailMap entry, never page-wide media search."""
    expected = note_id(page_url)
    if expected is None or len(body) > MAX_METADATA_BYTES:
        raise _error("source_metadata_unavailable", "小红书页面未提供可确认的笔记标识。")
    text = body.decode("utf-8", errors="replace")
    matches = list(_STATE.finditer(text))
    if len(matches) != 1:
        raise _error("source_metadata_unavailable", "小红书公开页面未提供可验证的目标笔记正文；未使用推荐内容。")
    raw = text[matches[0].end():].split("</script>", 1)[0].strip().rstrip(";")
    try:
        try:
            state = json.loads(raw, object_pairs_hook=_unique_object)
        except json.JSONDecodeError:
            # Most pages contain JSON. Only JavaScript literal syntax such as
            # `undefined` needs the optional converter; neither path executes
            # page JavaScript. Missing installation dependencies must not be
            # misreported as missing/wrong upstream note identity.
            try:
                from yt_dlp.utils import js_to_json
            except ImportError:
                raise SourceError(
                    "missing_dependency", "解析此小红书页面需要安装 yt-dlp 依赖。",
                    "请按 ClipMind 安装说明恢复锁定依赖后重启；无需刷新登录或导出 Cookie。",
                ) from None
            state = json.loads(js_to_json(raw), object_pairs_hook=_unique_object)
        notes = state["note"]["noteDetailMap"]
        note = notes[expected]["note"]
        if not isinstance(note, dict) or note.get("noteId", note.get("id")) != expected:
            raise ValueError("wrong note identity")
    except (ValueError, TypeError, KeyError, RecursionError):
        raise _error("source_metadata_unavailable", "小红书页面没有唯一匹配的目标笔记，已拒绝使用推荐内容。") from None
    info = {"id": expected, "title": str(note.get("title") or expected)[:2000],
        "description": str(note.get("desc") or "")[:20000], "duration": 0.0}
    user = note.get("user")
    if isinstance(user, dict):
        info["uploader"] = str(user.get("nickname") or "")[:500]
    kind = note.get("type")
    if kind == "normal":
        pages = note.get("imageList")
        if not isinstance(pages, list) or not pages or len(pages) > MAX_PAGES:
            raise _error("image_collection_limit_exceeded", "小红书图文没有完整可用的图片列表，或图片数量超过限制。")
        groups = []
        for page in pages:
            if not isinstance(page, dict):
                raise _error("image_collection_fetch_failed", "小红书图文的图片元数据不完整。")
            if page.get("livePhoto") or page.get("stream") or page.get("video"):
                raise _error("source_type_unsupported", "小红书动态图尚未完整适配；未将静态封面冒充全部内容。")
            url = _media_url(page.get("urlDefault"))
            if url is None:
                raise _error("image_collection_fetch_failed", "小红书图文存在缺页或不受支持的图片地址。")
            groups.append((url,))
        info.update(_clipmind_media_type="image_collection", _clipmind_audio_status="not_acquired")
        return ResolvedNote(ADAPTER.normalize_info(page_url, info), image_urls=tuple(groups))
    if kind != "video":
        raise _error("source_type_unsupported", "小红书笔记类型尚不支持完整提取。")
    video = note.get("video")
    try:
        streams = video["media"]["stream"]
        candidates = []
        for codec in ("h264", "h265", "av1"):
            for stream in streams.get(codec, []):
                if not isinstance(stream, dict):
                    continue
                addresses = [stream.get("masterUrl"), *(stream.get("backupUrls") or [])]
                urls = tuple(dict.fromkeys(url for value in addresses if (url := _media_url(value))))
                if urls:
                    width = float(stream.get("width") or 0)
                    duration = float(stream.get("duration") or 0) / 1000
                    if not math.isfinite(width) or not math.isfinite(duration) or width < 0 or duration < 0:
                        continue
                    candidates.append((codec == "h264", width, urls, duration))
        if not candidates:
            raise ValueError("missing video")
        _codec, _width, urls, duration = max(candidates, key=lambda row: row[:2])
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        raise _error("source_media_unavailable", "已识别小红书目标笔记，但没有受支持的完整视频地址。") from None
    info.update(duration=duration, _clipmind_media_type="video")
    return ResolvedNote(ADAPTER.normalize_info(page_url, info), urls=urls[:16])


def resolve(url: str, *, timeout: float = 30., max_bytes: int = MAX_METADATA_BYTES,
            max_requests: int = 8, stop=None) -> tuple[ResolvedNote, dict]:
    transport = _Transport(timeout=timeout, max_bytes=min(max_bytes, MAX_METADATA_BYTES),
        max_requests=max_requests, stop=stop)
    try:
        final, body = transport.read(_page_url(url), metadata=True)
        return parse_page(body, final), {"network_bytes": transport.bytes, "network_requests": transport.requests}
    except SourceError as exc:
        exc.usage = {"network_bytes": transport.bytes, "network_requests": transport.requests}
        raise
    except (OSError, urllib.error.URLError):
        exc = _error("source_network_error", "连接小红书来源失败，尚未取得完整元数据。")
        exc.usage = {"network_bytes": transport.bytes, "network_requests": transport.requests}
        raise exc from None


def _acquire(url, directory, stop, *, max_bytes, on_note):
    resolved, _usage = resolve(url, stop=stop)
    transport = _Transport(timeout=900.,
        max_bytes=min(max_bytes, 512 * 1024 * 1024) if resolved.image_urls else max_bytes,
        max_requests=6 * max(1, len(resolved.image_urls), len(resolved.urls)), stop=stop)
    if resolved.image_urls:
        from .douyin_images import _image_extension
        images = []
        for index, addresses in enumerate(resolved.image_urls, 1):
            path = directory / f"page-{index:04d}.part"
            transport.read(addresses[0], metadata=False, destination=path)
            try:
                extension = _image_extension(path, 40_000_000)
            except Exception:
                raise _error("image_collection_fetch_failed", f"小红书第 {index} 页无法完整解码，未发布缺页资料包。") from None
            final = path.with_suffix(extension)
            path.replace(final)
            images.append(final)
            on_note(f"正在获取小红书原页面：{index} / {len(resolved.image_urls)}")
        return MediaAsset(images[0], resolved.info, image_paths=images)
    destination = directory / "source.mp4"
    start = time.monotonic()
    last = 0.
    def progress(received, total):
        nonlocal last
        now = time.monotonic()
        if now - last >= 1 or total == received:
            last = now
            amount = f"{received / 1_000_000:.1f}"
            if total is not None:
                amount += f" / {total / 1_000_000:.1f}"
            on_note(f"正在接收临时媒体：{amount} MB · 平均 {received / max(.001, now-start) / 1_000_000:.1f} MB/s")
    # Mirrors belong to the same exact note; budgets include unsuccessful reads.
    for address in resolved.urls:
        try:
            transport.read(address, metadata=False, destination=destination, on_progress=progress)
            return MediaAsset(destination, resolved.info)
        except SourceError as exc:
            destination.unlink(missing_ok=True)
            if exc.code in {"source_timeout", "source_limit_exceeded"}:
                raise
    raise _error("source_media_unavailable", "已识别小红书笔记，但完整视频传输失败。")


async def acquire(url: str, root: Path, *, max_bytes: int, on_note=None) -> MediaAsset:
    if type(max_bytes) is not int or max_bytes <= 0 or root.is_symlink():
        raise _error("source_limit_exceeded", "小红书媒体获取配置无效。")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="xiaohongshu-", dir=root))
    stop = threading.Event()
    loop = asyncio.get_running_loop()
    active = True
    def notify(message):
        if active and on_note is not None:
            with contextlib.suppress(Exception):
                on_note(message)
    def post(message):
        loop.call_soon_threadsafe(notify, message)
    worker = asyncio.create_task(asyncio.to_thread(_acquire, url, directory, stop,
        max_bytes=max_bytes, on_note=post))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        while not worker.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(worker)
        with contextlib.suppress(BaseException):
            worker.result()
        shutil.rmtree(directory)
        raise
    except (OSError, urllib.error.URLError):
        shutil.rmtree(directory)
        raise _error("source_network_error", "小红书传输或临时文件写入失败，未发布不完整资料包。") from None
    except BaseException:
        shutil.rmtree(directory)
        raise
    finally:
        active = False
