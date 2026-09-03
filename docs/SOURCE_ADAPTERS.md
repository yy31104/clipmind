# Source adapters

Source adapters let ClipMind recognize and normalize new platforms without
adding platform branches to acquisition, processing, storage, or the Evidence
Pack writer.

## What an adapter owns

An adapter owns only:

- whether it matches a source string;
- a stable adapter name and platform identifier;
- normalization of upstream metadata into ClipMind's platform-neutral fields.

It does **not** own yt-dlp execution, cookies, retry/error policy, temporary file
cleanup, ASR, OCR, visual extraction, preflight, or serialization. Keeping this
boundary small prevents every platform from becoming its own pipeline.

## Built-ins and precedence

The registry order is:

```text
local file
verified specific built-ins (Douyin, YouTube)
installed clipmind.sources entry points
generic HTTP(S) fallback
```

Built-ins therefore cannot be accidentally shadowed by a plugin, while a plugin
still runs before the best-effort generic URL fallback. Only YouTube, Douyin,
and local files are advertised as verified inputs. Entry points are loaded once
per process. A plugin that raises during import or fails protocol validation is
logged and skipped so it cannot disable built-ins.

## Simplest plugin

Use the public `SourceAdapter` value type when domain matching and metadata
tagging are sufficient:

```python
# example_clipmind_source/adapter.py
from clipmind.sources import SourceAdapter

ADAPTER = SourceAdapter(
    name="example-video",
    platform="example",
    domains=("video.example", "exm.pl"),
)
```

Register the object in your package's `pyproject.toml`:

```toml
[project.entry-points."clipmind.sources"]
example = "example_clipmind_source.adapter:ADAPTER"
```

After installation:

```bash
clipmind doctor --json
```

The adapter should appear in `sources`, and a matching URL should be attributed
to its `platform` in `source.json`.

## Custom protocol implementation

For custom matching or normalization, export an object with:

```python
class ExampleAdapter:
    name = "example-video"
    platform = "example"
    local = False
    generic = False

    def matches(self, source: str) -> bool:
        ...

    def normalize_info(self, source: str, info: dict) -> dict:
        normalized = dict(info)
        normalized["_clipmind_platform"] = self.platform
        normalized["_clipmind_source_adapter"] = self.name
        normalized.setdefault("webpage_url", source)
        return normalized

ADAPTER = ExampleAdapter()
```

`normalize_info` must return a new or safely copied dictionary. At minimum it
must set `_clipmind_platform` and `_clipmind_source_adapter`, preserve an
upstream `id` when present, and provide `webpage_url` when upstream omitted it.
Do not store cookies, tokens, request headers, or raw credential-bearing
metadata in the returned object: normalized fields can reach `source.json` and
durable job diagnostics.

Platform identifiers are open extension values in the manifest schema. Use a
stable, non-empty, lowercase identifier and never change it after packs have
been published; consumers can use it for cache and routing decisions.

## Test contract

A source adapter contribution should prove:

1. positive and negative URL matching, including subdomains and lookalikes;
2. its specific match wins before the generic fallback;
3. normalized platform, adapter, source ID, URL, title, uploader, and duration;
4. no secrets appear in normalized metadata or surfaced errors;
5. an upstream failure maps to an actionable existing error category;
6. complete pack serialization accepts the platform identifier;
7. all existing offline tests remain green.

Network recordings, cookies, copyrighted video, transcripts, OCR text, or
creator screenshots must not be committed as fixtures. Prefer synthetic
metadata and tiny generated local media.

## When core code is appropriate

Open a design issue before changing core acquisition for a platform. A core
change can be justified when the platform fundamentally cannot yield a local
media file through the shared acquisition boundary, but the proposal must still
return a normal `MediaAsset` and preserve complete-or-refuse, cleanup, privacy,
and Evidence Pack invariants.

Source support means “recognized and tested against an upstream strategy,” not
“can bypass access controls.” Never add DRM circumvention, credential capture,
or automatic scraping beyond what the user explicitly submits and authorizes.
