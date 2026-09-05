import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .base import SourceAdapter
from .identity import canonical_host, canonicalize_generic_source


def numeric_source_id(source: str) -> str | None:
    # The old helper recognized these paths on any host, including share URLs.
    match = re.search(r"/(?:video|note)/(\d+)(?:/|$)", urlsplit(source).path)
    return match.group(1) if match else None


@dataclass(frozen=True)
class DouyinAdapter(SourceAdapter):
    def handles_canonical_identity(self, source: str) -> bool:
        host = canonical_host(source)
        return host.endswith("douyin.com") or host == "iesdouyin.com"

    def canonicalize_source(self, source: str) -> str:
        value = source.strip()
        if not self.handles_canonical_identity(value):
            return canonicalize_generic_source(value)
        path = urlsplit(value).path.rstrip("/") or "/"
        return urlunsplit(("https", canonical_host(value), path, "", ""))


ADAPTER = DouyinAdapter(
    name="douyin",
    platform="douyin",
    domains=("douyin.com", "iesdouyin.com"),
)
