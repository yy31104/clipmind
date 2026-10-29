"""Read the exact note's server-rendered page state without executing scripts.

Douyin's current desktop note page embeds its original image entry in a Pace
React payload rather than issuing an ``aweme/detail`` request. Only the named
Pace transport and its explicit ``aweme.detail`` props contract are consumed.
Arbitrary page images, JSON blobs and recommendation trees are never searched.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .douyin import numeric_source_id
from .douyin_browser import ResolveFailed, ResolvedMedia, _media_info

MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_PACE_CHUNKS = 256
_PREFIX = "self.__pace_f.push("
_RECORD = re.compile(r"^[0-9a-fA-F]+:(.*)$", re.DOTALL)
_RECORD_START = re.compile(r"^\s*[0-9a-fA-F]+:")
_PARTIAL_ID = re.compile(r"^\s*[0-9a-fA-F]*$")


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attributes = dict(attrs)
            self.active = "src" not in attributes and attributes.get("type") in (None, "text/javascript")
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            if self.active:
                self.scripts.append("".join(self.parts))
            self.active = False
            self.parts = []


def _props(value: object):
    # Traverse React children/element arrays, not arbitrary metadata dicts. A
    # recommendation inside a detail's relatedVideo/comment trees cannot turn
    # itself into the requested item through this traversal.
    stack = [(value, 0)]
    visited = 0
    while stack:
        node, depth = stack.pop()
        visited += 1
        if visited > 20_000 or depth > 40:
            raise ResolveFailed("the embedded page state exceeds its structural limit")
        if not isinstance(node, list):
            continue
        if (len(node) == 4 and node[0] == "$" and isinstance(node[1], str)
                and node[1].startswith("$") and isinstance(node[3], dict)):
            props = node[3]
            yield props
            children = props.get("children")
            if isinstance(children, list):
                stack.append((children, depth + 1))
        else:
            stack.extend((child, depth + 1) for child in reversed(node) if isinstance(child, list))


def _flight_values(scripts: list[str]):
    """Reassemble only validated Pace data chunks, then parse flight rows.

    Script boundaries are transport boundaries, not JSON-record boundaries.
    One row may span several push calls, or one push may contain several rows.
    No bytes from other JavaScript, script sources or transport channels enter
    this buffer. All script bodies remain covered by the page byte limit.
    """
    pending = ""
    chunk_count = 0
    for script in scripts:
        script = script.strip()
        if not script.startswith(_PREFIX):
            continue
        try:
            envelope, end = json.JSONDecoder().raw_decode(script, len(_PREFIX))
            if script[end:].strip() not in {")", ");"}:
                continue
            if (not isinstance(envelope, list) or len(envelope) != 2
                    or type(envelope[0]) is not int or envelope[0] != 1
                    or not isinstance(envelope[1], str)):
                continue
        except (ValueError, TypeError, RecursionError):
            continue
        chunk_count += 1
        if chunk_count > MAX_PACE_CHUNKS:
            raise ResolveFailed("the embedded page state exceeds its transport limit")
        pending += envelope[1]
        rows = pending.split("\n")
        pending = rows.pop()
        for row in rows:
            record = _RECORD.fullmatch(row.strip())
            if record:
                try:
                    yield json.loads(record[1])
                except (ValueError, TypeError, RecursionError):
                    pass
        # The site also puts a percent-encoded app-configuration blob on this
        # channel. It has no flight-row prefix and is not part of a pending
        # row; retaining it would swallow the next genuine row's prefix.
        if pending and not _RECORD_START.match(pending) and not _PARTIAL_ID.fullmatch(pending):
            pending = ""
    record = _RECORD.fullmatch(pending.strip())
    if record:
        try:
            yield json.loads(record[1])
        except (ValueError, TypeError, RecursionError):
            pass


def _normalized_detail(props: dict, expected: str) -> dict | None:
    if str(props.get("awemeId") or "") != expected:
        return None
    aweme = props.get("aweme")
    if not isinstance(aweme, dict):
        return None
    detail = aweme.get("detail")
    if not isinstance(detail, dict):
        return None
    if str(detail.get("awemeId") or "") != expected:
        raise ResolveFailed("the embedded page described a different item")
    if props.get("statusCode") != 0 or aweme.get("statusCode") != 0:
        raise ResolveFailed("the embedded page did not provide an accessible item")
    if str(detail.get("awemeType") or "") not in {"68", "150"}:
        return None
    rows = detail.get("images")
    if not isinstance(rows, list):
        raise ResolveFailed("the embedded image post carried no original pages")
    images = []
    for row in rows:
        if not isinstance(row, dict):
            raise ResolveFailed("the embedded image post contains malformed pages")
        image = {"url_list": row.get("urlList"), "clip_type": row.get("clipType"),
                 "live_photo_type": row.get("livePhotoType")}
        video = row.get("video")
        if video not in (None, "", "$undefined") and video != {}:
            if not isinstance(video, dict):
                raise ResolveFailed("the embedded Live Photo contains malformed motion metadata")
            addresses = video.get("playAddr")
            if (not isinstance(addresses, list) or not addresses or len(addresses) > 16
                    or not all(isinstance(address, dict) and isinstance(address.get("src"), str)
                               for address in addresses)):
                raise ResolveFailed("the embedded Live Photo carried no supported motion address")
            image["video"] = {
                "duration": video.get("duration"),
                "play_addr": {"url_list": [address["src"] for address in addresses],
                              "data_size": video.get("dataSize")},
            }
        images.append(image)
    author = detail.get("authorInfo")
    music = detail.get("music")
    return {
        "aweme_id": expected,
        "aweme_type": detail["awemeType"],
        "desc": str(detail.get("desc") or ""),
        "author": {"nickname": author.get("nickname")} if isinstance(author, dict) else {},
        "images": images,
        "music": {"play_url": music.get("playUrl")} if isinstance(music, dict) else {},
    }


def embedded_media_info(html: str, page_url: str, expected: str) -> ResolvedMedia:
    """Accept only one exact-ID image entry on its official individual page."""
    parsed = urlsplit(page_url)
    if (parsed.scheme != "https" or parsed.hostname not in {"www.douyin.com", "douyin.com"}
            or parsed.username is not None or parsed.password is not None
            or numeric_source_id(page_url) != expected):
        raise ResolveFailed("the embedded state is not on the requested Douyin page")
    if (not isinstance(html, str) or len(html.encode("utf-8")) > MAX_PAGE_BYTES
            or not expected.isascii() or not expected.isdecimal()):
        raise ResolveFailed("the embedded page state exceeds its metadata limit")
    parser = _Scripts()
    parser.feed(html)
    matches = []
    for value in _flight_values(parser.scripts):
        for props in _props(value):
            detail = _normalized_detail(props, expected)
            if detail is not None:
                matches.append(detail)
    if len(matches) != 1:
        raise ResolveFailed("the embedded page did not uniquely identify the requested image post")
    resolved = _media_info({"aweme_detail": matches[0]}, page_url, expected)
    return ResolvedMedia(
        urls=resolved.urls, info={**resolved.info, "_clipmind_metadata_source": "embedded page state"},
        size_bytes=resolved.size_bytes, image_urls=resolved.image_urls,
        image_motion_urls=resolved.image_motion_urls,
    )
