"""Stable source boundary shared by URL and local-file ingestion."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit


class SourceError(RuntimeError):
    def __init__(self, code: str, user_message: str, action: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.action = action


@dataclass(frozen=True)
class SourceAdapter:
    """Declarative adapter: platform identity without duplicating acquisition."""

    name: str
    platform: str
    domains: tuple[str, ...] = ()
    local: bool = False
    generic: bool = False

    def matches(self, source: str) -> bool:
        if self.local:
            return _local_path(source).is_file()
        parsed = urlsplit(source)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if self.generic:
            return True
        host = parsed.hostname.casefold()
        return any(host == domain or host.endswith(f".{domain}") for domain in self.domains)

    def normalize_info(self, source: str, info: dict) -> dict:
        normalized = dict(info)
        normalized["_clipmind_platform"] = self.platform
        normalized["_clipmind_source_adapter"] = self.name
        normalized.setdefault("webpage_url", source)
        return normalized


class SourceAdapterProtocol(Protocol):
    name: str
    platform: str
    local: bool
    generic: bool

    def matches(self, source: str) -> bool: ...

    def normalize_info(self, source: str, info: dict) -> dict: ...


@dataclass(init=False)
class MediaAsset:
    """A local media file plus normalized, platform-neutral source metadata."""

    media_path: Path
    info: dict

    def __init__(
        self,
        media_path: Path | None = None,
        info: dict | None = None,
        *,
        video_path: Path | None = None,
    ) -> None:
        # ``video_path`` keeps the public constructor used by v1 integrations.
        selected = media_path if media_path is not None else video_path
        if selected is None:
            raise TypeError("media_path is required")
        self.media_path = selected
        self.info = dict(info or {})

    @property
    def video_path(self) -> Path:
        """Compatibility alias retained while callers migrate to media_path."""
        return self.media_path

    @property
    def source_id(self) -> str:
        return str(self.info.get("id") or "unknown")

    @property
    def video_id(self) -> str:
        """Compatibility alias for pre-adapter Evidence Packs."""
        return self.source_id

    @property
    def platform(self) -> str:
        return str(self.info.get("_clipmind_platform") or "unknown")

    @property
    def title(self) -> str:
        return (self.info.get("title") or "").strip() or self.source_id

    @property
    def duration(self) -> float:
        try:
            return float(self.info.get("duration") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def uploader(self) -> str | None:
        return self.info.get("uploader") or self.info.get("channel")

    @property
    def webpage_url(self) -> str | None:
        return self.info.get("webpage_url")


def _local_path(source: str) -> Path:
    value = source.removeprefix("file://")
    return Path(value).expanduser()
