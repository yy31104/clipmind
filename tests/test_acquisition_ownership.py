"""Cleanup must work from the job directory alone.

Completion, failure and cancellation all still hold the ``MediaAsset``. Restart
recovery does not -- the process that acquired the media is gone -- so these
tests drive the shipped entry points (``pipeline.cleanup_temporary`` and
``JobStore.start``) rather than restating the rules they encode.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind import acquisition, fetch, pipeline
from clipmind.jobs import JobStore
from clipmind.sources import adapter_for


class AcquisitionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tempdir.name) / "job"
        self.workdir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_ownership_is_recorded_before_any_media_is_written(self) -> None:
        root = acquisition.open_workspace(self.workdir, strategy="chrome cookies")

        ledger = acquisition.load(self.workdir)
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger["root"], acquisition.ROOT_NAME)
        self.assertEqual(ledger["strategy"], "chrome cookies")
        self.assertTrue(root.is_dir())

    def test_purge_removes_artifacts_whatever_the_strategy_named_them(self) -> None:
        root = acquisition.open_workspace(self.workdir, strategy="browser capture")
        (root / "browser-capture.webm").write_bytes(b"captured media")
        (root / "capture.log").write_text("noise", encoding="utf-8")

        acquisition.purge(self.workdir)

        self.assertFalse(acquisition.workspace(self.workdir).exists())

    def test_purge_is_safe_with_no_acquisition_and_runs_twice(self) -> None:
        acquisition.purge(self.workdir)
        acquisition.open_workspace(self.workdir, strategy="pending")
        acquisition.purge(self.workdir)
        acquisition.purge(self.workdir)

        self.assertFalse(acquisition.workspace(self.workdir).exists())

    def test_no_shape_of_corrupt_ledger_can_block_cleanup(self) -> None:
        corruptions = {
            "not json": b"{ not json",
            "not utf-8": b'{"version": 1, "s": "\xff\xfe"}',
            "wrong shape": b'{"version": 1, "external_artifacts": 42}',
            "not an object": b"[1, 2, 3]",
        }
        for label, payload in corruptions.items():
            with self.subTest(ledger=label):
                root = acquisition.open_workspace(self.workdir, strategy="pending")
                (root / "browser-capture.webm").write_bytes(b"private media")
                acquisition.ledger_path(self.workdir).write_bytes(payload)

                acquisition.purge(self.workdir)

                self.assertFalse(acquisition.workspace(self.workdir).exists())
                self.assertEqual(acquisition.leftovers(self.workdir), [])

    def test_purge_ignores_tampered_ledger_entries(self) -> None:
        outside = Path(self.tempdir.name) / "the-users-own-video.mp4"
        outside.write_bytes(b"belongs to the user")
        (self.workdir / "source.json").write_text("{}", encoding="utf-8")
        (self.workdir / "manifest.json").write_text("{}", encoding="utf-8")
        acquisition.open_workspace(self.workdir, strategy="pending")
        acquisition.ledger_path(self.workdir).write_text(
            json.dumps(
                {
                    "version": 1,
                    "root": acquisition.ROOT_NAME,
                    "external_artifacts": [
                        "../the-users-own-video.mp4",
                        ".",
                        "source.json",
                        "manifest.json",
                    ],
                }
            ),
            encoding="utf-8",
        )

        acquisition.purge(self.workdir)

        self.assertTrue(outside.exists())
        self.assertTrue(self.workdir.is_dir())
        self.assertTrue((self.workdir / "source.json").exists())
        self.assertTrue((self.workdir / "manifest.json").exists())

    def test_the_job_directory_and_final_artifacts_can_never_be_owned(self) -> None:
        (self.workdir / "source.json").write_text("{}", encoding="utf-8")
        (self.workdir / "visual_states").mkdir()
        acquisition.open_workspace(self.workdir, strategy="pending")

        for candidate in (
            self.workdir,
            self.workdir / "source.json",
            self.workdir / "manifest.json",
            self.workdir / "visual_states",
            self.workdir / "note.md",
            Path(self.tempdir.name) / "elsewhere.mp4",
        ):
            with self.subTest(path=candidate.name):
                with self.assertRaises(ValueError):
                    acquisition.record_external(self.workdir, candidate)

        acquisition.purge(self.workdir)
        self.assertTrue(self.workdir.is_dir())
        self.assertTrue((self.workdir / "source.json").exists())

    def test_a_failed_deletion_stays_in_the_ledger_and_is_retried(self) -> None:
        acquisition.open_workspace(self.workdir, strategy="pending")
        stray = self.workdir / "acquisition-fragment.part"
        stray.write_bytes(b"partial download")
        acquisition.record_external(self.workdir, stray)

        real_unlink = Path.unlink

        def failing_unlink(target, *args, **kwargs):
            if target.name == "acquisition-fragment.part":
                raise PermissionError("injected")
            return real_unlink(target, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            acquisition.purge(self.workdir)

        self.assertTrue(stray.exists())
        ledger = acquisition.load(self.workdir)
        self.assertIsNotNone(ledger)
        self.assertIn("acquisition-fragment.part", ledger["external_artifacts"])

        acquisition.purge(self.workdir)

        self.assertFalse(stray.exists())
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    def test_a_symlinked_owned_directory_is_refused(self) -> None:
        outside = Path(self.tempdir.name) / "outside"
        outside.mkdir()
        acquisition.workspace(self.workdir).symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ValueError):
            acquisition.open_workspace(self.workdir, strategy="pending")

        self.assertEqual(list(outside.iterdir()), [])

    def test_purge_removes_a_symlink_without_touching_its_target(self) -> None:
        outside = Path(self.tempdir.name) / "outside"
        outside.mkdir()
        kept = outside / "not-ours.mp4"
        kept.write_bytes(b"someone else's file")
        acquisition.workspace(self.workdir).symlink_to(outside, target_is_directory=True)

        acquisition.purge(self.workdir)

        self.assertFalse(acquisition.workspace(self.workdir).is_symlink())
        self.assertTrue(kept.exists())
        self.assertEqual(acquisition.leftovers(self.workdir), [])

    def test_an_owned_external_artifact_is_removed(self) -> None:
        acquisition.open_workspace(self.workdir, strategy="pending")
        stray = self.workdir / "acquisition-fragment.part"
        stray.write_bytes(b"partial download")

        acquisition.record_external(self.workdir, stray)
        acquisition.purge(self.workdir)

        self.assertFalse(stray.exists())
        self.assertEqual(acquisition.leftovers(self.workdir), [])


class CleanupContractTests(unittest.TestCase):
    """One contract, exercised through the shipped ``cleanup_temporary``."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tempdir.name) / "job"
        self.workdir.mkdir(parents=True)
        # Evidence Pack members that must survive every cleanup path.
        (self.workdir / "source.json").write_text('{"source_id":"s"}', encoding="utf-8")
        (self.workdir / "manifest.json").write_text("{}", encoding="utf-8")
        (self.workdir / "visual_states").mkdir()
        (self.workdir / "visual_states" / "canonical.jpg").write_bytes(b"evidence")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _acquire(self, name: str = "browser-capture.webm") -> Path:
        root = acquisition.open_workspace(self.workdir, strategy="browser capture")
        media = root / name
        media.write_bytes(b"temporary media")
        return media

    def test_cleanup_removes_acquired_media_and_keeps_the_evidence_pack(self) -> None:
        media = self._acquire()
        (self.workdir / "audio.wav").write_bytes(b"audio")
        (self.workdir / "samples").mkdir()
        (self.workdir / "samples" / "s_00001.jpg").write_bytes(b"candidate")

        pipeline.cleanup_temporary(self.workdir)

        self.assertFalse(media.exists())
        self.assertFalse(acquisition.workspace(self.workdir).exists())
        self.assertFalse((self.workdir / "audio.wav").exists())
        self.assertFalse((self.workdir / "samples").exists())
        self.assertTrue((self.workdir / "source.json").exists())
        self.assertTrue((self.workdir / "manifest.json").exists())
        self.assertTrue((self.workdir / "visual_states" / "canonical.jpg").exists())

    def test_keep_source_video_keeps_the_acquired_media(self) -> None:
        media = self._acquire()
        (self.workdir / "samples").mkdir()
        (self.workdir / "samples" / "s_00001.jpg").write_bytes(b"candidate")

        pipeline.cleanup_temporary(self.workdir, keep_source=True)

        self.assertTrue(media.exists())
        self.assertFalse((self.workdir / "samples").exists())

    def test_job_directories_from_before_this_change_are_still_cleaned(self) -> None:
        legacy_media = self.workdir / "source.mp4"
        legacy_media.write_bytes(b"media from an earlier release")
        (self.workdir / "audio.wav").write_bytes(b"audio")

        pipeline.cleanup_temporary(self.workdir)

        self.assertFalse(legacy_media.exists())
        self.assertFalse((self.workdir / "audio.wav").exists())
        self.assertTrue((self.workdir / "source.json").exists())


