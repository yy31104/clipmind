# Security policy

## Supported versions

Security fixes target the latest release and the current `main` development
line. Older development snapshots and unsupported Evidence Pack schema majors
may be asked to upgrade before a fix can be validated.

## Report a vulnerability privately

Use GitHub's private vulnerability-reporting page when it is available:

<https://github.com/yy31104/clipmind/security/advisories/new>

Include:

- affected commit/version and operating system;
- the smallest reproduction that does not expose real credentials or private
  media;
- expected impact and whether exploitation requires local access;
- any suggested mitigation;
- how you would like to be credited.

If GitHub private reporting is unavailable, open a public issue that says only
that you need a private security contact. Do **not** post exploit details,
cookies, tokens, local paths, personal data, downloaded media, transcripts, OCR,
or screenshots.

Maintainers will aim to acknowledge a report within seven days, validate and
scope it, coordinate a fix and disclosure date, and credit the reporter unless
they prefer anonymity. Timelines depend on impact and maintainer availability;
please allow a reasonable remediation window before public disclosure.

## High-value report areas

- path traversal or arbitrary reads/writes through pack IDs, frame paths,
  uploads, exports, Inbox delivery, or plugin metadata;
- credential, browser-cookie, token, or raw yt-dlp diagnostic disclosure;
- unauthenticated network exposure beyond the documented loopback-only model;
- command injection through URLs, filenames, model names, or configuration;
- a partial/corrupt pack accepted as complete;
- restart behavior that silently replays side effects;
- temporary cleanup deleting canonical/user files;
- malicious archive contents or output escaping the selected destination;
- MCP/REST access outside the configured library;
- dependency or packaging issues that execute unexpected code.

## Security boundaries and non-vulnerabilities

- The local web server has no authentication and must stay on `127.0.0.1` unless
  the operator provides a separate trusted security layer.
- A user-authorized browser-cookie source gives yt-dlp broad read access to that
  browser's cookie database. That is a privacy boundary, documented in
  `docs/PRIVACY.md`, not domain-scoped sandboxing.
- Evidence Packs intentionally contain source media evidence and can contain
  personal/confidential material. A trusted local user or connected agent with
  filesystem access can read it.
- Third-party source/provider plugins execute as local Python code with the same
  account permissions. Review packages before installing them.
- ClipMind does not claim to bypass DRM, private-source controls, regional
  restrictions, or upstream removals.

## Handling secrets in reports

Revoke any token or cookie that was accidentally exposed before reporting it.
Use synthetic files and placeholder URLs wherever possible. Maintainers may
delete a public report or attachment that contains sensitive source evidence to
limit further exposure.
