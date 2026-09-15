import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .base import SourceAdapter
from .identity import canonical_host, canonicalize_generic_source


def numeric_source_id(source: str) -> str | None:
    modal = modal_video_id(source)
    if modal:
        return modal
    # The old helper recognized these paths on any host, including share URLs.
    match = re.search(r"/(?:video|note)/(\d+)(?:/|$)", urlsplit(source).path)
    return match.group(1) if match else None


def modal_video_id(source: str) -> str | None:
    parsed = urlsplit(source)
    host = (parsed.hostname or "").lower()
    if host not in {"douyin.com", "www.douyin.com"} or parsed.path.rstrip("/") != "/user/self":
        return None
    values = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key == "modal_id"]
    return values[0] if len(values) == 1 and re.fullmatch(r"[0-9]+", values[0]) else None


@dataclass(frozen=True)
class DouyinAdapter(SourceAdapter):
    def handles_canonical_identity(self, source: str) -> bool:
        host = canonical_host(source)
        return host.endswith("douyin.com") or host == "iesdouyin.com"

    def canonicalize_source(self, source: str) -> str:
        value = source.strip()
        modal = modal_video_id(value)
        if modal:
            return f"https://douyin.com/video/{modal}"
        if not self.handles_canonical_identity(value):
            return canonicalize_generic_source(value)
        path = urlsplit(value).path.rstrip("/") or "/"
        return urlunsplit(("https", canonical_host(value), path, "", ""))

    def classify_failure(self, failure) -> str | None:
        # This extractor message is also emitted with no cookies. It is not
        # evidence that a user's browser session expired or a video is private.
        if "fresh cookies" in failure.reason.lower():
            return "source_metadata_unavailable"
        return None


ADAPTER = DouyinAdapter(
    name="douyin",
    platform="douyin",
    domains=("douyin.com", "iesdouyin.com"),
)
