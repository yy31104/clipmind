from .base import SourceAdapter

ADAPTER = SourceAdapter(
    name="youtube",
    platform="youtube",
    domains=("youtube.com", "youtu.be"),
)
