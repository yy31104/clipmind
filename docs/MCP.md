# MCP integration

ClipMind includes a dependency-free Model Context Protocol server over standard
input/output. It lets local agents extract new media and read complete Evidence
Packs without scraping the web UI or parsing private implementation files.

## Start the server

After installing ClipMind:

```bash
clipmind-mcp
```

The equivalent command is:

```bash
clipmind mcp
```

The process uses newline-delimited JSON-RPC on stdio. Do not wrap it with a
program that writes logs or banners to stdout; protocol output must remain clean.

## Example client configuration

Clients use different configuration locations, but the process declaration is
the same shape:

```json
{
  "mcpServers": {
    "clipmind": {
      "command": "clipmind-mcp",
      "args": [],
      "env": {
        "CLIPMIND_OUT": "/absolute/path/to/your/clipmind-library"
      }
    }
  }
}
```

If the executable is not on the client's `PATH`, provide its absolute path.
Keep the library local and readable by only the intended user/agent.

## Tools

### `clipmind.extract_video`

Input: `source` plus optional `reprocess` and `force`. The source may be a
verified YouTube/Douyin URL, unedited share text containing supported URLs, a
source recognized by an installed adapter, or an explicit local path. The call
waits for durable terminal state and returns complete pack IDs. A cost refusal
remains an error unless `force` is explicitly true; forcing never truncates the
pack.

### `clipmind.get_transcript`

Returns timestamped transcript segments, including word timing and speaker
labels only when the configured local providers supplied them.

### `clipmind.get_visual_timeline`

Returns canonical state timing, OCR/transcript references, preview membership,
stability, scene/build/scroll metadata, and transcript novelty. Optional
`start`/`end` bounds limit the returned time range without mutating the pack.

### `clipmind.search_evidence`

Searches title, transcript, and OCR across the local library or within a
specified pack. Search is lexical and the SQLite index is a rebuildable cache;
complete pack files remain authoritative.

### `clipmind.get_frame`

Looks up a canonical or preview image by visual-state ID or timestamp. The MCP
response includes structured state metadata and the image bytes. Paths are
validated to remain inside the selected complete pack.

### `clipmind.export_pack`

Writes a deterministic canonical ZIP to the optional destination and returns
its local path. The destination is an external write authorized by the caller.

## Resources

`resources/list` exposes one read-only Markdown resource per complete pack:

```text
clipmind://packs/<pack-id>/evidence
```

Reading it returns `evidence.md`. Partial directories and unsupported schema
versions never become resources.

## Agent workflow

A robust workflow is:

1. call `extract_video` and keep the returned pack ID;
2. inspect `get_visual_timeline` and `get_transcript` before reasoning;
3. fetch exact frames when a visual claim matters;
4. cite timestamps and record IDs in downstream work;
5. export only when a durable handoff is required.

Example user task:

> Extract this product walkthrough, compare its claims with my repository, and
> show the exact transcript timestamps and UI frames behind every mismatch.

ClipMind supplies evidence. The agent remains responsible for interpretation and
must not describe novelty, scene labels, or OCR output as verified semantic
importance.

## Trust boundary

The MCP process can read complete packs under `CLIPMIND_OUT`, create new jobs,
download submitted URLs, copy submitted local files, and export a ZIP when asked.
`get_frame` returns image bytes that can contain sensitive source material. Only
attach the server to agents you trust with that library and review
[Privacy](PRIVACY.md).
