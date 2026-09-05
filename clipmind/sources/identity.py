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
    # Historically host-independent, even though Bilibili is not a verified
    # built-in. Preserve this fallback without adding an acquisition adapter.
    match = re.search(r"/(BV[0-9A-Za-z]+|av\d+)(?:/|$)", urlsplit(source).path, re.IGNORECASE)
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
