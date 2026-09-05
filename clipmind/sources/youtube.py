import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .base import SourceAdapter
from .identity import canonical_host, canonicalize_generic_source, generic_source_id


@dataclass(frozen=True)
class YouTubeAdapter(SourceAdapter):
    def handles_canonical_identity(self, source: str) -> bool:
        return canonical_host(source) in {
            "youtu.be", "youtube.com", "m.youtube.com", "music.youtube.com",
        }

    def handles_source_id(self, source: str) -> bool:
        # Preserve the old suffix rule for identity only. Acquisition continues
        # to use SourceAdapter.matches(), including its stricter domain boundary.
        host = (urlsplit(source).hostname or "").casefold()
        return host == "youtu.be" or host.endswith("youtube.com")

    def canonicalize_source(self, source: str) -> str:
        value = source.strip()
        if not self.handles_canonical_identity(value):
            return canonicalize_generic_source(value)
        parsed = urlsplit(value)
        path = parsed.path.rstrip("/") or "/"
        if canonical_host(value) == "youtu.be":
            video_id = path.strip("/").split("/")[0]
            return f"https://youtube.com/watch?v={video_id}" if video_id else value
        if path == "/watch":
            video_id = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("v")
            if video_id:
                return f"https://youtube.com/watch?v={video_id}"
        return urlunsplit(("https", "youtube.com", path, "", ""))

    def source_id(self, source: str) -> str | None:
        parsed = urlsplit(source)
        host = (parsed.hostname or "").casefold()
        if host == "youtu.be":
            return parsed.path.strip("/").split("/")[0] or None
        if self.handles_source_id(source):
            if parsed.path == "/watch":
                return dict(parse_qsl(parsed.query)).get("v")
            match = re.search(r"/(?:shorts|live|embed)/([^/?]+)", parsed.path)
            return match.group(1) if match else None
        return generic_source_id(source)


ADAPTER = YouTubeAdapter(
    name="youtube",
    platform="youtube",
    domains=("youtube.com", "youtu.be"),
)
