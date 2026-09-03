"""Source adapter registry for URLs and local media."""

from .base import MediaAsset, SourceAdapter, SourceError
from .registry import adapter_for, supported_sources

__all__ = [
    "MediaAsset",
    "SourceAdapter",
    "SourceError",
    "adapter_for",
    "supported_sources",
]
