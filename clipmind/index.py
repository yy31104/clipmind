"""Small durable search index derived entirely from complete Evidence Packs."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import logging
import math
import sqlite3
import uuid
from pathlib import Path

from .evidence import EvidencePackError, load_complete_pack


logger = logging.getLogger(__name__)


def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
    # Lock contention, permission errors, disk-full, or a migration bug are
    # not evidence of corruption and must never trigger file replacement.
    code = getattr(exc, "sqlite_errorcode", None)
    return type(code) is int and code & 0xff in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _search_context(record: dict, *, omitted: bool = False) -> dict:
    """Keep image provenance without promising that omitted images exist."""
    context = {}
    position = record.get("source_image_index")
    if type(position) is int and position >= 0:
        context.update(source_image_index=position, time_basis="image_order",
                       timestamp_available=False)
    visual_index = record.get("visual_state_index")
    if type(visual_index) is int and visual_index >= 0:
        context["visual_state_index"] = visual_index
    motion_time = record.get("source_motion_timestamp")
    if type(motion_time) in (int, float) and math.isfinite(motion_time) and motion_time >= 0:
        context["source_motion_timestamp"] = motion_time
    if omitted:
        context.update(omitted_visual=True, visual_available=False)
        if isinstance(record.get("id"), str):
            context["source_visual_ref"] = record["id"]
    return context


class EvidenceIndex:
    """SQLite is a rebuildable cache; Evidence Pack files remain canonical."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.recovery_directory: Path | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise OSError("Search index must not be a symbolic link")
        # Detect obviously foreign/truncated bytes before SQLite can inspect
        # or clean any sidecars. An empty file is a valid new SQLite database.
        if path.is_file():
            with path.open("rb") as handle:
                header = handle.read(16)
            if header and header != b"SQLite format 3\x00":
                self._quarantine_corrupt()
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            if self.recovery_directory is not None or not _is_corruption(exc):
                raise
            self._quarantine_corrupt()
            self._initialize()

    def _quarantine_corrupt(self) -> None:
        """Preserve the derived cache and its sidecars before starting fresh."""
        sources = [Path(str(self.path) + suffix) for suffix in ("", "-wal", "-shm", "-journal")]
        existing = [path for path in sources if path.exists()]
        if not existing or any(not path.is_file() or path.is_symlink() for path in existing):
            raise OSError("Search index recovery requires ordinary owned cache files")
        directory = self.path.parent / f".index-recovery-{uuid.uuid4().hex}"
        directory.mkdir()
        moved = []
        try:
            for source in existing:
                destination = directory / source.name
                source.rename(destination)
                moved.append((source, destination))
        except OSError:
            # If any handle is locked, restore what can be restored and leave
            # all remaining bytes recoverable. Never initialize over a partly
            # moved cache or overwrite a concurrent replacement.
            for source, destination in reversed(moved):
                if not source.exists():
                    try:
                        destination.rename(source)
                    except OSError:
                        pass
            try:
                directory.rmdir()
            except OSError:
                pass
            raise
        self.recovery_directory = directory
        logger.warning("Preserved a corrupt derived search cache; rebuilding from complete Evidence Packs")

    def _initialize(self) -> None:
        with self._database() as database:
            database.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS packs (
                    job_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    title TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    updated_ns INTEGER NOT NULL DEFAULT 0,
                    source_url TEXT
                );
                CREATE TABLE IF NOT EXISTS documents (
                    job_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    ref TEXT,
                    timestamp REAL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    text TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES packs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS documents_job_id ON documents(job_id);
                """
            )
            columns = {
                row["name"]
                for row in database.execute("PRAGMA table_info(packs)").fetchall()
            }
            migrated = False
            document_columns = {
                row["name"]
                for row in database.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "context_json" not in document_columns:
                database.execute("ALTER TABLE documents ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'")
                migrated = True
            if "source_id" not in columns:
                database.execute(
                    "ALTER TABLE packs ADD COLUMN source_id TEXT NOT NULL DEFAULT ''"
                )
                migrated = True
            if "updated_ns" not in columns:
                database.execute(
                    "ALTER TABLE packs ADD COLUMN updated_ns INTEGER NOT NULL DEFAULT 0"
                )
                migrated = True
            if migrated:
                # Existing rows must be re-read from their canonical source.json.
                database.execute("UPDATE packs SET fingerprint = ''")

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=5)
        try:
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA foreign_keys=ON")
            # SQLite's built-in lower() only folds ASCII; use the same Unicode
            # rules as query normalization and title matching, without rewriting
            # the canonical text or requiring an index migration.
            database.create_function("clipmind_casefold", 1,
                                     lambda text: str(text or "").casefold(),
                                     deterministic=True)
        except BaseException:
            database.close()
            raise
        return database

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back the operation, then always release its handle."""
        database = self._connect()
        try:
            with database:
                yield database
        finally:
            database.close()

    def remove(self, job_id: str) -> None:
        with self._database() as database:
            database.execute("DELETE FROM packs WHERE job_id = ?", (job_id,))

    def sync(self, job_id: str, workdir: Path) -> bool:
        """Index one complete pack. Returns False when no work was needed."""
        try:
            completion = load_complete_pack(workdir)
            manifest = workdir / "manifest.json"
            stat = manifest.stat()
            fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
        except (EvidencePackError, OSError, json.JSONDecodeError, TypeError):
            return False

        with self._database() as database:
            current = database.execute(
                "SELECT fingerprint FROM packs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if current and current["fingerprint"] == fingerprint:
                return False
            # Search asks every complete pack to sync. The manifest is the
            # writer's completion marker, so unchanged packs can use the index
            # without re-reading potentially large transcript/OCR files.
            try:
                source = _json(workdir / "source.json")
                transcript = _jsonl(workdir / "transcript.jsonl")
                ocr = _jsonl(workdir / "ocr.jsonl")
                compact = (completion.get("configuration") or {}).get("compact") or {}
                omitted_path = workdir / "omitted_visual_text.jsonl"
                omitted = (_jsonl(omitted_path)
                           if compact.get("profile") == "compact-reading-v1" and omitted_path.is_file()
                           else [])
            except (OSError, json.JSONDecodeError, TypeError):
                return False
            database.execute("DELETE FROM packs WHERE job_id = ?", (job_id,))
            database.execute(
                "INSERT INTO packs(job_id, fingerprint, title, platform, source_id, "
                "updated_ns, source_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    fingerprint,
                    str(source.get("title") or job_id),
                    str(source.get("platform") or "unknown"),
                    str(source.get("source_id") or ""),
                    stat.st_mtime_ns,
                    source.get("url"),
                ),
            )
            documents = [
                (
                    job_id,
                    "transcript",
                    row.get("id"),
                    row.get("start"),
                    str(row.get("text") or ""),
                    "{}",
                )
                for row in transcript
                if row.get("text")
            ] + [
                (
                    job_id,
                    "ocr",
                    row.get("visual_state_ref") or row.get("id"),
                    None if row.get("timestamp_available") is False else row.get("timestamp"),
                    str(row.get("text") or ""),
                    json.dumps(_search_context(row), ensure_ascii=False),
                )
                for row in ocr
                if row.get("text")
            ] + [
                (job_id, "ocr", None, None, str(row.get("text") or ""),
                 json.dumps(_search_context(row, omitted=True), ensure_ascii=False))
                for row in omitted if row.get("text")
            ]
            database.executemany(
                "INSERT INTO documents(job_id, kind, ref, timestamp, text, context_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                documents,
            )
        return True

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        needle = query.strip().casefold()
        if not needle:
            return []
        with self._database() as database:
            packs = database.execute(
                """
                SELECT job_id, title, platform, source_id, source_url, updated_ns
                FROM packs
                ORDER BY updated_ns DESC, job_id DESC
                """
            ).fetchall()
            rows = database.execute(
                """
                SELECT d.job_id, d.kind, d.ref, d.timestamp, d.text, d.context_json,
                       p.title, p.platform, p.source_id, p.source_url,
                       p.updated_ns
                FROM documents d
                JOIN packs p ON p.job_id = d.job_id
                WHERE instr(clipmind_casefold(d.text), ?) > 0
                ORDER BY p.updated_ns DESC, d.timestamp
                """,
                (needle,),
            ).fetchall()

        newest: dict[str, sqlite3.Row] = {}
        for pack in packs:
            source_key = (
                f'{pack["platform"]}:{pack["source_id"]}'
                if pack["source_id"]
                else f'job:{pack["job_id"]}'
            )
            newest.setdefault(source_key, pack)

        grouped: dict[str, dict] = {}
        selected_jobs: dict[str, str] = {}
        for source_key, pack in newest.items():
            selected_jobs[pack["job_id"]] = source_key
            if needle not in pack["title"].casefold():
                continue
            grouped[source_key] = {
                "job_id": pack["job_id"],
                "title": pack["title"],
                "platform": pack["platform"],
                "source_url": pack["source_url"],
                "score": 5,
                "hits": [],
            }

        for row in rows:
            source_key = selected_jobs.get(row["job_id"])
            if source_key is None:
                continue
            result = grouped.setdefault(
                source_key,
                {
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "platform": row["platform"],
                    "source_url": row["source_url"],
                    "score": 0,
                    "hits": [],
                },
            )
            occurrences = max(row["text"].casefold().count(needle), 1)
            result["score"] += occurrences
            if len(result["hits"]) < 5:
                result["hits"].append(
                    {
                        "kind": row["kind"],
                        "ref": row["ref"],
                        "timestamp": row["timestamp"],
                        "text": row["text"],
                        **json.loads(row["context_json"]),
                    }
                )
        return sorted(
            grouped.values(),
            key=lambda item: (-item["score"], item["title"].casefold()),
        )[: max(1, min(limit, 100))]
