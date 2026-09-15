"""External tools that stop when the job that started them stops.

Cancelling a coroutine does not stop a child process: ``communicate()`` gives
up waiting and the child keeps running. For acquisition and media work that
means a download or decode still writing into a job directory while the
cleanup that cancellation triggered is removing it. Tools also spawn their own
children -- yt-dlp hands merging to FFmpeg -- so the whole process group is
stopped, not only the process we started.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path


def kill(proc: asyncio.subprocess.Process) -> None:
    """Stop a child started in its own session, together with its children."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except PermissionError:
        # Some macOS execution policies deny signalling a process group
        # that has already exited while its buffered output is draining.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass


async def run(args: list[str], *, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
    """Run to completion, or stop the child before cancellation propagates.

    The child gets its own session so its tools can be stopped as a group, and
    no stdin: nothing here is interactive, and FFmpeg otherwise reads the
    terminal for keyboard commands.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=os.name == "posix",
    )
    try:
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        kill(proc)
        # Draining, not just waiting: a full pipe can hide end-of-file. Being
        # cancelled again must not cut this short, or the caller's cleanup
        # races a process that is still writing.
        drain = asyncio.ensure_future(proc.communicate())
        while not drain.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(drain)
        raise
    return proc.returncode or 0, out, err
