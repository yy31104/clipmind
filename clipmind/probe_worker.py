"""Isolated metadata parser with a single, bounded HTTP transport.

This is an internal Python worker, not a downloader. No yt-dlp networking
backend is used: some decompress a complete response before returning it.
Budgets count HTTP response body bytes read, not TCP/TLS/header overhead.
"""
from __future__ import annotations

import io
import http.client
import json
import os
import string
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit


class ProbeRefused(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class NetworkBudget:
    byte_limit: int
    request_limit: int
    bytes_read: int = 0
    requests: int = 0
    refusal: str | None = None

    def refuse(self, code: str = "probe_budget_exceeded") -> None:
        self.refusal = code
        raise ProbeRefused(code)

    def request(self) -> None:
        if self.refusal:
            raise ProbeRefused(self.refusal)
        if self.requests >= self.request_limit:
            self.refuse()
        self.requests += 1


class LimitedBody(io.RawIOBase):
    """All IO entry points consume the same budget, including iteration."""

    def __init__(self, response, budget: NetworkBudget) -> None:
        self.response = response
        self.budget = budget

    def readable(self):
        return True

    def read(self, size=-1):
        if size == 0:
            return b""
        remaining = self.budget.byte_limit - self.budget.bytes_read
        if remaining <= 0 or self.budget.refusal:
            self.close()
            self.budget.refuse(self.budget.refusal or "probe_budget_exceeded")
        amount = remaining if size is None or size < 0 else min(size, remaining)
        try:
            body = self.response.read(amount)
        except (OSError, http.client.HTTPException):
            # A partial read may have consumed bytes before failing. Do not
            # reset that cost by letting another cookie rung retry it.
            self.close()
            self.budget.refuse("probe_invalid_response")
        self.budget.bytes_read += len(body)
        # At the limit we cannot distinguish EOF from a longer body without
        # another read. Refuse conservatively; never silently truncate metadata.
        if self.budget.bytes_read >= self.budget.byte_limit:
            self.close()
            self.budget.refuse()
        return body

    def readinto(self, buffer):
        value = self.read(len(buffer))
        buffer[:len(value)] = value
        return len(value)

    def close(self):
        self.response.close()
        super().close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BoundedHTTP:
    def __init__(self, budget: NetworkBudget, timeout: float) -> None:
        self.budget = budget
        self.timeout = timeout
        self.responses = []
        self.connecting = False

    def close(self):
        for response in self.responses:
            response.close()

    def open(self, url, *, headers=None, data=None, method=None, cookiejar=None):
        headers = dict(headers or {})
        while True:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                self.budget.refuse("probe_transport_unsupported")
            if parsed.username is not None or parsed.password is not None:
                self.budget.refuse("probe_transport_unsupported")
            self.budget.request()
            # No proxy tunnel, auto redirects, or automatic decompression.
            # Redirect bodies are closed unread and every hop is counted.
            handlers = [urllib.request.ProxyHandler({}), NoRedirect()]
            if cookiejar is not None:
                handlers.append(urllib.request.HTTPCookieProcessor(cookiejar))
            opener = urllib.request.build_opener(*handlers)
            safe_headers = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}
            safe_headers["Accept-Encoding"] = "identity"
            request = urllib.request.Request(url, data=data, headers=safe_headers, method=method)
            try:
                self.connecting = True
                try:
                    response = opener.open(request, timeout=self.timeout)
                except urllib.error.HTTPError as error:
                    response = error
            finally:
                self.connecting = False
            if response.code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location") or response.headers.get("URI")
                code = response.code
                response.close()
                if not location:
                    self.budget.refuse("probe_transport_unsupported")
                # Preserve urllib's redirect escaping without its unbounded
                # fp.read(). HTTP header bytes were decoded as Latin-1.
                target = urljoin(url, quote(location, encoding='iso-8859-1', safe=string.punctuation))
                other = urlsplit(target)
                origin = lambda p: (p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80))
                if origin(parsed) != origin(other):
                    headers = {k: v for k, v in headers.items()
                               if k.lower() not in {"authorization", "cookie", "proxy-authorization"}}
                if code == 303 and method != "HEAD" or code in {301, 302} and method == "POST":
                    data, method = None, "GET"
                    headers = {k: v for k, v in headers.items()
                               if k.lower() not in {"content-length", "content-type"}}
                url = target
                continue
            encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                response.close()
                self.budget.refuse("probe_transport_unsupported")
            body = LimitedBody(response, self.budget)
            self.responses.append(body)
            return response, body


