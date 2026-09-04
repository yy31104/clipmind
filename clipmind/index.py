"""Small durable search index derived entirely from complete Evidence Packs."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path

from .evidence import EvidencePackError, load_complete_pack


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class EvidenceIndex:
    """SQLite is a rebuildable cache; Evidence Pack files remain canonical."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
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
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
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

    def sync(self, job_id: str, workdir: Path) -> bool:
        """Index one complete pack. Returns False when no work was needed."""
        try:
            load_complete_pack(workdir)
            manifest = workdir / "manifest.json"
            stat = manifest.stat()
            fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
            source = _json(workdir / "source.json")
            transcript = _jsonl(workdir / "transcript.jsonl")
            ocr = _jsonl(workdir / "ocr.jsonl")
        except (EvidencePackError, OSError, json.JSONDecodeError, TypeError):
            return False

        with self._database() as database:
            current = database.execute(
                "SELECT fingerprint FROM packs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if current and current["fingerprint"] == fingerprint:
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
                )
                for row in transcript
                if row.get("text")
            ] + [
                (
                    job_id,
                    "ocr",
                    row.get("visual_state_ref") or row.get("id"),
                    row.get("timestamp"),
                    str(row.get("text") or ""),
                )
                for row in ocr
                if row.get("text")
            ]
            database.executemany(
                "INSERT INTO documents(job_id, kind, ref, timestamp, text) "
                "VALUES (?, ?, ?, ?, ?)",
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
                SELECT d.job_id, d.kind, d.ref, d.timestamp, d.text,
                       p.title, p.platform, p.source_id, p.source_url,
                       p.updated_ns
                FROM documents d
                JOIN packs p ON p.job_id = d.job_id
                WHERE instr(lower(d.text), ?) > 0
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
                    }
                )
        return sorted(
            grouped.values(),
            key=lambda item: (-item["score"], item["title"].casefold()),
        )[: max(1, min(limit, 100))]
