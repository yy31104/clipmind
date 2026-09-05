"""Durable ownership of the files an acquisition strategy writes.

Cleanup has to work from the job directory alone. Normal completion, an
exception and a cancellation all still hold the ``MediaAsset``, but restart
recovery does not: the process that created it is gone. So ownership is
recorded on disk, before any bytes land, rather than in the returned object.

Ownership is deliberately narrow. Being inside the job directory is not what
makes a path deletable -- the Evidence Pack lives there too. Acquisition may
only own the ``acquisition/`` directory and siblings carrying the same prefix,
so no rule change can ever make a final artifact, or the job directory itself,
something cleanup is entitled to remove.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger("clipmind.acquisition")

ROOT_NAME = "acquisition"
# The ledger is a sibling of the owned directory, not a member of it, so a
# partial cleanup can keep the record of what still needs removing.
LEDGER_NAME = f"{ROOT_NAME}-ledger.json"
LEDGER_VERSION = 1


def workspace(workdir: Path) -> Path:
    """The directory every acquisition strategy must write into."""
    return workdir / ROOT_NAME


def ledger_path(workdir: Path) -> Path:
    """Where durable ownership is recorded."""
    return workdir / LEDGER_NAME


def open_workspace(workdir: Path, *, strategy: str = "unknown") -> Path:
    """Create the owned directory and declare it owned before downloading.

    A symlinked root is refused rather than followed: writing through it would
    put media outside the job directory, where cleanup has no authority.
    """
    root = workspace(workdir)
    if root.is_symlink():
        raise ValueError(
            f"refusing to acquire through a symlinked directory: {root}"
        )
    root.mkdir(parents=True, exist_ok=True)
    _write_ledger(
        workdir,
        {
            "version": LEDGER_VERSION,
            "root": ROOT_NAME,
            "strategy": strategy,
            "external_artifacts": [],
            "opened_at": time.time(),
        },
    )
    return root


def record_strategy(workdir: Path, strategy: str) -> None:
    """Note which strategy produced the media, for durable provenance."""
    ledger = load(workdir)
    if ledger is None:
        return
    ledger["strategy"] = strategy
    _write_ledger(workdir, ledger)


def record_external(workdir: Path, path: Path) -> None:
    """Take ownership of an artifact written outside the owned directory.

    Only acquisition-prefixed paths inside the job directory can be owned. The
    job directory itself, the Evidence Pack and every other final artifact are
    refused, so no ledger entry can authorize deleting them.
    """
    relative = _owned_relative(path, workdir)
    if relative is None:
        raise ValueError(f"acquisition may not own this path: {path}")
    ledger = load(workdir) or {
        "version": LEDGER_VERSION,
        "root": ROOT_NAME,
        "strategy": "unknown",
        "external_artifacts": [],
        "opened_at": time.time(),
    }
    entries = list(ledger["external_artifacts"])
    if relative not in entries:
        entries.append(relative)
    ledger["external_artifacts"] = entries
    _write_ledger(workdir, ledger)


def load(workdir: Path) -> dict | None:
    """Read the durable ledger, or ``None`` when there is nothing usable.

    A corrupt ledger must never be able to block cleanup, so every decoding and
    shape problem degrades to ``None`` instead of raising.
    """
    try:
        ledger = json.loads(ledger_path(workdir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ValueError covers JSONDecodeError and UnicodeDecodeError alike.
        return None
    if not isinstance(ledger, dict):
        return None
    entries = ledger.get("external_artifacts")
    ledger["external_artifacts"] = (
        [entry for entry in entries if isinstance(entry, str)]
        if isinstance(entries, list)
        else []
    )
    return ledger


def leftovers(workdir: Path) -> list[str]:
    """Acquisition artifacts a finished job should no longer have.

    Callers asking this instead of listing filenames cannot drift out of sync
    with what acquisition actually owns.
    """
    found = []
    root = workspace(workdir)
    if root.exists() or root.is_symlink():
        found.append(ROOT_NAME)
    if ledger_path(workdir).exists():
        found.append(LEDGER_NAME)
    return found


def purge(workdir: Path) -> None:
    """Delete everything acquisition owns for this job.

    Safe to call when nothing was acquired, twice in a row, and from restart
    recovery with no live ``MediaAsset``. Anything that could not be deleted
    stays in the ledger so a later cleanup retries it.
    """
    ledger = load(workdir)
    unfinished: list[str] = []
    for entry in (ledger or {}).get("external_artifacts", []):
        candidate = workdir / entry
        if _owned_relative(candidate, workdir) is None:
            logger.warning("Ignoring ledger entry acquisition may not own: %s", entry)
            continue
        if _remove(candidate):
            continue
        logger.warning("Could not remove %s; keeping it in the ledger", candidate)
        unfinished.append(entry)

    root_removed = _remove(workspace(workdir))
    if unfinished or not root_removed:
        _write_ledger(
            workdir,
            {
                "version": LEDGER_VERSION,
                "root": ROOT_NAME,
                "strategy": (ledger or {}).get("strategy", "unknown"),
                "external_artifacts": unfinished,
                "opened_at": (ledger or {}).get("opened_at", time.time()),
            },
        )
        return
    _remove(ledger_path(workdir))


def _remove(path: Path) -> bool:
    """Delete a path without ever following a symlink to its target."""
    try:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return False
    return not path.exists() and not path.is_symlink()


def _write_ledger(workdir: Path, ledger: dict) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    path = ledger_path(workdir)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(ledger, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def _owned_relative(path: Path, workdir: Path) -> str | None:
    """The job-relative path, when acquisition is allowed to own it.

    Membership of the job directory is necessary but not sufficient: the path
    must also carry the acquisition prefix. That keeps the rule a whitelist of
    what acquisition creates rather than a blacklist of artifacts to spare.
    """
    try:
        resolved = path.resolve()
        base = workdir.resolve()
    except OSError:
        return None
    if resolved == base:
        return None
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        return None
    head = relative.parts[0] if relative.parts else ""
    if head != ROOT_NAME and not head.startswith(f"{ROOT_NAME}-"):
        return None
    return str(relative)
