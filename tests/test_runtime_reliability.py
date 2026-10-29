"""Failure injection at the durable queue boundary, without network or models."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from clipmind.jobs import Job, JobStore
from clipmind.storage import JobPersistenceError, JobStorage
from tests.pack_fixture import make_complete_pack


class AtomicStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.storage = JobStorage(self.root)

    def test_replace_failure_preserves_previous_snapshot_and_cleans_temp(self):
        self.storage.save("job", Job("job", "https://example.com", "Before").record())
        before = (self.root / "job/job.json").read_bytes()
        with patch("clipmind.storage.os.replace", side_effect=PermissionError("private details")):
            with self.assertRaises(JobPersistenceError) as raised:
                self.storage.save("job", Job("job", "https://example.com", "After").record())
        self.assertNotIn("private details", str(raised.exception))
        self.assertEqual((self.root / "job/job.json").read_bytes(), before)
        self.assertEqual(list((self.root / "job").glob("*.tmp")), [])

    def test_serialization_failure_never_replaces_valid_state(self):
        record = Job("job", "https://example.com", "Before").record()
        self.storage.save("job", record)
        before = (self.root / "job/job.json").read_bytes()
        for bad in (float("nan"), object()):
            with self.assertRaises(JobPersistenceError):
                self.storage.save("job", {**record, "progress": bad})
            self.assertEqual((self.root / "job/job.json").read_bytes(), before)

    def test_concurrent_writers_use_separate_temporaries_and_commit_whole_records(self):
        barrier = threading.Barrier(2)
        replace = os.replace
        temporary_names = []
        visited = set()
        name_lock = threading.Lock()

        def simultaneous_replace(source, destination):
            with name_lock:
                first_attempt = source.name not in visited
                visited.add(source.name)
                if first_attempt:
                    temporary_names.append(source.name)
            if first_attempt:
                barrier.wait(timeout=5)
            return replace(source, destination)

        with patch("clipmind.storage.os.replace", side_effect=simultaneous_replace):
            with ThreadPoolExecutor(max_workers=2) as pool:
                writes = [pool.submit(self.storage.save, "job", Job("job", "url", title).record())
                          for title in ("First", "Second")]
                for write in writes:
                    write.result(timeout=10)
        self.assertEqual(len(set(temporary_names)), 2)
        self.assertIn(self.storage.load()[0]["title"], {"First", "Second"})
        self.assertEqual(list((self.root / "job").glob("*.tmp")), [])

    def test_bad_unicode_and_invalid_types_do_not_disable_other_library_entries(self):
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "job.json").write_bytes(b"\xff\xfe\x00")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "job.json").write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
        huge = self.root / "huge"
        huge.mkdir()
        (huge / "job.json").write_text(json.dumps({"version": 1, "job": {
            "id": "huge", "url": "url", "title": "bad", "created_at": 10**400
        }}), encoding="utf-8")
        self.storage.save("good", Job("good", "https://example.com", "Good", status="error").record())
        (self.root / "shape").mkdir()
        (self.root / "shape/job.json").write_text(json.dumps({
            "version": 1, "job": {"id": "shape", "url": "url", "title": "bad", "created_at": []}
        }), encoding="utf-8")
        store = JobStore(self.root)
        self.assertEqual(set(store.jobs), {"good"})
        self.assertEqual((bad / "job.json").read_bytes(), b"\xff\xfe\x00")

    def test_corrupt_job_record_recovers_read_only_from_canonical_pack(self):
        pack = make_complete_pack(self.root)
        (pack / "job.json").write_bytes(b"\xff")
        store = JobStore(self.root)
        job = store.jobs[pack.name]
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result["visual_preview"][0]["canonical_file"],
                         "visual_states/all/00-02-00001.jpg")
        self.assertEqual(job.result["visual_preview"][0]["text"], "向量数据库")
        self.assertEqual((pack / "job.json").read_bytes(), b"\xff")

    def test_broken_modern_pack_is_not_promoted_from_stale_legacy_metadata(self):
        pack = make_complete_pack(self.root)
        (pack / "job.json").write_bytes(b"\xff")
        (pack / "manifest.json").write_text("[]", encoding="utf-8")
        (pack / "note.md").write_text("stale note", encoding="utf-8")
        (pack / "metadata.json").write_text('{"title":"stale"}', encoding="utf-8")
        self.assertEqual(JobStore(self.root).jobs, {})

    def test_external_link_is_not_loaded_or_written(self):
        external = self.root / "outside"
        external.mkdir()
        library = self.root / "library"
        library.mkdir()
        try:
            (library / "linked").symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        self.assertEqual(JobStorage(library).load(), [])
        with self.assertRaises(JobPersistenceError):
            JobStorage(library).save("linked", Job("linked", "url", "No").record())
        self.assertEqual(list(external.iterdir()), [])


class RuntimeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    async def test_crash_after_manifest_recovers_without_download_and_cleans_only_temp(self):
        pack = make_complete_pack(self.root, name="published")
        storage = JobStorage(self.root)
        storage.save(pack.name, Job(pack.name, "https://youtu.be/video-1", "Original",
                                   status="running", stage="writing", progress=.95).record())
        (pack / "samples").mkdir()
        (pack / "samples/temp.jpg").write_bytes(b"temporary")
        (pack / "audio.wav").write_bytes(b"temporary")
        canonical = {p.relative_to(pack): p.read_bytes() for p in pack.rglob("*")
                     if p.is_file() and p.name not in {"job.json", "audio.wav", "temp.jpg"}}
        store = JobStore(self.root)
        with patch("clipmind.jobs.process") as process:
            store.start()
            await store.close()
        process.assert_not_called()
        self.assertEqual(store.jobs[pack.name].status, "done")
        self.assertEqual(store.jobs[pack.name].result["visual_preview"][0]["text"], "向量数据库")
        self.assertFalse((pack / "audio.wav").exists())
        self.assertFalse((pack / "samples").exists())
        self.assertEqual(canonical, {path: (pack / path).read_bytes() for path in canonical})
        self.assertEqual(JobStore(self.root).jobs[pack.name].status, "done")

    async def test_missing_required_artifact_is_interrupted_not_recovered_done(self):
        pack = make_complete_pack(self.root)
        (pack / "transcript.md").unlink()
        JobStorage(self.root).save(pack.name, Job(pack.name, "url", "Incomplete", status="running").record())
        store = JobStore(self.root)
        store.start()
        await store.close()
        self.assertEqual(store.jobs[pack.name].status, "interrupted")

    async def test_running_snapshot_failure_never_enters_pipeline_or_hangs_ui(self):
        store = JobStore(self.root)
        queue = store.subscribe()
        save = store.storage.save

        def full_disk(job_id, record):
            if record["status"] != "queued":
                raise JobPersistenceError("internal private pathname")
            save(job_id, record)

        with patch.object(store.storage, "save", side_effect=full_disk), patch("clipmind.jobs.process") as process:
            job = store.submit("https://example.com", "Test")
            await asyncio.wait_for(asyncio.gather(*list(store._tasks)), timeout=2)
        process.assert_not_called()
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["error_code"], "job_storage_unavailable")
        self.assertIn("disk space", events[-1]["error_action"])
        self.assertNotIn("private", json.dumps(events[-1]))
        self.assertEqual(JobStorage(self.root).load()[0]["status"], "queued")
        await store.close()

    async def test_final_snapshot_failure_still_notifies_completion_and_recovery_warning(self):
        store = JobStore(self.root)
        queue = store.subscribe()
        save = store.storage.save

        def final_write_failure(job_id, record):
            if record["status"] == "done":
                raise JobPersistenceError("disk full")
            save(job_id, record)

        async def completed(*args, **kwargs):
            return {"title": "Finished"}

        with patch.object(store.storage, "save", side_effect=final_write_failure), patch("clipmind.jobs.process", new=completed):
            job = store.submit("https://example.com", "Test")
            await asyncio.wait_for(asyncio.gather(*list(store._tasks)), timeout=2)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        self.assertEqual(events[-1]["status"], "done")
        self.assertEqual(events[-1]["persistence_warning"]["code"], "job_storage_unavailable")
        self.assertEqual(JobStorage(self.root).load()[0]["status"], "running")
        await store.close()

    async def test_initial_submission_failure_creates_no_worker(self):
        store = JobStore(self.root)
        with patch.object(store.storage, "save", side_effect=JobPersistenceError("disk full")):
            with self.assertRaises(JobPersistenceError):
                store.submit("https://example.com", "Test")
        self.assertEqual(store.jobs, {})
        self.assertEqual(store._tasks, set())
        await store.close()

    async def test_closed_store_rejects_new_jobs(self):
        store = JobStore(self.root)
        await store.close()
        with self.assertRaises(RuntimeError):
            store.submit("https://example.com", "Test")


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "PowerShell 7 on Windows required")
class WindowsLaunchTests(unittest.TestCase):
    def test_existing_instance_returns_before_doctor_or_second_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / ".venv/Scripts").mkdir(parents=True)
            (root / ".venv/Scripts/clipmind.exe").write_bytes(b"must never execute")
            source = Path(__file__).resolve().parents[1] / "scripts/clipmind_windows.ps1"
            shutil.copyfile(source, root / "scripts/clipmind_windows.ps1")
            runner = root / "check.ps1"
            runner.write_text("""
function Invoke-RestMethod { return @{ info = @{ title = 'ClipMind' } } }
function Get-NetTCPConnection { throw 'port probe should not run after identity confirmation' }
& (Join-Path $PSScriptRoot 'scripts/clipmind_windows.ps1') serve
""", encoding="utf-8")
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(runner)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already running", result.stdout)


if __name__ == "__main__":
    unittest.main()
