"""Small, deterministic on-disk storage for job state."""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from pathlib import Path


logger = logging.getLogger(__name__)


class JobPersistenceError(RuntimeError):
    """A safe diagnostic that never embeds a local path or persisted content."""

    code = "job_storage_unavailable"
    user_message = "ClipMind could not save the task state."
    action = "Check available disk space and library write permissions before retrying."


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _replace_snapshot(temporary: Path, target: Path) -> None:
    # Windows readers/virus scanners can briefly deny replacement. Retry only
    # those sharing/access errors, for at most 150 ms; disk-full and other
    # failures remain immediate and never truncate the previous snapshot.
    for delay in (.01, .02, .04, .08, None):
        try:
            os.replace(temporary, target)
            return
        except PermissionError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32, 33} or delay is None:
                raise
            time.sleep(delay)


class JobStorage:
    """Persist one versioned ``job.json`` beside each job's final artifacts."""

    VERSION = 1

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def workdir(self, job_id: str) -> Path:
        if (not isinstance(job_id, str) or not job_id or job_id.startswith(".")
                or Path(job_id).name != job_id or any(c in job_id for c in ("/", "\\", ":"))):
            raise ValueError("invalid job id")
        return self.root / job_id

    def move_to_trash(self, job_id: str) -> Path:
        """Move only one owned job directory; never follow links or delete media."""
        if not job_id or job_id.startswith('.') or Path(job_id).name != job_id or '\\' in job_id:
            raise ValueError("invalid job id")
        source = self.workdir(job_id)
        if _linked(source) or not source.is_dir():
            raise ValueError("job directory is missing or unsafe")
        trash = self.root / '.trash'
        if _linked(trash):
            raise ValueError("trash directory is unsafe")
        trash.mkdir(exist_ok=True)
        batch = trash / uuid.uuid4().hex
        batch.mkdir()
        destination = batch / job_id
        source.rename(destination)
        return destination

    def save(self, job_id: str, record: dict) -> None:
        workdir = self.workdir(job_id)
        target = workdir / "job.json"
        # A failed/concurrent writer must not unlink another writer's temporary
        # state. The committed snapshot remains untouched until replacement.
        temporary = workdir / f".job-{uuid.uuid4().hex}.tmp"
        payload = {"version": self.VERSION, "job": record}
        try:
            if _linked(workdir):
                raise OSError("unsafe job directory")
            workdir.mkdir(parents=True, exist_ok=True)
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            _replace_snapshot(temporary, target)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise JobPersistenceError(JobPersistenceError.user_message) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # The original write error is actionable; stale private temp
                # files are ignored by readers and must not mask that error.
                logger.warning("Could not remove a temporary task-state file")

    def load(self) -> list[dict]:
        if not self.root.exists():
            return []
        records: list[dict] = []
        for workdir in sorted(self.root.iterdir()):
            if workdir.name.startswith('.') or _linked(workdir) or not workdir.is_dir():
                continue
            record = (self._load_job(workdir) or self.completed_record(workdir.name)
                      or self._load_legacy_done(workdir))
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
        except (OSError, UnicodeError, ValueError, AttributeError, RecursionError):
            return None

    def completed_record(self, job_id: str) -> dict | None:
        """Reconcile a crash after publication from canonical, validated files.

        Compatibility metadata may not have been written yet. Reconstruct only
        its display fields; do not change evidence, OCR, images or source files.
        """
        from .evidence import EvidencePackError, load_complete_pack
        from .quality import audit_pack

        workdir = self.workdir(job_id)
        if _linked(workdir):
            return None
        try:
            manifest = load_complete_pack(workdir)
            if not audit_pack(workdir, decode_images=False, check_job=False).get("passed"):
                return None
            source = json.loads((workdir / "source.json").read_text(encoding="utf-8"))
            ocr = [json.loads(line) for line in (workdir / "ocr.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            timeline = [json.loads(line) for line in (workdir / "visual_timeline.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            if not isinstance(source, dict) or not all(isinstance(row, dict) for row in (*ocr, *timeline)):
                return None
            text_by_id = {row.get("id"): row.get("text", "") for row in ocr}
            states, preview = [], []
            for row in timeline:
                state = {key: value for key, value in row.items() if key not in {
                    "id", "start", "end", "in_preview", "preview_file", "ocr_ref", "transcript_refs"}}
                state.update(timestamp=row.get("start", 0), text=text_by_id.get(row.get("ocr_ref"), ""))
                if row.get("time_basis") == "image_order":
                    index = row.get("source_image_index")
                    if type(index) is int and index >= 0:
                        state["clock"] = f"第 {index + 1} 张"
                        motion = row.get("source_motion_timestamp")
                        if type(motion) in (int, float) and math.isfinite(motion) and motion >= 0:
                            state["clock"] += f" · 动态 {motion:g} 秒"
                states.append(state)
                if row.get("in_preview") and row.get("preview_file"):
                    preview.append({**state, "file": row["preview_file"], "canonical_file": row["file"]})
            diagnostics = manifest.get("diagnostics") or {}
            counts = manifest.get("counts") or {}
            configuration = manifest.get("configuration") or {}
            metadata = {
                **source, "id": source.get("source_id"),
                "visual_states": states, "visual_preview": preview,
                "canonical_visual_state_count": len(states), "preview_frame_count": len(preview),
                "candidate_frame_count": counts.get("candidate_frames"),
                "stage_timings": manifest.get("timings") or {},
                "preflight": configuration.get("preflight"),
                "asr_error": diagnostics.get("asr_error"), "ocr_error": diagnostics.get("ocr_error"),
                "evidence_pack": {
                    "manifest": "manifest.json", "evidence": "evidence.md",
                    "schema": manifest["schema"], "completeness": manifest.get("completeness") or {},
                    "diagnostics": diagnostics,
                },
            }
            timestamp = (workdir / "manifest.json").stat().st_mtime
            return {
                "id": job_id, "url": str(source.get("url") or ""),
                "title": str(source.get("title") or job_id),
                "status": "done", "stage": "done", "progress": 1.0,
                "note": "complete pack recovered after application restart",
                "result": metadata, "created_at": timestamp, "finished_at": timestamp,
            }
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, EvidencePackError):
            return None

    def _load_legacy_done(self, workdir: Path) -> dict | None:
        """Expose notes produced before job persistence was introduced."""
        # A broken modern pack cannot be made complete by its stale legacy note.
        if (workdir / "manifest.json").exists():
            return None
        metadata_path = workdir / "metadata.json"
        if not metadata_path.exists() or not (workdir / "note.md").exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                return None
            timestamp = metadata_path.stat().st_mtime
        except (OSError, UnicodeError, ValueError, RecursionError):
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
