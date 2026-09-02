# Privacy and local data

ClipMind's canonical extraction path runs on the Mac and requires no paid API
key. The web server binds to `127.0.0.1`; it has no authentication and should not
be exposed to a LAN or the public internet.

## Network access

The application contacts Douyin through yt-dlp to resolve and download the
submitted media. MLX Whisper may contact its model host on first use to download
the configured model; later runs use the local cache.

No transcript, image, or OCR text is sent to any model provider. Extraction is
local end to end, and there is no configuration that changes that.

## Browser cookies

The default acquisition order is `chrome,-`: yt-dlp first asks Chrome for its
cookie database, then tries without cookies. `--cookies-from-browser chrome`
gives yt-dlp access to the browser cookie store, not only Douyin cookies. It can
therefore trigger a macOS Keychain prompt and has a broader read boundary than
the final network request.

To avoid browser-cookie access, export only the required Douyin cookies to a
Netscape-format file and configure:

```dotenv
CLIPMIND_COOKIE_SOURCES=-
CLIPMIND_COOKIE_FILE=/absolute/path/to/douyin-cookies.txt
```

Protect that file like a password. It is never included in an Evidence Pack.
Safari is intentionally not a default fallback because its cookie container is
normally blocked by macOS privacy controls; ClipMind does not ask for Full Disk
Access to work around that boundary.

## Files retained

Successful jobs retain the Evidence Pack and legacy compatibility files under
`out/<job-id>/`. Source video, extracted audio, and sampling directories are
deleted unless `CLIPMIND_KEEP_VIDEO=1` is set. Failed/interrupted jobs retain
their state record and any already-published final artifacts but clean temporary
pipeline files.

Evidence Packs can contain faces, voices, usernames, source URLs, transcript
text, and on-screen personal information from the source video. Review a pack
before sharing its ZIP or committing it to another repository. The checked-in
evaluation reports contain aggregate metrics, not downloaded media or frames.

## Knowledge-base delivery

`CLIPMIND_KB_INBOX` authorizes writes only to that configured Inbox. Delivery
copies a complete pack into a private temporary directory and publishes its
manifest last. ClipMind does not scan, interpret, edit, or delete the rest of
the knowledge base.
