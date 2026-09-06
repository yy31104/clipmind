# Privacy and local data

ClipMind's canonical analysis path is local-first and requires no paid model API
key. Speech recognition, OCR, visual analysis, search indexing, timeline fusion,
and Evidence Pack generation run on the user's machine.

The web server binds to `127.0.0.1` by default. It has no authentication and must
not be exposed to a LAN, reverse proxy, shared host, or the public internet
without a separate security layer.

## Network access

URL ingestion uses yt-dlp to resolve and download only the submitted source.
What upstream hosts are contacted depends on the platform, redirects, and media
delivery network.

Probing a URL contacts the same hosts but downloads no media: it resolves
metadata under an explicit wall clock (`CLIPMIND_PROBE_TIMEOUT`, default 20s)
and a per-socket timeout, ignores your yt-dlp config so no configured option can
make it write files, runs in a sandbox directory that is removed either way,
creates no job directory, and never falls back to acquisition. Identifying a
direct media link still reads the head of the file, since that is what says what
the file is; measured against a 32 MB fixture a probe transferred under 1 MB
where acquisition transferred all of it.

**What is not bounded:** the size of a metadata response. yt-dlp exposes no cap
on how much of a page it reads while extracting, so probing a URL that serves a
very large HTML page reads that page in full, bounded only by the wall clock and
socket timeout. ClipMind bounds what it can enforce -- elapsed time, the number
of attempts, and the child process's own output -- and does not claim a network
byte budget it cannot impose.

Local model providers may contact their model registry on first use:

- MLX Whisper and faster-whisper can download configured ASR weights;
- optional pyannote diarization can contact Hugging Face and requires `HF_TOKEN`;
- later runs normally use provider-managed local caches.

ClipMind does not send transcript, OCR, images, or Evidence Packs to a paid or
hosted inference API. There is no semantic-summary provider in the canonical
pipeline. Installing a third-party source/provider plugin extends the trust
boundary to that package's code; review plugins before installing them.

## Browser cookies

The default URL acquisition order is `chrome,-`: yt-dlp first tries the local
Chrome cookie store and then an unauthenticated request. Browser-cookie access is
broader than the final source domain—yt-dlp reads the configured browser store,
not only cookies for YouTube, Douyin, or another submitted site. On macOS this
can trigger a Keychain prompt.

To disable browser-cookie access, configure only the unauthenticated path:

```dotenv
CLIPMIND_COOKIE_SOURCES=-
```

For an account-gated source, a narrower alternative is a Netscape-format cookie
file containing only the required cookies:

```dotenv
CLIPMIND_COOKIE_SOURCES=-
CLIPMIND_COOKIE_FILE=/absolute/path/to/cookies.txt
```

Protect that file like a password. It is passed to yt-dlp and is never copied
into an Evidence Pack. Safari is intentionally not a default fallback because
its protected cookie container would otherwise encourage misleading Full Disk
Access guidance.

## Local uploads and files

The browser upload endpoint streams the selected file into a randomized private
path inside the ClipMind library and enforces the configured size limit. Direct
CLI/SDK local-file ingestion copies the original into the job directory before
processing. ClipMind never edits the original. The durable library `job.json`
keeps the original path so an explicit local retry can work; exported ZIPs and
Inbox copies replace it with `local:///filename` and remove internal job options.

Acquisition writes into an `acquisition/` directory the job owns. Ownership is
recorded on disk before the first byte is downloaded, so the media is removable
whatever the strategy chose to name it, and by a later process that never held
it. Completion, failure, cancellation and restart recovery all delete that
directory, along with extracted audio and sampling frames, unless
`CLIPMIND_KEEP_VIDEO=1` is configured -- which keeps the acquired media inside
`acquisition/` rather than at the job root. Interrupted jobs retain their
durable state plus any already-published final artifacts.

Ownership is narrow by construction. Being inside the job directory is not what
makes something deletable -- the Evidence Pack lives there too -- so cleanup may
only remove the `acquisition/` directory and siblings carrying the same prefix.
The job directory itself, `source.json`, and every other final artifact are
refused rather than recorded, and a symlinked `acquisition/` is refused instead
of followed.

A local file you supply is never owned. ClipMind copies it into the job's
`acquisition/` directory and only ever deletes that copy. Anything that could
not be removed is recorded with the artifacts themselves, and every application
start retries the removal until it succeeds -- a job reaching a terminal state
is not treated as evidence that its temporary media is gone. Media a successful
job kept through `CLIPMIND_KEEP_VIDEO=1` is never recorded that way, so it is
never removed by a retry.

## Retained data

Successful jobs retain the Evidence Pack under the configured library root.
Installed builds use a per-user data directory; a source checkout uses `./out`
unless `CLIPMIND_OUT` overrides it.

Evidence Packs can contain:

- faces, voices, and speaker-turn labels;
- usernames, titles, source URLs, and upstream metadata;
- complete speech transcripts and word timing;
- screenshots, OCR text, and layout revealing personal or confidential data;
- filenames and local-file provenance.

Review a pack before sharing its ZIP, sending it to an agent, or committing it
to another repository. The checked-in evaluation reports contain aggregate
metrics and source identifiers/URLs used for reproducibility, not downloaded
video, frames, transcripts, or OCR text.

## Local search index

`.evidence-index.sqlite3` stores derived searchable copies of title, transcript,
and OCR text inside the library. It is not canonical and can be rebuilt from
complete packs, but it carries the same local sensitivity while present. The
index is never uploaded by ClipMind.

## Agent interfaces

The REST API, Python SDK, CLI, and MCP server can expose complete local evidence
to the caller. MCP uses stdio and returns image bytes for frame requests. An
agent with access to these interfaces can read the requested pack content; its
own provider/privacy policy is outside ClipMind's control.

## Knowledge-base delivery

`CLIPMIND_KB_INBOX` authorizes writes only to that configured Inbox. Delivery
copies a complete canonical pack through a private temporary directory and
publishes `manifest.json` last. ClipMind does not scan, interpret, edit, or
delete the rest of the knowledge base.

## Deletion and disclosure

ClipMind currently has no UI delete action and no telemetry. To remove data,
stop the app and delete the desired pack directory and derived SQLite index from
the local library. Security issues involving credentials, path boundaries, or
unexpected disclosure should be reported privately as described in
[`SECURITY.md`](../SECURITY.md), not posted with sensitive artifacts in a public
issue.
