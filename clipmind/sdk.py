"""Stable Python interface for complete ClipMind Evidence Packs."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import OUT_DIR, Settings, settings
from .evidence import EvidencePackError, load_complete_pack
from .handoff import export_zip
from .index import EvidenceIndex
from .jobs import JobStore
from .links import extract_sources, guess_title
from .providers import ProviderBundle


class ClipMindError(RuntimeError):
    """Actionable application error exposed by the SDK."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "clipmind_error",
        action: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.action = action
        self.details = details


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidencePackError(f"missing or invalid {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidencePackError(f"{path.name} must contain an object")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        values = [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidencePackError(f"missing or invalid {path.name}") from exc
    if not all(isinstance(value, dict) for value in values):
        raise EvidencePackError(f"{path.name} must contain objects")
    return values


@dataclass(frozen=True)
class EvidencePack:
    """Read-only view of one validated, complete Evidence Pack."""

    root: Path
    manifest: dict

    @classmethod
    def open(cls, root: Path | str) -> EvidencePack:
        path = Path(root).expanduser().resolve()
        return cls(path, load_complete_pack(path))

    @property
    def id(self) -> str:
        return self.root.name

    @property
    def source(self) -> dict:
        return _read_json(self.root / "source.json")

    @property
    def transcript(self) -> list[dict]:
        return _read_jsonl(self.root / "transcript.jsonl")

    @property
    def ocr(self) -> list[dict]:
        return _read_jsonl(self.root / "ocr.jsonl")

    @property
    def visual_timeline(self) -> list[dict]:
        return _read_jsonl(self.root / "visual_timeline.jsonl")

    @property
    def evidence_markdown(self) -> str:
        return (self.root / "evidence.md").read_text(encoding="utf-8")

    def summary(self) -> dict:
        source = self.source
        return {
            "pack_id": self.id,
            "title": source.get("title") or self.id,
            "platform": source.get("platform") or "unknown",
            "source_id": source.get("source_id"),
            "source_url": source.get("url"),
            "duration": source.get("duration"),
            "schema": self.manifest.get("schema"),
            "counts": self.manifest.get("counts") or {},
            "completeness": self.manifest.get("completeness") or {},
        }

    def search(self, query: str, *, limit: int = 50) -> list[dict]:
        needle = query.strip().casefold()
        if not needle:
            return []
        hits: list[dict] = []
        title = str(self.source.get("title") or "")
        if needle in title.casefold():
            hits.append({"kind": "title", "timestamp": None, "text": title})
        for kind, records in (("transcript", self.transcript), ("ocr", self.ocr)):
            for record in records:
                text = str(record.get("text") or "")
                if needle not in text.casefold():
                    continue
                hits.append(
                    {
                        "kind": kind,
                        "ref": record.get("id") or record.get("visual_state_ref"),
                        "timestamp": record.get("start", record.get("timestamp")),
                        "text": text,
                    }
                )
                if len(hits) >= max(1, min(limit, 500)):
                    return hits
        return hits

    def frame(
        self,
        *,
        visual_state_id: str | None = None,
        timestamp: float | None = None,
        preview: bool = False,
    ) -> tuple[Path, dict]:
        timeline = self.visual_timeline
        record = None
        if visual_state_id:
            record = next(
                (item for item in timeline if item.get("id") == visual_state_id),
                None,
            )
        elif timestamp is not None and timeline:
            at = float(timestamp)
            record = next(
                (
                    item
                    for item in timeline
                    if float(item.get("start") or 0) <= at
                    < float(item.get("end") or item.get("start") or 0)
                ),
                None,
            )
            if record is None:
                record = min(
                    timeline,
                    key=lambda item: abs(float(item.get("start") or 0) - at),
                )
        if record is None:
            raise EvidencePackError("no matching visual state")
        relative = record.get("preview_file") if preview else record.get("file")
        if not relative:
            raise EvidencePackError("matching visual state has no requested image")
        path = (self.root / str(relative)).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise EvidencePackError("visual state points outside the Evidence Pack")
        return path, record

    def export(self, destination: Path | str | None = None) -> Path:
        target = Path(destination).expanduser() if destination is not None else None
        return export_zip(self.root, target)


class PackLibrary:
    """Discover and search complete packs under one local library root."""

    def __init__(self, root: Path | str = OUT_DIR) -> None:
        self.root = Path(root).expanduser().resolve()

    def list(self) -> list[EvidencePack]:
        if not self.root.exists():
            return []
        packs: list[EvidencePack] = []
        for path in self.root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                packs.append(EvidencePack.open(path))
            except EvidencePackError:
                continue
        return sorted(
            packs,
            key=lambda pack: (pack.root / "manifest.json").stat().st_mtime_ns,
            reverse=True,
        )

    def get(self, pack_id: str) -> EvidencePack:
        if not pack_id or Path(pack_id).name != pack_id:
            raise EvidencePackError("invalid pack id")
        path = (self.root / pack_id).resolve()
        if not path.is_relative_to(self.root):
            raise EvidencePackError("invalid pack id")
        return EvidencePack.open(path)

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        index = EvidenceIndex(self.root / ".evidence-index.sqlite3")
        for pack in self.list():
            index.sync(pack.id, pack.root)
        return index.search(query, limit=limit)


ProgressCallback = Callable[[dict], None]


class ClipMind:
    """High-level extraction client used by Python and agent integrations."""

    def __init__(
        self,
        out_dir: Path | str = OUT_DIR,
        *,
        config: Settings = settings,
        providers: ProviderBundle | None = None,
    ) -> None:
        self.out_dir = Path(out_dir).expanduser().resolve()
        self.config = config
        self.providers = providers
        self.library = PackLibrary(self.out_dir)

    async def analyze(
        self,
        text: str,
        *,
        reprocess: bool = False,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> list[EvidencePack]:
        sources = extract_sources(text)
        if not sources:
            raise ClipMindError(
                "No supported URL or local media source was found.",
                code="unsupported_source",
                action="Paste a supported URL or choose a local media file.",
            )
        store = JobStore(
            self.out_dir,
            config=self.config,
            providers=self.providers,
        )
        queue = store.subscribe()
        positions: dict[str, int] = {}
        completed: dict[int, EvidencePack] = {}
        failures: list[dict] = []
        pending: set[str] = set()
        try:
            for position, source in enumerate(sources):
                cached = None if reprocess else store.reusable(source)
                if cached is not None:
                    completed[position] = self.library.get(cached.id)
                    continue
                job = store.submit(
                    source,
                    guess_title(text, source) or source,
                    options={"force": force},
                )
                positions[job.id] = position
                pending.add(job.id)

            while pending:
                event = await queue.get()
                job_id = event.get("id")
                if job_id not in pending:
                    continue
                if progress is not None:
                    progress(dict(event))
                if event.get("status") == "done":
                    completed[positions[job_id]] = self.library.get(job_id)
                    pending.remove(job_id)
                elif event.get("status") in {"error", "interrupted"}:
                    failures.append(event)
                    pending.remove(job_id)
        finally:
            store.unsubscribe(queue)
            await store.close()

        if failures:
            failure = failures[0]
            message = str(failure.get("error") or failure.get("note") or "Processing failed.")
            if len(failures) > 1:
                message = f"{len(failures)} sources failed. First error: {message}"
            raise ClipMindError(
                message,
                code=str(failure.get("error_code") or "processing_failed"),
                action=failure.get("error_action"),
                details=failure.get("error_details"),
            )
        return [completed[position] for position in sorted(completed)]

    def analyze_sync(self, text: str, **options) -> list[EvidencePack]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.analyze(text, **options))
        raise RuntimeError("analyze_sync cannot run inside an active event loop; await analyze()")