class LocalOriginalTests(unittest.IsolatedAsyncioTestCase):
    """The user's own file is never owned, so cleanup can never reach it."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workdir = self.root / "job"
        self.original = self.root / "lecture.mp4"
        self.original.write_bytes(b"the user's own recording")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_local_ingestion_copies_and_never_owns_the_original(self) -> None:
        adapter = adapter_for(str(self.original))
        asset = await fetch._fetch_local(str(self.original), self.workdir, adapter)

        self.assertNotEqual(asset.media_path.resolve(), self.original.resolve())
        self.assertTrue(
            asset.media_path.resolve().is_relative_to(
                acquisition.workspace(self.workdir).resolve()
            )
        )

        pipeline.cleanup_temporary(self.workdir)

        self.assertFalse(asset.media_path.exists())
        self.assertTrue(self.original.exists())
        self.assertEqual(self.original.read_bytes(), b"the user's own recording")


class RestartOwnershipTests(unittest.IsolatedAsyncioTestCase):
    """The path with no ``MediaAsset``: the process that acquired it is gone."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tempdir.name) / "out"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_restart_removes_media_a_filename_guess_would_miss(self) -> None:
        entered = asyncio.Event()

        async def interrupted_process(url, workdir, pools, report, **kwargs):
            workdir.mkdir(parents=True, exist_ok=True)
            root = acquisition.open_workspace(workdir, strategy="browser capture")
            (root / "browser-capture.webm").write_bytes(b"private media")
            (workdir / "note.md").write_text("published artifact", encoding="utf-8")
            entered.set()
            await asyncio.Event().wait()

        first = JobStore(self.out_dir)
        with patch("clipmind.jobs.process", new=interrupted_process):
            original = first.submit("https://v.douyin.com/running", "Running")
            await asyncio.wait_for(entered.wait(), timeout=1)
            await first.close()

        workdir = first.workdir(original.id)
        captured = acquisition.workspace(workdir) / "browser-capture.webm"
        self.assertTrue(captured.exists())
        # The point of the test: the legacy rule cannot see this file.
        self.assertEqual(list(workdir.glob("source.*")), [])

        async def must_not_run(url, workdir, pools, report, **kwargs):
            raise AssertionError("restart recovery must not reprocess the job")

        second = JobStore(self.out_dir)
        with patch("clipmind.jobs.process", new=must_not_run):
            second.start()
        recovered = second.jobs[original.id]
        await second.close()

        self.assertEqual(recovered.status, "interrupted")
        self.assertFalse(captured.exists())
        self.assertFalse(acquisition.workspace(workdir).exists())
        self.assertTrue((workdir / "note.md").exists())


if __name__ == "__main__":
    unittest.main()
