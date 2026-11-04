# Contributing to ClipMind

Thank you for improving ClipMind. Contributions are welcome across source
adapters, extraction providers, visual quality, packaging, APIs, tests,
documentation, and accessibility.

## Start with the product contract

Before changing code, read:

- [Architecture](docs/ARCHITECTURE.md)
- [Evidence Pack v1](docs/EVIDENCE_PACK.md)
- [Source adapters](docs/SOURCE_ADAPTERS.md) for a new platform
- [Privacy](docs/PRIVACY.md) and [Limitations](docs/LIMITATIONS.md)

The most important invariants are:

- canonical evidence is uncapped and fails open when comparison is uncertain;
- preview logic never deletes or repoints canonical evidence;
- cost limits refuse early rather than truncating or lowering fidelity;
- `manifest.json` is written last and is the only completion marker;
- `running` is persisted before side effects, and interrupted jobs never resume
  automatically;
- old additive v1 packs remain readable;
- local extraction does not acquire a paid-cloud dependency;
- credentials, cookies, downloaded media, transcripts, OCR text, and creator
  images do not belong in fixtures or pull-request logs.

If a proposal needs to change one of these contracts, open a design issue first.

## Good first contributions

Focused entry points include:

- documentation corrections and platform setup notes;
- a separately packaged source adapter using the `clipmind.sources` entry point;
- synthetic media fixtures that reproduce an extraction-quality gap;
- accessibility and keyboard-navigation fixes;
- tests for an already documented failure mode;
- provider availability/error messages on Linux or Windows.

Do not start a large platform core fork when a small adapter is sufficient. Do
not add a generated summary, chat feature, hosted account system, or unrelated
product surface without an accepted design issue.

## Development setup

```bash
git clone https://github.com/yy31104/clipmind.git
cd clipmind
uv venv --python 3.12
uv pip install -r clipmind/requirements.txt
uv pip install -r requirements-test.txt
make test
```

Tests are intentionally offline and should not require model downloads, browser
cookies, or real platform access. Use temporary directories and mocked provider
boundaries. Tiny synthetic media can be generated during a test, but avoid large
binary fixtures.

Run the app during UI work:

```bash
make run
```

Then verify desktop and narrow/mobile layouts, keyboard focus, empty/loading/
error states, and browser console errors.

## Make a focused change

1. Search existing issues and pull requests.
2. For a bug, include the smallest safe reproduction and expected behavior.
3. Create a focused branch and keep unrelated formatting/refactors out.
4. Add a test that fails before the fix and passes after it.
5. Update public docs when a contract, configuration variable, command, schema,
   or supported-source statement changes.
6. Run the complete verification checklist.

Large work should be split into independently reviewable commits. A refactor
commit should not silently change behavior; a schema change should not be hidden
inside a UI pull request.

## Verification checklist

```bash
make test
.venv/bin/python -m compileall -q clipmind tests scripts
node --check clipmind/web/app.js
jq empty schemas/evidence-pack-v1.schema.json
sh -n scripts/install.sh scripts/build_macos_app.sh
git diff --check
```

Every pull request should run `make test`, `make eval`, and `git diff --check`.
`make eval` audits the newest already-complete Evidence Pack for each real case;
it is fast and normally offline, but it does **not** prove that the current
pipeline can reproduce those packs.

Any change to acquisition, sampling, deduplication, OCR/ASR wiring, canonical
membership, or preview derivation must also run:

```bash
make eval-reextract
```

That target uses the current pipeline and a fresh temporary library, so cached
packs cannot satisfy it. It may require network access, working local providers,
and a current browser session. Exact canonical counts are enforced only when
the downloaded source SHA-256 matches the reviewed baseline. Changed or
unavailable source identity is reported explicitly and only broad quality bounds
apply; source drift is a review signal, not a reason to rewrite the baseline.

Use `make eval-synthetic` for the generated silent-slide end-to-end fixture.

If you change packaging, also build a wheel and inspect/install it in a clean
temporary environment. If you change source matching, prove specific adapters
still win before the generic fallback. If you change preview logic, demonstrate
that canonical membership and file targets remain unchanged.

Real-platform manual checks and re-extraction are useful but never replace
deterministic tests. A failed acquisition must remain a failed evaluation; never
change an expected count merely to make a changed-source run green.
Report only aggregate metrics or permission-safe synthetic output; redact
personal data and secrets.

## Evidence Pack evolution

- Additive optional fields can extend the current v1 reader/writer.
- Never reinterpret an existing field or synthesize missing evidence for an old
  pack.
- Update the JSON Schema, writer, reader compatibility set, fixture builder,
  tests, and `docs/EVIDENCE_PACK.md` together.
- A breaking semantic/layout change requires a new major schema and an explicit
  migration/read strategy.

## Pull requests

A good pull request explains:

- the user-visible problem;
- the narrow change and what is deliberately out of scope;
- the invariant or failure mode protected by each new test;
- privacy, compatibility, and resource-use implications;
- exact verification results.

Maintainers may ask to split a pull request when its failure signals would mask
one another. Passing tests are necessary but not proof that a test reaches the
real implementation—prefer a controlled failure injection when that distinction
matters.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Security vulnerabilities must follow [SECURITY.md](SECURITY.md), not a public
bug report.
