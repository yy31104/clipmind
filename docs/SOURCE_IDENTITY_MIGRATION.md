# Source identity ownership migration

Baseline: `01e2719` (post-PR-20 main). This change relocates identity rules;
it does not establish new normalization semantics or add a verified platform.

## Flow and ownership

Before: callers -> `links.normalize_url / source_id_from_url` -> inline
platform rules. Acquisition independently selected an adapter.

After: callers -> the same compatibility wrappers -> registry identity
delegation -> the selected adapter's `canonicalize_source / source_id`.
YouTube owns its URL and ID rules; Douyin owns its URL and numeric path ID
rules. The base adapter supplies generic/legacy defaults. Post-acquisition
`normalize_info` and acquisition `matches` are unchanged.

The identity compatibility shim also preserves historic rules on hosts or
schemes that acquisition would not treat as that platform. In particular, old
wide suffix matching does not expand the acquisition registry. The old
host-independent BV/av fallback remains without registering Bilibili.

Legacy plugins need no new methods: each missing hook falls back independently.
New plugins can override either hook without editing `links.py`. A provided ID
hook returning `None` is respected, not replaced by fallback parsing.

## Executed old/new truth table

Each row below was evaluated against the actual functions loaded from
`git show main:clipmind/links.py` at the baseline above and the adapter-owned
implementation. The same expected values were tested **before** production
code was changed. All URLs are synthetic. `None` means no pre-acquisition ID.

