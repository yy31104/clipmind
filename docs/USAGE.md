# Using ClipMind

From a source checkout, first activate the environment created by the root [README](../README.md):

```bash
source .venv/bin/activate
```

An installed tool already exposes the commands on `PATH`.


### Web app

Paste one or more links, or drop local media. Inbox shows live jobs, Library
searches complete packs. Each search hit opens its video at that moment, with
the screenshot on screen, its OCR text and the nearby speech side by side;
clicking any screenshot shows the same context. The **Frames** tab starts with a
compact preview and can reveal every retained canonical frame. Over-budget work is refused with an estimate; only an
explicit **Process anyway** starts the full untruncated job.

To tidy the list, expand unfinished tasks or open Library, select individual
entries (including old versions) or select all, then choose **Remove selected** and confirm.
Only terminal jobs can be removed. Files move to the library's `.trash`, remain
recoverable and still occupy disk space; original media and exports are untouched.
See [recovery and privacy details](PRIVACY.md#removing-tasks-and-library-versions).

If an older pack kept its screenshots but OCR was unavailable, repair it locally
without downloading or transcribing the source again:

```bash
.venv/bin/python scripts/rebuild_preview.py --refresh-ocr out/PACK_ID
```

The command refuses to replace the pack when OCR still fails on every frame.
After a successful repair, restart a running ClipMind service so its in-memory
job snapshot reloads the repaired result.

### CLI

```bash
clipmind analyze "https://youtu.be/..."
clipmind analyze --force "/path/to/local-video.mp4"
clipmind list
clipmind search "retrieval augmented generation"
clipmind transcript PACK_ID
clipmind timeline PACK_ID --json
clipmind export PACK_ID --output evidence.zip
clipmind export PACK_ID --compact --output reading.zip
clipmind export PACK_ID --text-only --output text.zip
clipmind doctor
```

`--force` means “process the complete source despite the estimate.” It never
means truncate, lower resolution, or manufacture a partial pack.

### Python SDK

```python
from clipmind.sdk import ClipMind, PackLibrary

packs = ClipMind().analyze_sync("https://youtu.be/...")
pack = packs[0]
print(pack.id, pack.summary())

hits = PackLibrary().search("vector database")
frame_path, state = pack.frame(timestamp=143.0)
```

### REST API

Start `clipmind serve`, then use the canonical read API:

```text
GET /api/packs
GET /api/packs/{pack_id}
GET /api/packs/{pack_id}/transcript
GET /api/packs/{pack_id}/timeline
GET /api/packs/{pack_id}/ocr
GET /api/packs/{pack_id}/search?q=...
GET /api/packs/{pack_id}/frame?timestamp=143
```

Extraction and browser uploads use `POST /api/jobs` and `POST /api/uploads`.
The local server binds to loopback by default and has no authentication; do not
expose it to a LAN or the public internet.

### MCP for agents

```bash
clipmind-mcp
# or: clipmind mcp
```

The stdio server exposes:

```text
clipmind.extract_video
clipmind.get_transcript
clipmind.get_visual_timeline
clipmind.search_evidence
clipmind.get_frame
clipmind.export_pack
```

It also exposes complete evidence documents as read-only
`clipmind://packs/<pack-id>/evidence` resources. See [MCP integration](MCP.md)
for configuration and tool contracts.


## Docker


```bash
docker build -t clipmind .
docker run --rm -p 127.0.0.1:8420:8420 -v "$PWD/clipmind-data:/data" clipmind
```

The image uses faster-whisper and Tesseract. It does not bundle browser cookies,
GPU drivers, or model weights.

