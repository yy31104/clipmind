# Input types and verification

ClipMind chooses an extraction path from the acquired content. A source URL,
an image page, an audio recording and a document are different inputs; an
extractor failure does not mean that installation failed or that a link expired.

## Content paths

| Content | Current handling | Evidence and limits |
| --- | --- | --- |
| Video with speech | Sample visual states, OCR and local ASR | Codec must be readable by the installed FFmpeg build |
| Video without an audio track | Visual states and OCR; no ASR model load | `audio_status=absent`; no invented transcript |
| Audio track with no detected speech | Preserve the actual ASR result and modality status | OCR is never substituted for speech |
| Pure audio, including audio with attached cover art | Audio transcription path; attached cover art is not a timed video stream | Local files and remote downloads explicitly identified as audio are checked using FFprobe stream dispositions |
| Local still image | Original image and OCR | Uses the installed Pillow decoder; no synthetic video clock |
| Local animated GIF, APNG or WebP | Video sampling and OCR | Actual decoder support varies; short animations retain their first readable frame |
| Local multipage TIFF | Decode every page to lossless PNG, preserving page order and OCR | Original container remains unchanged; its hash identifies the source. `image_page_transform=tiff-to-png-lossless` distinguishes decoded pages from byte-identical original images |
| Douyin static image post | Acquire every original page and OCR in source order | Exact requested item ID is required; covers and recommendations cannot replace pages |
| Douyin Live Photo | Every original page plus sampled motion from its own clip | Clip-relative time, not a whole-post clock; image-post audio is not acquired or transcribed |
| Xiaohongshu public video note | Exact-ID public page parsing and FFprobe-verified video | Fixture-tested; the supplied live shortlink requires login, so authenticated acquisition remains unverified |
| Xiaohongshu public static-image note | Every declared page in original order, including repeated pages | A missing page fails the job; Live Photo posts are explicitly refused until their motion can also be preserved |
| Finished live-stream recording | The ordinary recording path where the source exposes a recording | An active or scheduled stream is not treated as a finished recording |
| Active live stream | Not captured | Submit a recording/replay; a partial live capture is not advertised as complete |
| Profile, collection or playlist page | Not automatically expanded into all items | Submit individual item links; a collection URL is not permission to process unrelated recommendations |
| Article or ordinary webpage body | No built-in general document/body extractor yet | An unsupported URL remains an extraction capability gap, not evidence of expiry. A saved image of a page can use the image path, but is not native article-body extraction |
| PDF, Office document, text file | Not currently admitted as a media upload | These require a separate document model; renaming them to a video extension does not add support |

## Source coverage

YouTube, Douyin and local media have built-in tested paths. A successful real
item validates that item and the exercised path, not every account, region,
codec or future site version. Douyin browser fallback uses a temporary signed-out
Chrome session and never replaces a missing target with a recommended item.

Xiaohongshu has a separate public-note adapter for `xhslink.cn`, `xhslink.com`
and canonical note URLs. Share signatures may be used during acquisition but
are omitted from exported provenance. Redirects to login are reported as
`login_required`; installing Chrome again will not resolve that limit.

WeChat Channels share links have an experimental browser route. The authenticated
path has not passed a real end-to-end acceptance test. Some items can be available
only inside WeChat; the existence of an adapter does not imply all Channels
content types are supported. See [installation](INSTALL.md) and
[limitations](LIMITATIONS.md).

## Browser uploads and local files

The browser upload picker and server allowlist include the common video/audio
families supported by the local path, including MPEG/MPG, TS/M2TS/MTS, WMV/VOB,
3GP/3G2, AIFF/WMA and AC3/EAC3. Admission is followed by actual decoding; the
extension alone is never evidence that a pack can be produced. File-size,
decoded-image and extraction budgets still apply. Original user files are not
changed by extraction or cleanup.

Multipage TIFF conversion preserves supported decoded pixel modes, including
16-bit grayscale. Modes that cannot be represented losslessly as PNG, such as
floating-point TIFF pages, are explicitly refused; they are not silently
clamped to an 8-bit preview. Limits also apply to page count, per-page pixels,
total decoded pixels and generated page bytes.

## Regression gate for another content type

Before describing a new type as supported, verify the complete path:

1. Bind source identity to the requested item, and choose content type without
   borrowing a cover, recommended item or unrelated page body.
2. Decode all declared pages/assets under bounded resource limits. An unreadable
   later page must not result in a successful prefix-only pack.
3. Keep page order, real timestamps and missing-modality status distinct. Do not
   invent speech, duration or a playback clock for a document/image collection.
4. Check reading, search references, full/compact/text exports and temporary-file
   cleanup. Original files and pre-existing Evidence Packs must remain unchanged.
5. Report generated-fixture tests separately from real-site verification and
   unauthenticated verification separately from authenticated verification.

Historical failed cards are retained as history. A later successful job does not
rewrite the original failure into a success, and an old saved error is not a new
test of current source availability.