| Input source | Old canonical | New canonical | Old source ID | New source ID |
| --- | --- | --- | --- | --- |
| `https://youtu.be/AbC123?si=share&t=12` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `AbC123` | `AbC123` |
| `http://WWW.youtube.com/watch?v=AbC123&feature=share#t=3` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `AbC123` | `AbC123` |
| `https://m.youtube.com/watch?v=AbC123&list=playlist` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `AbC123` | `AbC123` |
| `https://music.youtube.com/watch?v=AbC123&index=2` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `AbC123` | `AbC123` |
| `https://youtube.com/shorts/AbC123?si=share` | `https://youtube.com/shorts/AbC123` | `https://youtube.com/shorts/AbC123` | `AbC123` | `AbC123` |
| `https://youtube.com/live/AbC123/?feature=share` | `https://youtube.com/live/AbC123` | `https://youtube.com/live/AbC123` | `AbC123` | `AbC123` |
| `https://youtube.com/embed/AbC123?start=4` | `https://youtube.com/embed/AbC123` | `https://youtube.com/embed/AbC123` | `AbC123` | `AbC123` |
| `https://youtube.com/watch/?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `None` | `None` |
| `https://youtube.com/watch?v=A&v=B` | `https://youtube.com/watch?v=B` | `https://youtube.com/watch?v=B` | `B` | `B` |
| `https://youtube.com/watch?v=A&v=` | `https://youtube.com/watch` | `https://youtube.com/watch` | `A` | `A` |
| `https://youtube.com/watch?v=&feature=share` | `https://youtube.com/watch` | `https://youtube.com/watch` | `None` | `None` |
| `https://youtube.com/watch?V=AbC123` | `https://youtube.com/watch` | `https://youtube.com/watch` | `None` | `None` |
| `https://youtu.be/?si=share` | `https://youtu.be/?si=share` | `https://youtu.be/?si=share` | `None` | `None` |
| `https://www.youtu.be/AbC123?si=share` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `None` | `None` |
| `https://studio.youtube.com/watch?v=AbC123&x=1` | `https://studio.youtube.com/watch?v=AbC123&x=1` | `https://studio.youtube.com/watch?v=AbC123&x=1` | `AbC123` | `AbC123` |
| `https://foo.youtu.be/note/123?x=1` | `https://foo.youtu.be/note/123?x=1` | `https://foo.youtu.be/note/123?x=1` | `123` | `123` |
| `https://youtube.com/video/123` | `https://youtube.com/video/123` | `https://youtube.com/video/123` | `None` | `None` |
| `http://WWW.douyin.com/video/123/?x=tracking` | `https://douyin.com/video/123` | `https://douyin.com/video/123` | `123` | `123` |
| `https://douyin.com/note/456?modal_id=999&p=7` | `https://douyin.com/note/456` | `https://douyin.com/note/456` | `456` | `456` |
| `https://v.douyin.com/AbC-1/?foo=bar#fragment` | `https://v.douyin.com/AbC-1` | `https://v.douyin.com/AbC-1` | `None` | `None` |
| `https://www.iesdouyin.com/share/video/123/?from=share` | `https://iesdouyin.com/share/video/123` | `https://iesdouyin.com/share/video/123` | `123` | `123` |
| `https://iesdouyin.com/share/note/456?x=1` | `https://iesdouyin.com/share/note/456` | `https://iesdouyin.com/share/note/456` | `456` | `456` |
| `https://foo.iesdouyin.com/share/video/123?x=1` | `https://foo.iesdouyin.com/share/video/123` | `https://foo.iesdouyin.com/share/video/123` | `123` | `123` |
| `https://douyin.com/?modal_id=123` | `https://douyin.com/` | `https://douyin.com/` | `None` | `None` |
| `https://douyin.com/video/123x?x=1` | `https://douyin.com/video/123x` | `https://douyin.com/video/123x` | `None` | `None` |
| `http://WWW.example.org/watch/?utm_source=x&feature=y&SI=z&p=1#part` | `https://example.org/watch?p=1` | `https://example.org/watch?p=1` | `None` | `None` |
| `https://example.org/watch?z=last&p=7&a=first` | `https://example.org/watch?a=first&p=7&z=last` | `https://example.org/watch?a=first&p=7&z=last` | `None` | `None` |
| `https://example.org/watch?p=1` | `https://example.org/watch?p=1` | `https://example.org/watch?p=1` | `None` | `None` |
| `https://example.org/watch?p=7` | `https://example.org/watch?p=7` | `https://example.org/watch?p=7` | `None` | `None` |
| `https://example.org/watch?x=2&blank=&x=1&ref=keep` | `https://example.org/watch?blank=&ref=keep&x=1&x=2` | `https://example.org/watch?blank=&ref=keep&x=1&x=2` | `None` | `None` |
| `https://example.org/watch?q=a%20b&unknown=a%2Fb` | `https://example.org/watch?q=a+b&unknown=a%2Fb` | `https://example.org/watch?q=a+b&unknown=a%2Fb` | `None` | `None` |
| `https://user:synthetic@example.org:8443/demo.mp4?quality=hd` | `https://example.org/demo.mp4?quality=hd` | `https://example.org/demo.mp4?quality=hd` | `None` | `None` |
| `https://example.org/` | `https://example.org/` | `https://example.org/` | `None` | `None` |
| `https://www.bilibili.com/video/BV1abc123?p=7&spm_id_from=share` | `https://bilibili.com/video/BV1abc123?p=7` | `https://bilibili.com/video/BV1abc123?p=7` | `BV1abc123` | `BV1abc123` |
| `https://www.bilibili.com/video/av123?p=1` | `https://bilibili.com/video/av123?p=1` | `https://bilibili.com/video/av123?p=1` | `av123` | `av123` |
| `https://other.example/Bv1ABC/part?x=1` | `https://other.example/Bv1ABC/part?x=1` | `https://other.example/Bv1ABC/part?x=1` | `Bv1ABC` | `Bv1ABC` |
| `https://other.example/AV123/` | `https://other.example/AV123` | `https://other.example/AV123` | `AV123` | `AV123` |
| `https://other.example/video/123?x=1` | `https://other.example/video/123?x=1` | `https://other.example/video/123?x=1` | `123` | `123` |
| `https://other.example/share/note/456` | `https://other.example/share/note/456` | `https://other.example/share/note/456` | `456` | `456` |
| `https://notyoutube.com/watch?v=AbC123&x=1` | `https://notyoutube.com/watch?v=AbC123&x=1` | `https://notyoutube.com/watch?v=AbC123&x=1` | `AbC123` | `AbC123` |
| `https://notdouyin.com/video/123?x=1` | `https://notdouyin.com/video/123` | `https://notdouyin.com/video/123` | `123` | `123` |
| `https://youtube.com.example.org/watch?v=AbC123` | `https://youtube.com.example.org/watch?v=AbC123` | `https://youtube.com.example.org/watch?v=AbC123` | `None` | `None` |
| `ftp://youtube.com/watch?v=AbC123&si=share` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `AbC123` | `AbC123` |
| `//youtube.com/watch?v=AbC123&si=share` | `https://youtube.com/watch?v=AbC123` | `https://youtube.com/watch?v=AbC123` | `AbC123` | `AbC123` |
| `file:///not-a-real-clipmind-path/demo.mp4` | `https:///not-a-real-clipmind-path/demo.mp4` | `https:///not-a-real-clipmind-path/demo.mp4` | `None` | `None` |
| `plain-text` | `https:plain-text` | `https:plain-text` | `None` | `None` |
| `""` | `https:///` | `https:///` | `None` | `None` |

