"""WeChat Channels share-link identity; media requires an authenticated browser."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .base import SourceAdapter

_SHARE_ID = re.compile(r"[A-Za-z0-9_-]{6,80}\Z")


def share_id(source: str) -> str | None:
    parsed = urlsplit(source)
    if parsed.scheme != "https":
        return None
    if parsed.hostname == "weixin.qq.com":
        parts = parsed.path.strip("/").split("/")
        candidate = parts[1] if len(parts) == 2 and parts[0] == "sph" else ""
    elif parsed.hostname == "channels.weixin.qq.com" and parsed.path == "/finder-preview/pages/sph":
        values = parse_qs(parsed.query).get("id", [])
        candidate = values[0] if len(values) == 1 else ""
    else:
        return None
    return candidate if _SHARE_ID.fullmatch(candidate) else None


class WechatAdapter(SourceAdapter):
    def matches(self, source: str) -> bool:
        return share_id(source) is not None

    def source_id(self, source: str) -> str | None:
        return share_id(source)

    def canonicalize_source(self, source: str) -> str:
        token = share_id(source)
        return f"https://weixin.qq.com/sph/{token}" if token else super().canonicalize_source(source)


ADAPTER = WechatAdapter(name="wechat-channels", platform="wechat", domains=("weixin.qq.com", "channels.weixin.qq.com"))
