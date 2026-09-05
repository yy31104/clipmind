"""Extract and normalize supported video sources from pasted text."""
from __future__ import annotations

import re
from pathlib import Path

from .sources.registry import canonicalize_source, source_id

_URL_RE = re.compile(r"https?://[^\s<>\"'，。；！？、（）【】]+", re.IGNORECASE)
_TRAILING = "。，、；：！？）」』】…,.;:!?)]}"


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
    """Compatibility wrapper for the selected adapter's stable cache key."""
    return canonicalize_source(url)


def source_id_from_url(url: str) -> str | None:
    """Compatibility wrapper for the selected adapter's pre-acquisition ID."""
    return source_id(url)


def guess_title(text: str, url: str) -> str | None:
    """Best-effort title from share text, shown until metadata resolves."""
    head = (text or "").split(url)[0]
    head = head.rsplit(":/", 1)[-1]
    head = re.sub(r"^\s*\d{2}/\d{2}\b", " ", head)
    head = re.sub(r"^\s*\S+@\S+", " ", head)
    head = re.sub(r"#\S+", " ", head)
    head = re.sub(r"\s+", " ", head).strip()
    return head[:120] or None
