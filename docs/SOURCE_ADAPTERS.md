# Source adapters

Source adapters let ClipMind recognize and normalize new platforms without
adding platform branches to acquisition, processing, storage, or the Evidence
Pack writer.

## What an adapter owns

An adapter owns only:

- whether it matches a source string;
- a stable adapter name and platform identifier;
- pre-acquisition canonical source and known source ID (optional hooks);
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

## Optional source identity hooks

Adapters can define `canonicalize_source(source: str) -> str` and
`source_id(source: str) -> str | None`. These run **before acquisition** to
identify reusable complete packs; `normalize_info(source, info)` still handles
metadata **after acquisition**. The compatibility helpers
`clipmind.links.normalize_url` and `source_id_from_url` delegate to these hooks.

Subclass `SourceAdapter` to override either method, or add the methods to an
existing protocol implementation. An installed plugin implementing only the
original required protocol continues to load and match. Each missing hook uses
the existing generic identity defaults independently. A supplied `source_id`
hook returning `None` is authoritative; it does not request fallback parsing.

For a source whose identity lives in a query parameter, an adapter can do:

```python
from urllib.parse import parse_qsl, urlencode, urlsplit

class LessonAdapter(SourceAdapter):
    def source_id(self, source: str) -> str | None:
        return dict(parse_qsl(urlsplit(source).query)).get("lesson")

    def canonicalize_source(self, source: str) -> str:
        lesson = self.source_id(source)
        if lesson is None:
            return super().canonicalize_source(source)
        return "https://video.example/watch?" + urlencode({"lesson": lesson})
```

Identity hooks should be deterministic, local operations without acquisition or
metadata requests. Preserve every parameter needed to identify distinct media
in both the canonical source and the source ID. `JobStore.reusable()` accepts a
match on either one, so returning the same ID for distinct parts can alias them.

The migration deliberately retains historic helper behavior: generic defaults
strip only known tracking keys, sort remaining query pairs, and keep the old
host-independent numeric video/note and BV/av ID parsing. Some legacy identity
host rules are wider than acquisition matching, and bare local paths differ
from explicit `file://` URIs. These quirks do not add acquisition support. See
[the old/new identity table](SOURCE_IDENTITY_MIGRATION.md) for the preserved
behavior and known limitations.

## Optional failure classification hook

Adapters can define `classify_failure(attempt) -> str | None`, called once per
acquisition attempt before the shared rules. `attempt` carries `strategy`,
`label`, and `reason` — `reason` is what the tool actually reported, kept apart
from the label ClipMind gave the rung, because every browser rung is labelled
`"<browser> cookies"` and matching that would read a platform's own access
refusal as a cookie problem.

The hook is optional. An adapter without it, including an already-installed
plugin, keeps the shared classification unchanged. Returning `None` defers to
the shared rules; a hook that raises is logged and skipped rather than breaking
classification.

Return one of the established codes — `private_video`, `cookies_stale`,
`login_required`, `cookies_unavailable`, `link_unavailable`,
`media_fetch_failed`. Other values are logged and ignored: these codes are a
public contract that the REST error events, the SDK, and the web app read.

```python
class LessonAdapter(SourceAdapter):
    def classify_failure(self, attempt) -> str | None:
        if "enrolment required" in attempt.reason.lower():
            return "login_required"
        return None
```

Classification is ranked across every attempt, not taken from the last one: the
rung that explains a failure is often not the rung that ran last. Keep hooks
free of network access — they receive text that has already been collected.

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

### Reusable offline conformance checks

Third-party packages can import the conformance kit directly; it has no test
framework dependency and raises `AssertionError` for a failed contract case:

```python
from clipmind.sources.conformance import (
    IdentityCase,
    NormalizationCase,
    assert_adapter_conformance,
)
from example_clipmind_source.adapter import ADAPTER

source = "https://video.example/watch?lesson=7&p=1"
assert_adapter_conformance(
    ADAPTER,
    matching_sources=[source, "https://cdn.video.example/watch?lesson=8"],
    nonmatching_sources=[
        "https://notvideo.example/watch",
        "https://video.example.evil.invalid/watch",
        "https://video.example@evil.invalid/watch",
        "ftp://video.example/watch",
    ],
    normalization_cases=[NormalizationCase(
        source=source,
        info={
            "id": "lesson-7-part-1", "title": "Synthetic lesson",
            "uploader": "Fixture", "duration": 12.5,
        },
        expected={
            "title": "Synthetic lesson", "uploader": "Fixture",
            "duration": 12.5, "webpage_url": source,
        },
    )],
    distinct_sources=[
        (source, "https://video.example/watch?lesson=7&p=2"),
        (source, "https://video.example/watch?lesson=8&p=1"),
    ],
    identity_cases=[
        IdentityCase(source, source, None),
        IdentityCase(source + "&utm_source=share", source, None),
        IdentityCase(source.replace("p=1", "p=2"), source.replace("p=1", "p=2"), None),
    ],
)
```