All 47 rows match. Acquisition adapter names are also asserted in the executable
table in `tests/test_source_identity.py`. An additional cross-product probe
compared 18 hosts, 13 paths, 9 queries and 4 scheme forms against the old
functions: 8,424 combinations, or 8,471 comparisons including the table above,
with zero differences (including exception outcomes).

## Local files and complete-pack reuse

Local-file tests create real temporary media paths. Bare existing paths still
normalize to their resolved file URI. Explicit `file://` inputs still pass
through the historic URL normalization and acquire an `https://` prefix; this
quirk is deliberately not fixed in the ownership migration. Local acquisition
still selects the local adapter. The original media file is not modified.

Reuse tests call the real `JobStore.reusable()` and real complete-pack loader
against on-disk fixtures. They cover canonical-equivalent YouTube/Douyin URLs,
tracking-only differences, query ordering, distinct generic `?p=1` versus
`?p=7`, known IDs across URL forms, local paths, and missing/incomplete packs.
Plugin tests also prove custom hooks control real pack reuse.

## Known pre-existing identity limits

Preservation is not an endorsement of the old rules:

- Bare local paths and explicit `file://` URIs have different cache keys.
- Historic identity suffix checks are broader than acquisition domain checks;
  the table includes synthetic lookalikes to make that distinction explicit.
- **Known-ID reuse can alias distinct parts.** For a completed job with result
  ID `BV1abc123`, requesting `/video/BV1abc123?p=7` can reuse the pack made
  from `?p=1`. The canonical keys differ but `JobStore.reusable()` accepts
  either equal canonical keys **or** equal known IDs. An independent probe of
  old and new helpers reproduced the same reuse. Therefore the general claim
  “all identity-bearing query differences prevent reuse” is not true today.
  Fixing it requires an explicit identity/reuse semantics change, outside this
  relocation. The generic `/watch?p=1` versus `/watch?p=7` gate does pass.

No observed baseline behavior changed; none of these limits was silently fixed
or added as a new product guarantee.

## Verification for this migration

- Full local suite: 162 tests passed (152 baseline tests plus 10 added tests).
- Existing-pack evaluation: 3/3 cases passed, canonical counts 243 / 29 / 31.
  This audits existing output; it is not a fresh network extraction.
- Fresh synthetic evaluation using Apple Vision: all four slides recognized,
  15 candidates -> 4 canonical -> 4 preview, complete Evidence Pack, transcript
  explicitly unavailable because the fixture has no audio. The restricted
  sandbox could not perform OCR; the identical command passed with access to
  Apple Vision. No code or baseline was changed to bypass that failure.
- The plugin wiring mutation probe ignored the selected adapter's identity
  hooks: both targeted tests failed. Restoring delegation made both pass.
- Compileall, packaged app.js syntax, and diff whitespace checks passed.
- Acquisition, pipeline, dHash/media, visual selection, OCR/ASR, job reuse
  implementation, Evidence Pack schema/writer, evaluation baselines, and release
  workflows are unchanged from main.
