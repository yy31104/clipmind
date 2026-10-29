"""A damaged, rebuildable cache must not make the evidence library disappear."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clipmind.index import EvidenceIndex
from clipmind.jobs import Job, JobStore
from clipmind.storage import JobStorage
from tests.pack_fixture import make_complete_pack


class IndexRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / ".evidence-index.sqlite3"

    async def test_corrupt_cache_is_preserved_and_library_startup_rebuilds_search(self):
        pack = make_complete_pack(self.root)
        JobStorage(self.root).save(pack.name, Job(pack.name, "https://youtu.be/video-1", "Original",
                                                status="done", result={"id": "video-1"}).record())
        original_pack = {p.relative_to(pack): p.read_bytes() for p in pack.rglob("*") if p.is_file()}
        damaged = b"damaged SQLite cache: all evidence is elsewhere"
        self.path.write_bytes(damaged)
        store = JobStore(self.root)
        with patch("clipmind.jobs.process") as process:
            store.start()
            results = store.search("vector")
            await store.close()
        process.assert_not_called()
        self.assertEqual(results[0]["job_id"], pack.name)
        self.assertEqual(store.jobs[pack.name].status, "done")
        backup = store.index.recovery_directory
        self.assertIsNotNone(backup)
        self.assertEqual((backup / self.path.name).read_bytes(), damaged)
        self.assertEqual(original_pack, {p: (pack / p).read_bytes() for p in original_pack})
        self.assertIsNone(EvidenceIndex(self.path).recovery_directory)

    async def test_foreign_header_sidecars_are_preserved_before_sqlite_opens(self):
        contents = {suffix: f"recover me {suffix}".encode() for suffix in ("", "-wal", "-shm", "-journal")}
        for suffix, value in contents.items():
            Path(str(self.path) + suffix).write_bytes(value)
        index = EvidenceIndex(self.path)
        for suffix, value in contents.items():
            self.assertEqual((index.recovery_directory / (self.path.name + suffix)).read_bytes(), value)
        self.assertEqual(index.search("nothing"), [])

    async def test_corrupt_sqlite_page_is_detected_without_requiring_foreign_header(self):
        damaged = b"SQLite format 3\x00" + b"\x00" * 4096
        self.path.write_bytes(damaged)
        index = EvidenceIndex(self.path)
        self.assertEqual((index.recovery_directory / self.path.name).read_bytes(), damaged)
        self.assertEqual(index.search("anything"), [])

    async def test_locked_readonly_full_or_bad_migration_do_not_quarantine_valid_cache(self):
        EvidenceIndex(self.path)
        before = self.path.read_bytes()
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_READONLY,
                     sqlite3.SQLITE_FULL, sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_ERROR):
            with self.subTest(code=code):
                failure = sqlite3.OperationalError("diagnostic")
                failure.sqlite_errorcode = code
                with patch.object(EvidenceIndex, "_initialize", side_effect=failure):
                    with self.assertRaises(sqlite3.OperationalError):
                        EvidenceIndex(self.path)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(list(self.root.glob(".index-recovery-*")), [])

    async def test_read_permission_failure_does_not_touch_cache(self):
        self.path.write_bytes(b"corrupt but unreadable")
        with patch.object(Path, "open", side_effect=PermissionError("readonly folder")):
            with self.assertRaises(PermissionError):
                EvidenceIndex(self.path)
        self.assertEqual(self.path.read_bytes(), b"corrupt but unreadable")
        self.assertEqual(list(self.root.glob(".index-recovery-*")), [])

    async def test_failed_sidecar_move_restores_cache_and_does_not_initialize(self):
        contents = {suffix: f"recoverable {suffix}".encode() for suffix in ("", "-wal", "-shm")}
        for suffix, value in contents.items():
            Path(str(self.path) + suffix).write_bytes(value)
        rename = Path.rename

        def lock_wal(source, target):
            if source.name.endswith("-wal"):
                raise PermissionError("locked WAL")
            return rename(source, target)

        with patch.object(Path, "rename", lock_wal), patch.object(EvidenceIndex, "_initialize") as initialize:
            with self.assertRaises(PermissionError):
                EvidenceIndex(self.path)
        initialize.assert_not_called()
        for suffix, value in contents.items():
            self.assertEqual(Path(str(self.path) + suffix).read_bytes(), value)
        self.assertEqual(list(self.root.glob(".index-recovery-*")), [])

    async def test_empty_new_database_is_not_quarantined(self):
        self.path.touch()
        index = EvidenceIndex(self.path)
        self.assertIsNone(index.recovery_directory)
        self.assertEqual(index.search("nothing"), [])


if __name__ == "__main__":
    unittest.main()
