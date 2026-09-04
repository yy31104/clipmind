"""One-way delivery of completed Evidence Packs."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .evidence import PACK_ARTIFACTS, EvidencePackError, load_complete_pack


def _files(pack_dir: Path) -> list[tuple[Path, Path]]:
    root = pack_dir.resolve()
    files: list[tuple[Path, Path]] = []
    for artifact in ("manifest.json", *PACK_ARTIFACTS):
        path = pack_dir / artifact
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                raise EvidencePackError("Evidence Pack contains an unsafe path")
            files.append((candidate, candidate.relative_to(pack_dir)))
    return files


def _portable_job_bytes(pack_dir: Path) -> bytes:
    """Remove machine-local retry state from the exported job view."""
    try:
        payload = json.loads((pack_dir / "job.json").read_text(encoding="utf-8"))
        source = json.loads((pack_dir / "source.json").read_text(encoding="utf-8"))
        job = dict(payload["job"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise EvidencePackError("missing or invalid job.json") from exc

    portable_url = str(source.get("url") or "")
    job["url"] = portable_url
    job["options"] = {}
    if isinstance(job.get("result"), dict):
        job["result"] = {**job["result"], "url": portable_url}
    payload = {**payload, "job": job}
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _artifact_bytes(pack_dir: Path, source: Path, relative: Path) -> bytes:
    if relative == Path("job.json"):
        return _portable_job_bytes(pack_dir)
    return source.read_bytes()


def export_zip(pack_dir: Path, destination: Path | None = None) -> Path:
    """Create a deterministic ZIP containing only canonical v1 artifacts."""
    load_complete_pack(pack_dir)
    destination = destination or pack_dir.with_suffix(".evidence.zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for directory in ("visual_states/all/", "visual_states/preview/"):
                info = zipfile.ZipInfo(
                    f"{pack_dir.name}/{directory}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.external_attr = 0o40755 << 16
                archive.writestr(info, b"")
            for source, relative in _files(pack_dir):
                info = zipfile.ZipInfo(
                    f"{pack_dir.name}/{relative.as_posix()}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, _artifact_bytes(pack_dir, source, relative))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def send_to_inbox(pack_dir: Path, inbox: Path) -> dict:
    """Copy a pack atomically, publishing its manifest as the final file."""
    manifest = load_complete_pack(pack_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / pack_dir.name
    if destination.exists():
        existing = load_complete_pack(destination)
        if existing["source"] == manifest["source"]:
            return {"status": "already_present", "destination": str(destination)}
        raise EvidencePackError("Inbox destination already exists for another source")

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{pack_dir.name}.", suffix=".tmp", dir=inbox)
    )
    try:
        for directory in ("visual_states/all", "visual_states/preview"):
            (temporary / directory).mkdir(parents=True, exist_ok=True)
        for source, relative in _files(pack_dir):
            if relative == Path("manifest.json"):
                continue
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == Path("job.json"):
                target.write_bytes(_portable_job_bytes(pack_dir))
            else:
                shutil.copy2(source, target)
        shutil.copy2(pack_dir / "manifest.json", temporary / "manifest.json")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"status": "sent", "destination": str(destination)}


def source_identity(pack_dir: Path) -> tuple[str, str]:
    """Read the stable platform/source pair used by cache and handoff callers."""
    try:
        source = json.loads((pack_dir / "source.json").read_text(encoding="utf-8"))
        return str(source["platform"]), str(source["source_id"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise EvidencePackError("missing or invalid source.json") from exc
