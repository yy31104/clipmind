"""Generic identity defaults, including pre-adapter helper compatibility.

The old helpers accepted more inputs than acquisition matching does. Keep those
rules here for generic/legacy adapters without broadening acquisition domains.
New adapters provide their own identity hooks instead of extending this shim.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "feature", "si", "spm_id_from", "share_source", "share_medium",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
}
_VIDEO_ID_RE = re.compile(r"/(BV[0-9A-Za-z]+|av\d+)(?:/|$)", re.IGNORECASE)


def _bilibili_identity(source: str) -> tuple[str, str | None] | None:
    """Return (video ID, exact cache ID); None cache IDs are unsafe to reuse."""
    parsed = urlsplit(source)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not (
        host == "bilibili.com" or host.endswith(".bilibili.com")
    ):
        return None
    match = _VIDEO_ID_RE.search(parsed.path)
    if not match:
        return None
    video_id = match.group(1)
    parts = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key == "p"]
    if not parts:
        return video_id, video_id  # Unspecified is deliberately NOT part 1.
    if len(parts) != 1 or not re.fullmatch(r"[0-9]+", parts[0]):
        return video_id, None
    part = parts[0].lstrip("0")
    return video_id, f"{video_id}_p{part}" if part else None


def allows_source_reuse(source: str, stored_source: str, media_id: str) -> bool:
    """Veto ambiguous legacy multipart identities, including same-URL matches.

    This does not authorize reuse: the caller must still match a source and
    validate the complete pack. Other platforms retain their existing policy.
    Downloader IDs and stored artifacts are never rewritten or inferred.
    """
    requested = _bilibili_identity(source)
    stored = _bilibili_identity(stored_source)
    if requested is None and stored is None:
        return True
    if requested is None or stored is None:
        return False
    return bool(requested[1] and requested == stored and requested[1] == media_id)


def canonical_host(source: str) -> str:
    host = (urlsplit(source).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def canonicalize_generic_source(source: str) -> str:
    parsed = urlsplit(source.strip())
    path = parsed.path.rstrip("/") or "/"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    stable_query = sorted(
        (key, item) for key, item in query if key.casefold() not in _TRACKING_KEYS
    )
    return urlunsplit(("https", canonical_host(source), path, urlencode(stable_query), ""))


def generic_source_id(source: str) -> str | None:
    from .douyin import numeric_source_id

    source_id = numeric_source_id(source)
    if source_id is not None:
        return source_id
    multipart = _bilibili_identity(source)
    if multipart is not None:
        return multipart[1]
    # Historically host-independent, even though Bilibili is not a verified
    # built-in. Preserve this fallback without adding an acquisition adapter.
    match = _VIDEO_ID_RE.search(urlsplit(source).path)
    return match.group(1) if match else None


def legacy_canonicalize_source(source: str) -> str:
    # Lazy imports keep adapter defaults independent of registry construction.
    from .douyin import ADAPTER as DOUYIN
    from .youtube import ADAPTER as YOUTUBE

    value = source.strip()
    local = Path(value.removeprefix("file://")).expanduser()
    if "://" not in value and local.is_file():
        return local.resolve().as_uri()
    for adapter in (YOUTUBE, DOUYIN):
        if adapter.handles_canonical_identity(value):
            return adapter.canonicalize_source(value)
    return canonicalize_generic_source(value)


def legacy_source_id(source: str) -> str | None:
    from .youtube import ADAPTER as YOUTUBE

    # A recognized YouTube host returning None must not fall through to numeric
    # path IDs. That precedence is part of the existing public helper behavior.
    if YOUTUBE.handles_source_id(source):
        return YOUTUBE.source_id(source)
    return generic_source_id(source)
