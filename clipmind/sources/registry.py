"""Ordered source registry; specific platforms always beat generic URLs."""
from __future__ import annotations

from functools import lru_cache
from importlib import metadata
import logging

from .base import SourceAdapter, SourceError
from .direct import ADAPTER as DIRECT
from .douyin import ADAPTER as DOUYIN
from .local import ADAPTER as LOCAL
from .youtube import ADAPTER as YOUTUBE

ADAPTERS: tuple[SourceAdapter, ...] = (
    LOCAL,
    DOUYIN,
    YOUTUBE,
    DIRECT,
)

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@lru_cache(maxsize=1)
def registered_adapters() -> tuple[SourceAdapter, ...]:
    """Return built-ins plus installed ``clipmind.sources`` entry points."""
    plugins = []
    for entry in metadata.entry_points(group="clipmind.sources"):
        try:
            adapter = entry.load()
            required = (
                "name",
                "platform",
                "local",
                "generic",
                "matches",
                "normalize_info",
            )
            if not all(hasattr(adapter, attribute) for attribute in required):
                raise TypeError("source adapter does not implement the required protocol")
            if not isinstance(adapter.name, str) or not adapter.name.strip():
                raise TypeError("source adapter name must be a non-empty string")
            if not isinstance(adapter.platform, str) or not adapter.platform.strip():
                raise TypeError("source adapter platform must be a non-empty string")
            if not isinstance(adapter.local, bool) or not isinstance(adapter.generic, bool):
                raise TypeError("source adapter flags must be booleans")
            if not callable(adapter.matches) or not callable(adapter.normalize_info):
                raise TypeError("source adapter hooks must be callable")
            plugins.append(adapter)
        except Exception:  # noqa: BLE001 - one plugin cannot disable built-ins
            logger.exception("Could not load source adapter plugin %s", entry.name)
    # The generic direct-URL adapter must remain last.
    return (*ADAPTERS[:-1], *plugins, ADAPTERS[-1])


def adapter_for(source: str) -> SourceAdapter:
    for adapter in registered_adapters():
        try:
            if adapter.matches(source):
                return adapter
        except Exception:  # noqa: BLE001 - a broken plugin must not block fallback
            logger.exception("Source adapter %s failed while matching", adapter.name)
    raise SourceError(
        "unsupported_source",
        "ClipMind does not recognize this source.",
        "Paste a supported http(s) URL or choose a local media file.",
    )


def supported_sources() -> list[dict[str, str]]:
    return [
        {"name": adapter.name, "platform": adapter.platform}
        for adapter in registered_adapters()
        if not adapter.generic
    ]
