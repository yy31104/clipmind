# Changelog

Notable user-visible changes are recorded here. Evidence Pack schema versions
are documented separately and do not have to equal the application version.

## Unreleased

### Changed

- Keep the full canonical frame set visible from result pages, warn clearly when
  OCR is incomplete, avoid fleeting blurred transitions in derived previews,
  and allow OCR/preview repair from already-retained screenshots.

- Library cards line up. Every title takes two lines, and a longer one fades
  out at the end of its second line instead of stretching its card; the full
  title is on the detail page. Cards in a row keep one height even when a pack
  lists older versions. A 回到顶部 button appears once a page is scrolled down.

- Reading a result starts from the evidence. Each search hit is its own link to
  that moment: an on-screen-text hit selects the matching screenshot, a speech
  hit the matching transcript line, and the screenshot on screen at that time
  opens beside its OCR text and the speech within 10 seconds. Clicking any
  screenshot shows the same context. The overview leads with 复制全文转写,
  导出笔记（Markdown）, 下载全部资料（含截图） and 打开原视频 (only for web
  sources); completeness, JSON resources and MCP moved under 开发者信息.

- Live and scheduled streams are refused before any of their media is
  downloaded, with `live_stream_unsupported` and an action to submit the replay
  once the stream has ended. yt-dlp checks this after reading metadata, in the
  same call, so the refusal costs no media bytes or extra request, and
  **Process anyway** does not bypass it. Recordings of finished streams are
  unaffected. Capturing part of a live stream is not supported.

- Apple Vision OCR works again on macOS 27. Creating Vision's image request
  with an empty Python dict as its options raises `NSInvalidArgumentException`
  there (observed with PyObjC 12.2.2), so OCR failed on every frame. ClipMind now
  passes `None` for Vision's default options; recognition settings and results
  are otherwise unchanged.

- Stopping a job now stops the tools it started. yt-dlp and FFmpeg run in their
  own process group and are killed, together with their children (such as
  yt-dlp's FFmpeg merge), before cancellation or application shutdown proceeds
  to cleanup. Previously they kept running and could write into a directory
  being cleaned up. On Windows only the direct child is stopped.

- Douyin links that yt-dlp cannot acquire now fall back to a temporary,
  signed-out Google Chrome session driven by Playwright. The page is only
  trusted for the requested video (a different or recommended video is refused),
  the browser closes as soon as the media address is known, and the media follows
  the normal acquisition cleanup. `douyin.com/user/self?modal_id=…` links keep
  their video identity, and yt-dlp's "fresh cookies" message for Douyin is
  reported as unavailable metadata rather than expired cookies. Probes never
  launch the browser; `CLIPMIND_DOUYIN_BROWSER=0` turns it off. Playwright is
  now a dependency: the lockfile adds Playwright and pyee, and the already
  locked greenlet 3.5.5 now installs on every platform, including macOS arm64.

- Inbox failed/interrupted tasks and Library versions support individual selection,
  select-all and confirmed bulk removal. Removed job directories are retained in
  the library's `.trash` (manual recovery), excluded from search and restart recovery.
  Active jobs, original media and separately exported copies are not removed.

- Bilibili BV/av request identities now include explicit part numbers. Cache
  reuse rejects ambiguous part queries and legacy packs without matching part
  identity, including same-URL matches. Identical canonical URLs can reuse
  compatible downloader IDs (av-to-BV resolution, or an unspecified anthology
  URL's `_p1` result); different URLs never infer those equivalences. Stored
  downloader IDs and old packs are not migrated. This is an identity fix, not
  a claim of verified Bilibili acquisition support.
- Raise the default cumulative probe response-body budget from 4 MiB to 32 MiB
  to restore YouTube metadata probing with headroom for multiple resources.
  Request, wall-clock and child-output limits are unchanged; smaller explicit
  body budgets are still enforced across resources and cookie attempts.
- URL probes now share HTTP response-body and request budgets across cookie
  attempts. Large pages, compressed responses and unsupported transports fail
  conservatively with `unknown`; child output and process lifetime are bounded.
- Probe cookie files are read-only. URL acquisition and probes ignore yt-dlp
  config files, and relative workdir/cookie paths resolve before child cwd changes.
- Acquisition failures are classified across all attempts, preserving existing
  public error codes and optional adapter hooks.

- acquisition writes into a job-owned `acquisition/` directory whose ownership
  is recorded on disk before downloading, so completion, failure, cancellation
  and restart recovery share one cleanup contract and temporary media is removed
  whatever the acquiring strategy named it. With `CLIPMIND_KEEP_VIDEO=1` the
  retained media now lives in `acquisition/` instead of at the job root.

## 1.2.0 — 2026-09-04

### Added

- verified source adapters for YouTube, Douyin, and local files, plus the
  installed `clipmind.sources` plugin boundary;
- complete-or-refuse visual cost preflight with an explicit full-processing
  override;
- portable faster-whisper and Tesseract providers, word timing, OCR layout,
  stability/scene/scroll metadata, and optional pyannote diarization;
- browser uploads, local evidence search, and productized Inbox/Library/detail
  views;
- Python SDK, canonical REST resources, full CLI, and stdio MCP tools/resources;
- Python packaging, cross-platform CI, Docker, desktop launcher, and local macOS
  app/DMG build path;
- public installation, architecture, MCP, source-plugin, privacy, security, and
  contribution documentation;
- separate existing-pack audits from fresh real-source re-extraction, with exact
  canonical-count checks gated by matching source SHA-256.

### Changed

- runtime settings and media providers are injected rather than fixed at import;
- preview serialization has one canonical view builder;
- Evidence Pack writer emits additive schema `1.3.0` while accepting all v1
  minor versions;
- the SQLite search index is explicitly derived from complete packs and keeps
  only the newest source version in global results.
- ZIP and Inbox delivery sanitize machine-local retry paths from `job.json`.

### Removed

- duplicate legacy keyframe/note generation and its obsolete configuration and
  summarization dependencies. Older packs remain readable.

## 1.1.0

- measured transcript novelty for every canonical visual state;
- added a preview safety net for unspoken document/code text without changing
  canonical membership.

## 1.0.1

- kept slow SSE subscribers attached through bounded resync rather than silently
  dropping them;
- prioritized complete Evidence Packs in the library UI and collapsed failures.

## 1.0.0

- published the local-first Douyin extraction baseline;
- added durable recovery, uncapped canonical visual states, derived previews,
  readable evidence resolution, deterministic Evidence Packs, knowledge-base
  handoff, evaluation, and runtime safety.
