"""Small, deterministic on-disk storage for job state."""
from __future__ import annotations

import json
import os
from pathlib import Path


class JobStorage:
    """Persist one versioned ``job.json`` beside each job's final artifacts."""

    VERSION = 1

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def workdir(self, job_id: str) -> Path:
        return self.root / job_id

    def save(self, job_id: str, record: dict) -> None:
        workdir = self.workdir(job_id)
        workdir.mkdir(parents=True, exist_ok=True)
        target = workdir / "job.json"
        temporary = workdir / "job.json.tmp"
        payload = {"version": self.VERSION, "job": record}
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self) -> list[dict]:
        if not self.root.exists():
            return []
        records: list[dict] = []
        for workdir in sorted(self.root.iterdir()):
            if not workdir.is_dir():
                continue
            record = self._load_job(workdir) or self._load_legacy_done(workdir)
            if record is not None:
                # The containing directory owns the identity; persisted content
                # cannot redirect reads or later writes outside that directory.
                record["id"] = workdir.name
                records.append(record)
        return records

    def _load_job(self, workdir: Path) -> dict | None:
        path = workdir / "job.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != self.VERSION:
                return None
            record = payload.get("job")
            return dict(record) if isinstance(record, dict) else None
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def _load_legacy_done(self, workdir: Path) -> dict | None:
        """Expose notes produced before job persistence was introduced."""
        metadata_path = workdir / "metadata.json"
        if not metadata_path.exists() or not (workdir / "note.md").exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                return None
            timestamp = metadata_path.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            return None
        return {
            "id": workdir.name,
            "url": str(metadata.get("url") or ""),
            "title": str(metadata.get("title") or workdir.name),
            "status": "done",
            "stage": "done",
            "progress": 1.0,
            "note": "",
            "error": None,
            "result": metadata,
            "created_at": timestamp,
            "started_at": timestamp,
            "finished_at": timestamp,
        }
