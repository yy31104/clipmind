"""Anonymous, exact-ID Douyin acquisition from its public feed response."""
from __future__ import annotations
import asyncio
import json
import urllib.request
from urllib.parse import urlsplit
from .douyin import numeric_source_id
from .douyin_browser import ResolvedMedia, ResolveFailed, _media_info

PUBLIC_FEED = "https://api5-normal-c-hl.amemv.com/aweme/v1/feed/"
MAX_METADATA_BYTES = 2 * 1024 * 1024

class MetadataUnavailable(ResolveFailed):
    """A safe, structured metadata failure; never includes response contents."""

    def __init__(self, reason: str, *, entry_count: int | None = None,
                 exact_id_matches: int | None = None):
        messages = {
            "entries_missing": "the public feed returned no video entries",
            "target_missing": "the public feed did not include the requested video",
            "target_ambiguous": "the public feed did not uniquely identify the requested video",
        }
        super().__init__(messages[reason])
        self.details = {"reason": reason}
        if entry_count is not None:
            self.details["entry_count"] = entry_count
        if exact_id_matches is not None:
            self.details["exact_id_matches"] = exact_id_matches

class _ShareRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {
            "v.douyin.com", "www.douyin.com", "douyin.com",
            "www.iesdouyin.com", "iesdouyin.com",
        }:
            raise ResolveFailed("the share link redirected outside Douyin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def _source_id(source: str) -> str:
    known = numeric_source_id(source)
    if known:
        return known
    parsed = urlsplit(source)
    if parsed.scheme != "https" or parsed.hostname != "v.douyin.com":
        raise ResolveFailed("the public feed needs an individual video id")
    opener = urllib.request.build_opener(_ShareRedirects())
    with opener.open(urllib.request.Request(source, headers={"User-Agent":"Mozilla/5.0"}),timeout=15) as response:
        found = numeric_source_id(response.url)
    if not found:
        raise ResolveFailed("the share link did not resolve to an individual video id")
    return found

def media_info(payload: dict, source: str, expected: str) -> ResolvedMedia:
    entries = payload.get("aweme_list") if isinstance(payload,dict) else None
    if not isinstance(entries,list):
        raise MetadataUnavailable("entries_missing")
    matching = [entry for entry in entries if isinstance(entry,dict) and str(entry.get("aweme_id") or "") == expected]
    if len(matching) != 1:
        # The feed also includes recommendations. Never use the first entry.
        raise MetadataUnavailable(
            "target_missing" if not matching else "target_ambiguous",
            entry_count=len(entries), exact_id_matches=len(matching),
        )
    resolved = _media_info({"aweme_detail":matching[0]},source,expected)
    suffixes = ("douyinvod.com","douyin.com","amemv.com","snssdk.com","bytecdn.cn","byteimg.com","bytedance.com","zjcdn.com")
    for url in resolved.urls:
        parsed=urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or not any(
            parsed.hostname == suffix or (parsed.hostname or "").endswith("."+suffix)
            for suffix in suffixes
        ):
            raise ResolveFailed("the public feed returned an untrusted media address")
    return ResolvedMedia(resolved.urls,{**resolved.info,"_clipmind_strategy":"public feed"},
                         resolved.size_bytes, image_urls=resolved.image_urls,
                         image_motion_urls=resolved.image_motion_urls)

def _resolve(source: str) -> ResolvedMedia:
    try:
        expected = _source_id(source)
        request=urllib.request.Request(
            PUBLIC_FEED + "?aweme_id=" + expected + "&aid=1128",
            headers={"User-Agent":"Mozilla/5.0"},
        )
        with urllib.request.urlopen(request,timeout=20) as response:
            data=response.read(MAX_METADATA_BYTES+1)
        if len(data)>MAX_METADATA_BYTES:
            raise ResolveFailed("the public feed exceeded the metadata limit")
        return media_info(json.loads(data),source,expected)
    except ResolveFailed:
        raise
    except Exception as exc:
        # urllib/JSON diagnostics can carry URLs; keep them out of user logs.
        raise ResolveFailed("the public feed could not be read") from exc

async def resolve(source: str) -> ResolvedMedia:
    return await asyncio.to_thread(_resolve,source)
