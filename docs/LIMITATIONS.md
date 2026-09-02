# Limitations

- **Platform and OS:** v1 supports Douyin on macOS. MLX Whisper targets Apple
  Silicon and OCR uses Apple's Vision framework.
- **Acquisition is upstream-dependent:** Douyin can reject a previously valid
  URL or cookie session. Opening Douyin in Chrome and copying a fresh share link
  is sometimes required. Private or removed media cannot be recovered.
- **Sampling is evidence-driven, not continuous recording:** production uses
  uniform 2 fps detection. A 4 fps probe covered all 105 states that remained
  stable for at least 0.5 seconds in the long code/UI fixture. Sub-0.5-second
  flashes can still be missed and are not assumed readable.
- **Visual semantics are conservative:** dHash and OCR changes are heuristics,
  not general scene understanding. Canonical output may retain transition or
  scrolling frames; preview may omit a useful representative even though the
  canonical frame remains available.
- **OCR is not ground truth:** small fonts, stylized subtitles, motion blur, and
  unusual layouts can produce omissions or substitutions. v1 stores 1280 px
  evidence because the measured 640 px alternative lost substantial code/UI
  text, but higher resolution does not guarantee correctness.
- **ASR is not ground truth:** accents, music, crosstalk, and specialized terms
  can reduce Whisper accuracy. ClipMind preserves segments and timestamps but
  does not semantically correct them on the free path.
- **Corpus size:** the real-video suite has three public sources covering long
  code/UI, scrolling, talking-head captions, inserted documents, and slides.
  Silent infographic videos, arbitrary whiteboards, and 10-minute-plus sources
  remain explicit corpus gaps.
- **Disk use follows content:** `visual_states/all/` has no fixed cap. Long,
  visually dense videos can produce large packs. Temporary media is cleaned,
  but final evidence is not automatically evicted.
- **Cache aliases:** exact normalized URLs are reusable, and stable URLs match a
  known source ID. Two different unresolved short-link codes for the same video
  may require one acquisition before their common identity is known.
- **Local UI lifecycle:** there is no per-job cancel control or history
  pagination in v1. Concurrency is bounded, but a very large long-lived library
  will increase startup index time.
