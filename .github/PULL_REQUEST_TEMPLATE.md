## Problem

What user-visible problem or documented gap does this change solve?

## Change

Describe the narrow implementation and what is deliberately out of scope.

## Evidence and tests

- [ ] A test fails before the fix and passes after it, or the reason no test is possible is explained.
- [ ] `make test`
- [ ] `make eval` (audits existing packs; does not re-extract)
- [ ] `make eval-reextract` for extraction-path changes, or marked not applicable
- [ ] `.venv/bin/python -m compileall -q clipmind tests scripts`
- [ ] `node --check clipmind/web/app.js` (when web files are touched)
- [ ] `git diff --check`

List exact results and any manual/synthetic evaluation.

## Contract checklist

- [ ] Canonical evidence remains uncapped and fail-open.
- [ ] Preview changes do not remove/repoint canonical artifacts.
- [ ] Complete-or-refuse and manifest-last semantics remain intact.
- [ ] Old additive v1 packs remain readable.
- [ ] No secrets, cookies, personal data, downloaded media, transcript/OCR bodies, or creator screenshots were added.
- [ ] Public docs/schema/configuration were updated if behavior changed.

Mark non-applicable items and explain why.

## Privacy, compatibility, and resource impact

Describe new network access, filesystem writes, plugin/provider trust, CPU/GPU,
memory, disk, model, schema, or platform implications.
