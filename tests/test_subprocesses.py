"""Stopping a job stops the tools it started.

These run real processes. A mock can prove we called kill; only a process that
is actually gone proves nothing is still writing into the job directory.
"""

import asyncio
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind import fetch, media

posix_only = unittest.skipUnless(os.name == "posix", "process groups are POSIX")

# A child that starts a grandchild, reports both, then outlives any test.
SPAWNS_GRANDCHILD = """
import os, subprocess, sys, time
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(sys.argv[1] + ".tmp", "w") as handle:
    handle.write(f"{os.getpid()} {grandchild.pid}")
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
time.sleep(60)
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_for_file(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path.name} was never written")
        await asyncio.sleep(0.05)
    return path.read_text()


async def gone_within(pid: int, timeout: float = 5.0) -> bool:
    # A reparented grandchild is reaped by init, not by us, so allow a moment.
    deadline = time.monotonic() + timeout
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


class RunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_completion_reports_exit_code_and_output(self) -> None:
        # Bytes, not print(): Windows text streams turn "\n" into "\r\n", and the
        # point is that the output arrives exactly as the child wrote it.
        code, out, err = await fetch._run(
            [sys.executable, "-c",
             "import sys; sys.stdout.buffer.write(b'out\\n'); sys.stderr.buffer.write(b'err'); sys.exit(3)"]
        )
        self.assertEqual((code, out, err), (3, "out\n", "err"))

    @posix_only
    async def test_cancel_stops_the_child_and_its_children(self) -> None:
        pids = self.root / "pids"
        task = asyncio.create_task(
            fetch._run([sys.executable, "-c", SPAWNS_GRANDCHILD, str(pids)])
        )
        child, grandchild = map(int, (await wait_for_file(pids)).split())

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        # The direct child is reaped before cancellation reaches the caller.
        self.assertFalse(alive(child))
        self.assertTrue(await gone_within(grandchild))

    @posix_only
    async def test_repeated_cancel_still_waits_for_the_child(self) -> None:
        pids = self.root / "pids"
        task = asyncio.create_task(
            fetch._run([sys.executable, "-c", SPAWNS_GRANDCHILD, str(pids)])
        )
        child, grandchild = map(int, (await wait_for_file(pids)).split())

        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertFalse(alive(child))
        self.assertTrue(await gone_within(grandchild))


@posix_only
class FFmpegTests(unittest.IsolatedAsyncioTestCase):
    """The media stage runs the same boundary, checked through a stand-in FFmpeg."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def fake_ffmpeg(self, body: str):
        script = self.bin / "ffmpeg"
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return patch.dict(
            os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        )

    async def test_cancel_stops_ffmpeg(self) -> None:
        pid_file = self.root / "ffmpeg.pid"
        with self.fake_ffmpeg(f'echo $$ > "{pid_file}.tmp"; mv "{pid_file}.tmp" "{pid_file}"; exec sleep 60'):
            task = asyncio.create_task(media._ffmpeg(["-i", "input.mp4", "out.wav"]))
            pid = int((await wait_for_file(pid_file)).strip())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(alive(pid))

    async def test_failure_still_raises_media_error_with_stderr(self) -> None:
        with self.fake_ffmpeg('echo "input.mp4: Invalid data" >&2; exit 1'):
            with self.assertRaises(media.MediaError) as raised:
                await media._ffmpeg(["-i", "input.mp4", "out.wav"])
        self.assertIn("Invalid data", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
