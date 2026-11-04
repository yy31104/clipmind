# Development

Use the source setup in the root [README](../README.md). Keep changes focused on acquisition, extraction, evidence access, or their existing interfaces.

```bash
make test
make eval-synthetic
make bench
.venv/bin/python -m compileall -q clipmind tests scripts
node --check clipmind/web/app.js
sh -n scripts/install.sh scripts/build_macos_app.sh
git diff --check
```

The unit suite is offline. The synthetic check generates silent slides locally and uses real FFmpeg and OCR. `make eval` reads existing packs listed in `eval/cases.json`; a clean clone has no such packs. `make eval-reextract` fetches those sources and needs separate network/provider access. See [evaluation](REAL_WORLD_EVAL.md).

- Keep full evidence separate from its reading preview.
- Write the completion manifest last and preserve interrupted jobs after restart.
- Keep processing local and document configured browser-cookie access. Optional diarization credentials stay local.
- Preserve additive Evidence Pack compatibility and update the schema, writer, reader, and fixtures together when changing the format.
- Test source adapters through the existing protocol; see [source adapters](SOURCE_ADAPTERS.md).
- Use generated media and temporary libraries in tests. Keep credentials, private media, transcripts, and account data out of fixtures and logs.
- Run the existing failure and recovery tests when changing lifecycle behavior.

Read [architecture](ARCHITECTURE.md), [the format](EVIDENCE_PACK.md), and [privacy](PRIVACY.md) before changing those boundaries. Private reports follow the [security policy](SECURITY.md).
