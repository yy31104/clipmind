"""Durable ownership of the files an acquisition strategy writes.

Cleanup has to work from the job directory alone. Normal completion, an
exception and a cancellation all still hold the ``MediaAsset``, but restart
recovery does not: the process that created it is gone. So ownership is
recorded on disk, before any bytes land, rather than in the returned object.

Every strategy writes inside :func:`workspace`. The directory is declared owned
the moment acquisition begins, which is what makes a crash at any later point
recoverable -- whatever name the strategy chose, the directory is still ours.
The ledger adds provenance and is the only thing that may authorize deleting a
path outside that directory.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger("clipmind.acquisition")

ROOT_NAME = "acquisition"
LEDGER_NAME = "ledger.json"
LEDGER_VERSION = 1


def workspace(workdir: Path) -> Path:
    """The directory every acquisition strategy must write into."""
    return workdir / ROOT_NAME


def open_workspace(workdir: Path, *, strategy: str = "unknown") -> Path:
    """Create the owned directory and declare it owned before downloading."""
    root = workspace(workdir)
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

    Only paths inside the job directory can be owned. Anything else -- most
    importantly a user's own local video -- is refused rather than recorded,
    so it can never become something cleanup is entitled to delete.
    """
    if not _inside(path, workdir):
        raise ValueError(f"refusing to own a path outside the job directory: {path}")
    ledger = load(workdir)
    if ledger is None:
        ledger = {
            "version": LEDGER_VERSION,
            "root": ROOT_NAME,
            "strategy": "unknown",
            "external_artifacts": [],
            "opened_at": time.time(),
        }
    relative = str(path.resolve().relative_to(workdir.resolve()))
    entries = list(ledger.get("external_artifacts") or [])
    if relative not in entries:
        entries.append(relative)
    ledger["external_artifacts"] = entries
    _write_ledger(workdir, ledger)


def load(workdir: Path) -> dict | None:
    """Read the durable ledger, or ``None`` when there is nothing to read."""
    path = workspace(workdir) / LEDGER_NAME
    try:
        with path.open(encoding="utf-8") as handle:
            ledger = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return ledger if isinstance(ledger, dict) else None


def purge(workdir: Path) -> None:
    """Delete everything acquisition owns for this job.

    Safe to call when nothing was acquired, twice in a row, and from restart
    recovery with no live ``MediaAsset``. A corrupt or missing ledger does not
    strand the directory: the directory name is itself durable ownership.
    """
    ledger = load(workdir)
    for entry in (ledger or {}).get("external_artifacts") or []:
        candidate = (workdir / str(entry)).resolve()
        if not _inside(candidate, workdir):
            logger.warning("Ignoring ledger entry outside the job directory: %s", entry)
            continue
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)
            else:
                candidate.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove owned artifact %s", candidate)
    shutil.rmtree(workspace(workdir), ignore_errors=True)


def _write_ledger(workdir: Path, ledger: dict) -> None:
    root = workspace(workdir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / LEDGER_NAME
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(ledger, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def _inside(path: Path, workdir: Path) -> bool:
    try:
        return path.resolve().is_relative_to(workdir.resolve())
    except (OSError, ValueError):
        return False
