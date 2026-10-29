"""Own background work until it exits, including during cancellation.

Cancelling ``to_thread`` only cancels its awaiter, not the native worker. Keep
resource slots and input files alive until that worker actually returns.
"""
from __future__ import annotations

import asyncio


async def _drain(future: asyncio.Future) -> None:
    """Observe completion without letting repeated cancellation abandon work."""
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    if not future.cancelled():
        future.exception()


async def to_thread_joined(function, /, *args, **kwargs):
    """Run a synchronous worker; cancellation waits for this worker only.

    It does not forcibly terminate native inference. A graceful shutdown may
    therefore wait for the current ASR/OCR call, but queued calls can cancel.
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await _drain(worker)
        raise


async def gather_joined(*awaitables):
    """On any failure, cancel and join all siblings before returning to cleanup."""
    tasks = [asyncio.ensure_future(item) for item in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await _drain(asyncio.gather(*tasks, return_exceptions=True))
        raise
