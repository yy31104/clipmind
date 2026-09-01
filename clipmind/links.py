"""Pull Douyin URLs out of whatever the user pasted.

Douyin share text looks like:
    4.66 g@b.nQ 09/22 :5pm ULJ:/ <title> #tag https://v.douyin.com/XXXX/ 复制此链接...
so we cannot assume the clipboard holds a bare URL.
"""
from __future__ import annotations

import re

_PATTERNS = (
    r"https?://v\.douyin\.com/[A-Za-z0-9_\-]+",
    r"https?://(?:www\.)?douyin\.com/video/\d+",
    r"https?://(?:www\.)?douyin\.com/note/\d+",
    r"https?://(?:www\.)?iesdouyin\.com/share/(?:video|note)/\d+",
)
_URL_RE = re.compile("|".join(f"(?:{p})" for p in _PATTERNS))

# Trailing characters that are punctuation in the share blurb, never part of a URL.
_TRAILING = "。，、；：！？）」』】…,.;:!?)]}\"'"


def extract_urls(text: str) -> list[str]:
    """Return de-duplicated Douyin URLs in the order they appear."""
    seen: dict[str, None] = {}
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING).rstrip("/")
        seen.setdefault(url, None)
    return list(seen)


def guess_title(text: str, url: str) -> str | None:
    """Best-effort title from the share blurb, shown until real metadata lands.

    The blurb is '<code> <date> <code>:/ <title> #tag #tag <url> 复制此链接...',
    so the title is what sits between the last ':/' marker and the hashtags.
    """
    head = (text or "").split(url)[0]
    head = head.rsplit(":/", 1)[-1]              # drop the share-code preamble
    head = re.sub(r"^\s*\d{2}/\d{2}\b", " ", head)  # leading date
    head = re.sub(r"^\s*\S+@\S+", " ", head)     # leading 'E@U.YZ' style code
    head = re.sub(r"#\S+", " ", head)             # hashtags
    head = re.sub(r"\s+", " ", head).strip()
    return head[:120] or None