def run(byte_limit: int, request_limit: int, args: list[str]) -> dict:
    # Set before importing yt-dlp; no third-party extractor or JS subprocess
    # may route around our transport. This affects only this isolated worker.
    os.environ["YTDLP_NO_PLUGINS"] = "1"
    # Legacy extractors may create temporary files before discovering that an
    # external runtime is unavailable. Keep those files under parent cleanup.
    tempfile.tempdir = os.getcwd()
    import yt_dlp
    from yt_dlp.networking import Request, Response
    from yt_dlp.networking.exceptions import HTTPError, TransportError

    budget = NetworkBudget(byte_limit, request_limit)
    transport = None

    class MetadataDL(yt_dlp.YoutubeDL):
        def save_cookies(self):
            # Cookie files are inputs, not an output of probing.
            pass

        def urlopen(self, request):
            if isinstance(request, str):
                request = Request(request)
            elif isinstance(request, urllib.request.Request):
                request = Request(request.full_url, data=request.data,
                                  headers=dict(request.header_items()), method=request.get_method())
            extensions = request.extensions
            if extensions.get("impersonate") or request.proxies:
                budget.refuse("probe_transport_unsupported")
            headers = dict(self.params.get("http_headers") or {})
            headers.update(request.headers)
            try:
                raw, body = transport.open(
                    request.url, headers=headers, data=request.data, method=request.method,
                    cookiejar=extensions.get("cookiejar") or self.cookiejar,
                )
            except (OSError, urllib.error.URLError) as exc:
                raise TransportError(cause=exc) from exc
            wrapped = Response(body, raw.url, raw.headers, status=raw.code, reason=raw.reason)
            if raw.code >= 400:
                raise HTTPError(wrapped)
            return wrapped

    try:
        parsed = yt_dlp.parse_options(args)
        opts = parsed.ydl_opts
        opts.update(cachedir=False, quiet=True, no_warnings=True, skip_download=True,
                    simulate=True, js_runtimes={}, remote_components=set(),
                    forceprint={}, print_to_file={},
                    # Avoid default merger capability detection (ffmpeg
                    # subprocess). Only metadata is returned, never a format
                    # downloaded or tested for playability.
                    format='best/bestvideo/bestaudio', check_formats=False)
        transport = BoundedHTTP(budget, opts.get("socket_timeout") or 8.0)
        with MetadataDL(opts) as downloader:
            # Cookie bootstrap may need the OS credential helper. Do it before
            # parsing any untrusted page; extraction itself may not spawn a
            # legacy PhantomJS helper or open an alternate networking backend.
            downloader.cookiejar

            def guard(event, args):
                if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"}:
                    budget.refuse("probe_transport_unsupported")
                if event in {"socket.connect", "socket.getaddrinfo"} and not transport.connecting:
                    budget.refuse("probe_transport_unsupported")

            sys.addaudithook(guard)
            info = downloader.extract_info(parsed.urls[0], download=False)
        if budget.refusal:
            raise ProbeRefused(budget.refusal)
        metadata = {key: info.get(key) for key in ("id", "title", "duration", "webpage_url")} if isinstance(info, dict) else None
        result = {"metadata": metadata}
    except Exception as exc:
        # Raw diagnostics stay in the child envelope and go only to local logs.
        result = {"metadata": None, "error": str(exc), "failure_code": budget.refusal}
    finally:
        if transport is not None:
            transport.close()
    result.update(network_bytes=budget.bytes_read, network_requests=budget.requests)
    return result


if __name__ == "__main__":
    print(json.dumps(run(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:])))