Supply cases appropriate to your adapter. At least one matching source,
nonmatching source, and normalization case are required. `expected` is a subset
of normalized field values, so a plugin can declare intentional transformations.
The kit always checks the real required protocol, boolean match results,
platform/adapter tags, preservation of an upstream `id`, a URL when omitted by
upstream, and a separate returned dictionary without changing the input,
including nested metadata. Caller-owned fixtures are copied before invocation.

The optional identity hooks are checked independently when present: repeated
calls must agree, canonicalization must be idempotent, and return types must
match the contract. Existing plugins without either hook can use the kit.
For each explicitly supplied `distinct_sources` pair, both sources must match
and neither their canonical sources nor their nonempty source IDs may collide.
Missing hooks use the same legacy defaults as the registry; a `source_id` hook
returning `None` remains authoritative. An empty distinct-pair list makes no
claim about identity separation.

Supply `IdentityCase` values with independently specified expected canonical
sources and IDs. Determinism alone cannot detect retained tracking keys or a
consistently wrong ID. Tracking-only variants should have identical expected
identities; distinct-media pairs should differ **only** in the identity-bearing
parameter, so incidental tracking differences cannot hide a lost part number.
This example uses the simplest declarative adapter above: its generic identity
parser returns `None` for these query-based URLs until acquisition supplies the
upstream ID. A custom adapter with explicit lesson/part hooks should instead
specify its own composite expected IDs, such as `lesson-7-part-1`.

For privacy cases, pass synthetic sentinels using
`NormalizationCase(..., forbidden_text=("synthetic-token-marker",))`. Put the
marker in the supplied upstream fields, then require your normalizer to remove
it. The kit checks nested string keys/values in dictionaries, lists and tuples,
and does not echo the forbidden value when an assertion fails. Include both
raw headers and exportable fields such as `webpage_url`, title and uploader.
The real writer excludes raw headers, but deliberately preserves those public
fields; its whitelist is not a substitute for sanitizing a credential-bearing
URL. This is a check for the literal sentinels supplied, not a generic secret
detector or proof about encoded values, plugin logs, or arbitrary objects.

These checks can expose preserved identity limitations. For example, the generic
adapter keeps different Bilibili `?p=1` and `?p=7` canonical URLs but returns the
same BV/av ID for both, so that distinct pair fails. The kit does not change
legacy identities or add Bilibili acquisition support. Local-path and other
historic identity quirks described in the migration table also remain in place.

The kit calls adapter methods without registering plugins or acquiring media.
It is not a sandbox: use trusted adapters and synthetic cases, and stub any
external dependencies in your own tests. Passing the supplied cases does not
prove uniqueness for untested URLs, removal of untested secrets, registry precedence,
failure classification, or complete-pack serialization; keep the integration
tests from the test contract above. The kit's own positive and deliberately
broken-adapter tests run with:

```bash
python -m unittest tests.test_source_conformance -v
```

The framework integration side of the same contract is covered by real registry
entry-point dispatch, broken-plugin fallback, safe actionable failure selection,
and the real pack writer, without contacting a platform:

```bash
python -m unittest tests.test_source_conformance tests.test_sources tests.test_source_identity tests.test_fetch -v
```

Keep corresponding integration cases in a third-party plugin project; the
standalone value checker does not install a plugin or mock the host registry.

## When core code is appropriate

Open a design issue before changing core acquisition for a platform. A core
change can be justified when the platform fundamentally cannot yield a local
media file through the shared acquisition boundary, but the proposal must still
return a normal `MediaAsset` and preserve complete-or-refuse, cleanup, privacy,
and Evidence Pack invariants.

Source support means “recognized and tested against an upstream strategy,” not
“can bypass access controls.” Never add DRM circumvention, credential capture,
or automatic scraping beyond what the user explicitly submits and authorizes.
