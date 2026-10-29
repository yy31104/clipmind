"""Resolve WeChat Channels media with a user's local Tencent browser session.

The public sph page is a preview and does not expose media. Yuanbao can turn a
share link into an authorized feed page after the user signs in on its own site.
No cookies, tokens, or playable URLs are written to logs or Evidence Packs.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .wechat import share_id

YUANBAO = "https://yuanbao.tencent.com/"
PARSE_API = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
FEED_HOST = "channels.weixin.qq.com"


class BrowserUnavailable(RuntimeError):
    pass


class ResolveFailed(RuntimeError):
    pass


def profile_dir() -> Path:
    """Keep account state in a private user data directory, outside checkout/out."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "ClipMind" / "wechat-browser"
    return Path.home() / ".local" / "share" / "clipmind" / "wechat-browser"


def _safe_media_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    trusted = ("qq.com", "qpic.cn", "gtimg.cn", "myqcloud.com")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ResolveFailed("微信返回了无效的媒体地址") from exc
    if (parsed.scheme != "https" or parsed.username or parsed.password or port
            or not any(host == suffix or host.endswith("." + suffix) for suffix in trusted)):
        raise ResolveFailed("微信返回了无法验证来源的媒体地址")
    return value


@dataclass(frozen=True)
class ResolvedMedia:
    url: str
    info: dict


def _parse_playable(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ResolveFailed("腾讯元宝尚未提供可播放链接，请确认已在官方网页登录")
    playable = str(data.get("playable_url") or "")
    parsed = urlsplit(playable)
    if (parsed.scheme != "https" or parsed.hostname != FEED_HOST
            or parsed.path != "/finder-preview/pages/feed"):
        raise ResolveFailed("腾讯元宝没有返回有效的视频号播放页")
    return playable


def _feed_media(payload: dict, source: str, expected_export_id: str) -> ResolvedMedia:
    if not isinstance(payload, dict) or payload.get("errCode") != 0:
        raise ResolveFailed("微信视频号未提供可用的视频信息")
    data = payload.get("data") or {}
    feed = data.get("feedInfo") or {}
    if not isinstance(feed, dict):
        raise ResolveFailed("微信视频号未提供视频信息")
    scene = data.get("sceneInfo") or {}
    actual = str((scene.get("dynamicExportId") if isinstance(scene, dict) else "")
                 or feed.get("exportId") or feed.get("export_id") or "")
    if actual and actual != expected_export_id:
        raise ResolveFailed("微信返回了另一条视频，已停止处理")
    url = ""
    for key in ("h264VideoInfo", "h265VideoInfo"):
        variant = feed.get(key) or {}
        if isinstance(variant, dict) and variant.get("videoUrl"):
            url = str(variant["videoUrl"])
            break
    url = url or str(feed.get("videoUrl") or "")
    if not url:
        raise ResolveFailed("微信只返回了预览信息，未提供可下载的视频文件")
    author = data.get("authorInfo") or {}
    info = {
        "id": share_id(source),
        "title": str(feed.get("description") or feed.get("desc") or "").strip(),
        "uploader": str(author.get("nickname") or "").strip() if isinstance(author, dict) else "",
        "webpage_url": source,
    }
    return ResolvedMedia(_safe_media_url(url), {k: v for k, v in info.items() if v})


async def login() -> None:
    """Show the official sign-in page; the user scans and closes it themselves."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserUnavailable("Playwright 未安装") from exc
    directory = profile_dir()
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as engine:
        try:
            context = await engine.chromium.launch_persistent_context(
                str(directory), channel="chrome", headless=False, locale="zh-CN"
            )
        except Exception as exc:
            raise BrowserUnavailable("无法打开 Chrome，请先关闭同一 ClipMind 登录窗口") from exc
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(YUANBAO, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_event("close", timeout=300000)
            except Exception as exc:
                raise BrowserUnavailable("腾讯元宝登录窗口无法打开或等待已超时") from exc
        finally:
            await context.close()


async def resolve(source: str, *, timeout: float = 90.0) -> ResolvedMedia:
    token = share_id(source)
    if not token:
        raise ResolveFailed("不是有效的微信视频号分享链接")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserUnavailable("Playwright 未安装") from exc
    directory = profile_dir()
    if not directory.exists():
        raise ResolveFailed("请先运行 clipmind wechat-login，在腾讯元宝官方网页扫码登录")
    async with async_playwright() as engine:
        try:
            context = await engine.chromium.launch_persistent_context(
                str(directory), channel="chrome", headless=True, locale="zh-CN"
            )
        except Exception as exc:
            raise BrowserUnavailable("无法打开微信专用 Chrome 会话，请关闭登录窗口后重试") from exc
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(YUANBAO, wait_until="domcontentloaded", timeout=45000)
            result = await page.evaluate(
                """async ({url, endpoint}) => {
                    const response = await fetch(endpoint, {
                      method: 'POST', credentials: 'include',
                      headers: {'content-type': 'application/json'},
                      body: JSON.stringify({type:'video_channel_url', url, scene:1})
                    });
                    return {status: response.status, body: await response.json().catch(() => ({}))};
                }""",
                {"url": source, "endpoint": PARSE_API},
            )
            if result.get("status") in (401, 403):
                raise ResolveFailed("腾讯元宝登录已失效；请运行 clipmind wechat-login 重新扫码")
            if not 200 <= int(result.get("status") or 0) < 300:
                raise ResolveFailed("腾讯元宝暂时无法解析这条分享链接")
            body = result.get("body") or {}
            playable = _parse_playable(body)
            expected_export_id = str((body.get("data") or {}).get("wx_export_id") or "")
            if not expected_export_id or parse_qs(urlsplit(playable).query).get("eid") != [expected_export_id]:
                raise ResolveFailed("腾讯元宝未返回视频标识")
            captured: list[dict] = []

            async def on_response(response) -> None:
                if response.url.startswith("https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"):
                    try:
                        captured.append(await response.json())
                    except Exception:
                        pass

            page.on("response", on_response)
            await page.goto(playable, wait_until="domcontentloaded", timeout=45000)
            deadline = asyncio.get_running_loop().time() + max(10.0, timeout)
            while not captured and asyncio.get_running_loop().time() < deadline:
                await page.wait_for_timeout(250)
            if not captured:
                raise ResolveFailed("微信播放页未返回视频文件，可能需要在微信中查看")
            return _feed_media(captured[-1], source, expected_export_id)
        except ResolveFailed:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ResolveFailed("微信网页请求失败，请稍后重试") from exc
        finally:
            await context.close()


async def acquire(source: str, destination: Path, *, timeout: float = 90.0) -> ResolvedMedia:
    resolved = await resolve(source, timeout=timeout)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_download_to, resolved.url, destination, stop))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        while not worker.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(worker)
        raise
    except Exception as exc:
        raise ResolveFailed("微信媒体下载失败，可能已过期或需要在微信中查看") from exc
    return resolved


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _safe_media_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _download_to(url: str, destination: Path, stop: threading.Event) -> None:
    opener = urllib.request.build_opener(_SafeRedirects())
    request = urllib.request.Request(url, headers={"Referer": "https://channels.weixin.qq.com/"})
    written = 0
    with opener.open(request, timeout=60) as response, destination.open("wb") as handle:
        while True:
            if stop.is_set():
                return
            block = response.read(1 << 20)
            if not block:
                break
            handle.write(block)
            written += len(block)
    if not written:
        raise ResolveFailed("微信媒体地址返回空文件")
