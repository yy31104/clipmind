# Contributing to ClipMind

Keep changes focused and include a regression test for changed behavior. Do not
commit downloaded media, browser cookies, transcripts, OCR text, or creator
images from real sources.

## Verification

Every pull request should run:

```bash
make test
make eval
git diff --check
```

`make eval` audits the newest already-complete Evidence Pack for each checked-in
real case. It is fast and normally offline. It does **not** prove that the
current extraction pipeline can reproduce those packs.

Any change to acquisition, sampling, deduplication, OCR/ASR wiring, canonical
membership, or preview derivation must also run:

```bash
make eval-reextract
```

That target uses the current pipeline and a fresh temporary library, so cached
packs cannot satisfy it. It may require network access, working local providers,
and a current browser session. The evaluator hashes the downloaded source before
removing it. It enforces the exact canonical count only when that hash matches
the case baseline; changed or unavailable source identity is reported explicitly
and only the broad quality bounds apply. Source drift is a review signal, not a
reason to rewrite the count or hash baseline.

Use `make eval-synthetic` for the generated silent-slide end-to-end fixture.
