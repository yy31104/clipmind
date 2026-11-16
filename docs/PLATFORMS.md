# Sources: what this release actually verifies

Recognizing a URL, downloading its metadata, and producing the right complete
Evidence Pack are separate checks. A generic yt-dlp match is an attempt, not a
promise of platform support. This release adds no social-platform adapter.

| Source | Evidence | Boundary |
| --- | --- | --- |
| Local video | Ten real 180-second English excerpts, full ASR/OCR/frame extraction; separate clean native and Docker installation smokes | Quality varies; see the [real-talk table](REAL_TALK_BENCHMARK.md). The user's original file is preserved. |
| media.ccc.de | Complete 39:24 Xous talk through the generic URL path; exact title/duration checked; search/UI/MCP and cleanup verified | One public CC BY recording and English track; this does not verify every generic website. |
| YouTube | Previously validated built-in acquisition and source identity; historical reports in [benchmarks](BENCHMARK.md) | Not freshly revalidated against YouTube in the v1.3.0 quality run. Login, regional and extractor failures remain possible. |
| Douyin | Previously validated browser-session fallback, two real complete packs and lifecycle tests | Not freshly revalidated against Douyin in this release. Native Chrome is required; the Docker image does not include Chrome. A window may appear temporarily. |
| Bilibili | Part-aware identity and real reuse-path regression tests | Cache correctness is tested; authenticated current-platform full extraction is unverified. No Bilibili support announcement. |
| Instagram, TikTok, X and other HTTP(S) sources | Generic yt-dlp fallback may attempt supported extractors | Unverified here. No guarantee of access, identity or quality for a given post. |
| WeChat, Xiaohongshu, Douyin image posts/Live Photos | Archived candidate work only | Not delivered or advertised by v1.3.0. |

## Decision on the old candidates

[Issue #45](https://github.com/yy31104/clipmind/issues/45) preserved stale PR
work; it was not a promise to ship it. None of its experimental platform/media
adapters is merged for this release. The original and archive branches remain
available as recovery points; closing the acceptance checklist does not delete
that code or users' packs.

A future platform proposal needs a small independent PR and current evidence:
the requested identity (including parts), correct media and full pack, useful
failure explanations, original-file preservation, cancellation and restart
cleanup. Login-only experiments without a real end-to-end run do
not satisfy that gate. Pure articles, protected streams and inaccessible media
must not silently become a cover image, recommendation or another video.

## Storage and access

Acquisition media, temporary audio/frames and temporary browser profiles are
cleaned by the production lifecycle. Saved transcripts, screenshots, model
weights and exported ZIPs remain intentionally. UI removal moves packs into
the library's `.trash`; it does not free their disk space. Keep the web server
on loopback; the container examples publish only `127.0.0.1:8420`.
