"""Extract and normalize supported video sources from pasted text."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_URL_RE = re.compile(r"https?://[^\s<>\"'，。；！？、（）【】]+", re.IGNORECASE)
_TRAILING = "。，、；：！？）」』】…,.;:!?)]}"
_TRACKING_KEYS = {
    "feature", "si", "spm_id_from", "share_source", "share_medium",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
}


def extract_urls(text: str) -> list[str]:
    """Return de-duplicated http(s) URLs in source order."""
    seen: dict[str, None] = {}
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING).rstrip("/")
        seen.setdefault(url, None)
    return list(seen)


def extract_sources(text: str) -> list[str]:
    """Extract URLs, or accept one explicit existing local path."""
    urls = extract_urls(text)
    if urls:
        return urls
    value = (text or "").strip().removeprefix("file://")
    if value and Path(value).expanduser().is_file():
        return [str(Path(value).expanduser().resolve())]
    return []


def normalize_url(url: str) -> str:
    """Return a stable cache key while preserving identity-bearing query data."""
    value = url.strip()
    local = Path(value.removeprefix("file://")).expanduser()
    if "://" not in value and local.is_file():
        return local.resolve().as_uri()

    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    query = parse_qsl(parsed.query, keep_blank_values=True)

    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
        return f"https://youtube.com/watch?v={video_id}" if video_id else value
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if path == "/watch":
            video_id = dict(query).get("v")
            if video_id:
                return f"https://youtube.com/watch?v={video_id}"
        return urlunsplit(("https", "youtube.com", path, "", ""))
    if host.endswith("douyin.com") or host == "iesdouyin.com":
        return urlunsplit(("https", host, path, "", ""))

    stable_query = sorted(
        (key, item) for key, item in query if key.casefold() not in _TRACKING_KEYS
    )
    return urlunsplit(("https", host, path, urlencode(stable_query), ""))


def source_id_from_url(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path
    if host == "youtu.be":
        return path.strip("/").split("/")[0] or None
    if host.endswith("youtube.com"):
        if path == "/watch":
            return dict(parse_qsl(parsed.query)).get("v")
        match = re.search(r"/(?:shorts|live|embed)/([^/?]+)", path)
        return match.group(1) if match else None
    match = re.search(r"/(?:video|note)/(\d+)(?:/|$)", path)
    if match:
        return match.group(1)
    match = re.search(r"/(BV[0-9A-Za-z]+|av\d+)(?:/|$)", path, re.IGNORECASE)
    return match.group(1) if match else None


def guess_title(text: str, url: str) -> str | None:
    """Best-effort title from share text, shown until metadata resolves."""
    head = (text or "").split(url)[0]
    head = head.rsplit(":/", 1)[-1]
    head = re.sub(r"^\s*\d{2}/\d{2}\b", " ", head)
    head = re.sub(r"^\s*\S+@\S+", " ", head)
    head = re.sub(r"#\S+", " ", head)
    head = re.sub(r"\s+", " ", head).strip()
    return head[:120] or None
