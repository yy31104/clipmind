# Reliable local operation and release gates

ClipMind is a **single-user, local application**. It is not an authenticated,
multi-tenant web service. Keep the listener and container publication on
`127.0.0.1:8420`. The HTTP app accepts loopback Host names and rejects browser
writes from other origins. A public deployment needs authentication,
authorization, quotas, isolation and an operational owner before exposure.

## Install, start and recover

Use the locked environment and the platform instructions in [INSTALL.md](INSTALL.md).
On Windows, `scripts/clipmind_windows.ps1 serve` recognizes an existing ClipMind
listener and avoids starting another process. It refuses an unrelated process
on that port; it does not kill it. Stop new submissions and wait for active jobs
before upgrading. Keep a copy of the library and the prior application version.

The library directory is the source of truth. SQLite search data is a derived
cache. A corrupt cache and its sidecars are moved into a recoverable
`.index-recovery-*` directory before rebuilding; a locked file, full disk or
permission failure is reported rather than misclassified as corruption.

Job snapshots are atomically replaced. If a crash occurs after the complete
Evidence Pack was published, startup audits the artifacts and restores the
completed job without downloading or processing the source again. A malformed
individual job is isolated. Incomplete running jobs are marked interrupted;
they do not silently restart an expensive extraction. A failed state write is
visible in the UI, including whether a complete result is available to export.

If one analysis branch fails, its sibling work is cancelled and joined before
temporary media are cleaned up. Native ASR/OCR/diarization threads cannot be
safely killed: graceful cancellation waits for the current call to return while
holding its resource slot. It does not start queued work or promise a fixed
shutdown deadline. Interrupted browser uploads remove their partial files.

One server should own a library. Arbitrary concurrent CLI/SDK/server writers and
forced termination of all Windows subprocess descendants have not been certified.

## Verify outputs, not just a green status

Run this against a library ID or a saved pack directory:

```powershell
uv run --locked clipmind verify "C:\path\to\evidence-pack" --json
```

The command is read-only and uses no network. It checks the completion manifest,
source identity, job status, record counts, unique IDs, transcript time order,
OCR associations, transcript references, image paths, actual image decoding and
preview/canonical equality. A failing audit returns a nonzero exit status. This
is **artifact integrity**, not proof that every spoken word or visual meaning
was recognized correctly.

Compact exports include `selection_report.json`: every source visual state has
a retained/omitted decision, reason, and representative where applicable. The
transcript is copied verbatim, and omitted OCR remains as text. Compact exports
are derivatives; keep the complete pack when visual evidence matters.

ZIP output must be outside its source pack. Inbox delivery compares the actual
portable artifacts before reporting `already_present`; repaired text or images
with the same pack ID produce an explicit conflict. The existing Inbox copy is
preserved. Deliver a revised pack to a different Inbox rather than overwriting
evidence implicitly. Malformed source metadata is isolated from library listing,
and unreadable records return actionable errors instead of an empty success.

## Visual quality gates

The current selector checks structural evidence of code, documents, plots and
connected diagrams. Low-confidence OCR alone no longer removes an otherwise
recognizable document. Talking-head changes and ordered subtitle text matching
the nearby transcript do not justify another image. Code edits, numeric changes,
negations and changed chart marks must be exercised by regression fixtures.

Canonical deduplication now checks local pixel changes as well as the perceptual
hash. A small changed number or chart point can survive a hash collision. This
can increase canonical frames and processing estimates; it does not override
resource budgets. Selection after OCR is responsible for compact reading images.

```powershell
uv run --locked python scripts/evaluate_visual_quality.py --output visual-quality.json
uv run --locked python -m unittest discover -s tests -v
```

The labeled fixture gate requires all labeled information groups, no labeled
irrelevant images, no redundant representatives, and unchanged input bytes.
The CI workflow is configured to run it on all three platforms and retain the
report. The Linux integration
job additionally exercises FFmpeg/image formats and a generated, offline
Tesseract extraction. These are reproducible tests, not a live-site success rate.

Review representative real packs whenever selection rules change, including
pure speech, filmed pages, dense code, charts, silent slides, image posts and
long podcasts. Compare both retained information and exported image count/bytes;
a rule that turns OCR noise into hundreds of extra screenshots is a regression.
Keep the reviewed sample corpus separate from new holdout material.

Uniform sampling can miss brief events between samples. Moving-camera OCR is
noisy, and approximate reading-view consolidation is not a proof of equivalence.
For high-stakes use, inspect the canonical images and original material. No
automatic selector can honestly promise zero omissions on every source.

## Link and media support

See [INPUT_TYPES.md](INPUT_TYPES.md) for the support matrix. Xiaohongshu now has
an explicit public-note adapter with identity-preserving shortlink resolution,
ordered static-image pages and video metadata handling. Authentication redirects
receive `login_required`; unrelated recommendations, incomplete downloads and
Live Photo cover-only fallbacks are rejected. Login redirects were exercised in
a bounded live check; they did not produce accessible media. Public video/image contracts
are fixture-tested; authenticated Xiaohongshu and WeChat capture are not release
claims. Never equate a recognized domain with successful media extraction.

Before advertising another platform, require an exact source-ID check, real
sample acquisition, relevant media-type tests, bounded resources, cleanup on
failure/cancellation, and token-free exported provenance. Sources requiring
login, removed content, DRM, changed endpoints and network failures can fail.

## Product differentiation and release decision

More supported domain names alone is not a defensible advantage. ClipMind's
product focus is local processing plus inspectable speech, visual evidence,
source identity, portable exports and agent interfaces. Measure the cost of
getting a **correct, reviewable result**, not only summary speed.

A public release should attach the tested commit, frozen dependency install,
package smoke results, visual gate report, known unsupported paths, and a
rollback procedure. Windows local verification is not three-platform runtime
certification, and local patches are not evidence that GitHub CI has run them.
Passing those gates verifies the stated local workflows; it does not certify an
internet-facing service or establish a competitive accuracy lead. Use the Actions
run for the exact commit being installed, not a result from another branch or
earlier revision.
