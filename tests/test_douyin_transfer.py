"""Bounded Douyin downloads report real bytes without publishing a prefix."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clipmind.sources import douyin_browser as browser


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now


class Response:
    def __init__(self, blocks, *, headers=None, clock=None, tick=0, status=200):
        self.blocks = list(blocks)
        self.headers = headers or {}
        self.clock, self.tick = clock, tick
        self.status = status
        self.amounts = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read1(self, amount):
        self.amounts.append(amount)
        if self.clock:
            self.clock.now += self.tick
        block = self.blocks.pop(0) if self.blocks else b""
        if isinstance(block, Exception):
            raise block
        return block

    def read(self, amount):
        raise AssertionError("read1 must be used when available")


class TransferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.destination = self.root / "source.mp4"

    def tearDown(self):
        self.temporary.cleanup()

    async def test_real_byte_progress_returns_to_event_loop_and_is_throttled(self):
        response = Response([b"a"] * 20, headers={"Content-Length":"20"})
        events = []
        main_thread = threading.get_ident()

        def progress(written, total, elapsed):
            self.assertEqual(threading.get_ident(), main_thread)
            self.assertIs(type(written), int)
            self.assertTrue(total is None or type(total) is int)
            self.assertIs(type(elapsed), float)
            events.append((written, total, elapsed))

        with patch.object(browser.urllib.request, "urlopen", return_value=response) as open_url:
            path = await browser.download(["https://cdn.example/media?private=secret"], self.destination,
                                          chunk=1024*1024, on_progress=progress)
        self.assertEqual(path.read_bytes(), b"a" * 20)
        self.assertEqual([(row[0], row[1]) for row in events], [(0,None),(0,20),(20,20)])
        self.assertTrue(all(amount <= 64*1024 for amount in response.amounts))
        self.assertEqual(open_url.call_args.args[0].get_header("Accept-encoding"), "identity")
        self.assertLessEqual(open_url.call_args.kwargs["timeout"], 30)
        self.assertTrue(response.closed)

    async def test_unknown_length_still_reports_bytes_and_uses_read_fallback(self):
        class PlainResponse(Response):
            read1 = None
            def read(self, amount):
                return Response.read1(self, amount)

        response = PlainResponse([b"abc", b"de"])
        events = []
        with patch.object(browser.urllib.request, "urlopen", return_value=response):
            await browser.download(["https://cdn.example/media"], self.destination,
                                   on_progress=lambda *event: events.append(event))
        self.assertEqual(self.destination.read_bytes(), b"abcde")
        self.assertEqual((events[0][0],events[0][1]), (0,None))
        self.assertEqual((events[-1][0],events[-1][1]), (5,None))
        self.assertTrue(all(row[1] is None for row in events))
        self.assertLessEqual(len(events),3)

    async def test_intermediate_progress_is_emitted_for_a_slow_growing_transfer(self):
        clock = Clock()
        response = Response([b"a"] * 4, headers={"Content-Length":"4"}, clock=clock,tick=.6)
        events = []
        with (patch.object(browser, "time", SimpleNamespace(monotonic=clock.monotonic)),
              patch.object(browser.urllib.request, "urlopen", return_value=response)):
            await browser.download(["https://cdn.example/media"], self.destination,
                                   on_progress=lambda *event: events.append(event),timeout=10)
        self.assertEqual([row[0] for row in events], [0,0,2,4,4])
        self.assertTrue(any(0 < row[0] < 4 for row in events))

    async def test_truncated_response_tries_next_mirror_and_resets_byte_progress(self):
        responses = [Response([b"ab"],headers={"Content-Length":"4"}),
                     Response([b"good"],headers={"Content-Length":"4"})]
        events = []
        with patch.object(browser.urllib.request, "urlopen", side_effect=responses):
            await browser.download(["https://cdn.example/bad","https://cdn.example/good"],
                                   self.destination,max_bytes=20,
                                   on_progress=lambda *event: events.append(event))
        self.assertEqual(self.destination.read_bytes(), b"good")
        self.assertEqual([(row[0],row[1]) for row in events],[(0,None),(0,4),(0,None),(0,4),(4,4)])
        self.assertTrue(all(response.closed for response in responses))

    async def test_truncated_and_overlong_bodies_are_not_successful_files(self):
        for blocks in ([b"a"],[b"too long"]):
            response = Response(blocks,headers={"Content-Length":"3"})
            with (self.subTest(blocks=blocks),
                  patch.object(browser.urllib.request, "urlopen", return_value=response),
                  self.assertRaises(browser.ResolveFailed)):
                await browser.download(["https://cdn.example/media"],self.destination)
            self.assertFalse(self.destination.exists())

    async def test_invalid_lengths_or_compressed_transport_are_refused(self):
        for headers in ({"Content-Length":"-1"},{"Content-Length":"bad"},
                        {"Content-Encoding":"gzip"}):
            response = Response([b"abc"],headers=headers)
            with (self.subTest(headers=headers),
                  patch.object(browser.urllib.request, "urlopen", return_value=response),
                  self.assertRaises(browser.ResolveFailed)):
                await browser.download(["https://cdn.example/media"],self.destination)
            self.assertEqual(response.amounts,[])
            self.assertFalse(self.destination.exists())

    async def test_only_provably_complete_206_representation_is_accepted(self):
        full = Response([b"whole"],status=206,
                        headers={"Content-Length":"5","Content-Range":"bytes 0-4/5"})
        with patch.object(browser.urllib.request,"urlopen",return_value=full):
            await browser.download(["https://cdn.example/media"],self.destination)
        self.assertEqual(self.destination.read_bytes(),b"whole")
        self.destination.unlink()

        for status, headers in (
            (206,{"Content-Length":"5"}),
            (206,{"Content-Length":"5","Content-Range":"bytes 0-4/10"}),
            (206,{"Content-Length":"5","Content-Range":"bytes 5-9/10"}),
            (206,{"Content-Range":"bytes 0-4/5"}),
            (200,{"Content-Length":"5","Content-Range":"bytes 0-4/5"}),
        ):
            response=Response([b"whole"],status=status,headers=headers)
            with (self.subTest(status=status,headers=headers),
                  patch.object(browser.urllib.request,"urlopen",return_value=response),
                  self.assertRaisesRegex(browser.ResolveFailed,"range")):
                await browser.download(["https://cdn.example/media"],self.destination)
            self.assertEqual(response.amounts,[])
            self.assertFalse(self.destination.exists())

    async def test_announced_and_unannounced_byte_limits_reject_without_partial_file(self):
        for headers in ({"Content-Length":"9"},{}):
            response = Response([b"abcdef"],headers=headers)
            with (self.subTest(headers=headers),
                  patch.object(browser.urllib.request, "urlopen", return_value=response),
                  self.assertRaisesRegex(browser.ResolveFailed,"byte limit")):
                await browser.download(["https://cdn.example/media"],self.destination,max_bytes=5)
            self.assertFalse(self.destination.exists())

    async def test_byte_budget_covers_failed_mirrors_too(self):
        responses = [Response([b"abc"],headers={"Content-Length":"4"}),
                     Response([b"good"],headers={"Content-Length":"4"})]
        with (patch.object(browser.urllib.request,"urlopen",side_effect=responses) as opener,
              self.assertRaisesRegex(browser.ResolveFailed,"byte limit")):
            await browser.download(["https://cdn.example/a","https://cdn.example/b"],
                                   self.destination,max_bytes=6)
        self.assertEqual(opener.call_count,2)
        self.assertEqual(responses[1].amounts,[])
        self.assertFalse(self.destination.exists())

    async def test_total_deadline_stops_a_continuous_trickle(self):
        clock = Clock()
        response = Response([b"a"]*20,clock=clock,tick=.6)
        with (patch.object(browser,"time",SimpleNamespace(monotonic=clock.monotonic)),
              patch.object(browser.urllib.request,"urlopen",return_value=response),
              self.assertRaisesRegex(browser.ResolveFailed,"total time")):
            await browser.download(["https://cdn.example/media"],self.destination,timeout=1)
        self.assertEqual(len(response.amounts),2)
        self.assertFalse(self.destination.exists())

    async def test_deadline_is_shared_by_all_mirrors(self):
        clock = Clock()
        response = Response([OSError("refused")],clock=clock,tick=1.1)
        with (patch.object(browser,"time",SimpleNamespace(monotonic=clock.monotonic)),
              patch.object(browser.urllib.request,"urlopen",return_value=response) as opener,
              self.assertRaisesRegex(browser.ResolveFailed,"total time")):
            await browser.download(["https://cdn.example/a","https://cdn.example/b"],
                                   self.destination,timeout=1)
        self.assertEqual(opener.call_count,1)
        self.assertFalse(self.destination.exists())

    async def test_repeated_cancel_waits_for_writer_and_then_cleans_partial_file(self):
        started,exited = threading.Event(),threading.Event()

        def transfer(url,destination,chunk,stop,**kwargs):
            started.set()
            try:
                stop.wait(5)
                time.sleep(.03)
                destination.write_bytes(b"late write")
                raise browser._Stopped()
            finally:
                exited.set()

        with patch.object(browser,"_fetch_to",transfer):
            task=asyncio.create_task(browser.download(["https://cdn.example/media"],self.destination))
            self.assertTrue(await asyncio.to_thread(started.wait,5))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(exited.is_set())
        self.assertFalse(self.destination.exists())

    async def test_public_errors_and_normal_log_do_not_include_transport_urls(self):
        sensitive="https://cdn.example/media?token=do-not-print"
        with (patch.object(browser.urllib.request,"urlopen",side_effect=OSError(sensitive)),
              self.assertLogs(browser.logger,level="WARNING") as log,
              self.assertRaises(browser.ResolveFailed) as caught):
            await browser.download([sensitive],self.destination)
        self.assertNotIn(sensitive,str(caught.exception))
        self.assertNotIn("do-not-print"," ".join(log.output))

    async def test_success_delivers_final_progress_before_return_and_drops_late_queue(self):
        pending, events = [], []
        queued_loop = SimpleNamespace(call_soon_threadsafe=lambda callback,*args: pending.append((callback,args)))
        response = Response([b"whole"],headers={"Content-Length":"5"})
        with (patch.object(browser.asyncio,"get_running_loop",return_value=queued_loop),
              patch.object(browser.urllib.request,"urlopen",return_value=response)):
            await browser.download(["https://cdn.example/media"],self.destination,
                                   on_progress=lambda *event: events.append(event))
        self.assertEqual([(row[0],row[1]) for row in events],[(0,None),(5,5)])
        completed = list(events)
        for callback,args in pending:
            callback(*args)
        self.assertEqual(events,completed)

    async def test_old_mirror_callbacks_cannot_overwrite_next_mirror_progress(self):
        pending, events = [], []
        actual_loop = asyncio.get_running_loop()
        queued_loop = SimpleNamespace(call_soon_threadsafe=lambda callback,*args: pending.append((callback,args)))
        responses = [Response([b"bad"],headers={"Content-Length":"4"}),
                     Response([b"good"],headers={"Content-Length":"4"})]
        original = browser._fetch_to
        calls = []

        def flush_old_callbacks():
            for callback,values in list(pending):
                callback(*values)
            pending.clear()
            flushed.set()

        flushed=threading.Event()

        def transfer(*args,**kwargs):
            calls.append(args[0])
            if len(calls) == 2:
                # Simulate a delayed first-mirror report after the second
                # mirror already reset its visible progress to zero.
                actual_loop.call_soon_threadsafe(flush_old_callbacks)
                if not flushed.wait(5):
                    raise AssertionError("the event-loop callback queue did not flush")
            return original(*args,**kwargs)

        with (patch.object(browser.asyncio,"get_running_loop",return_value=queued_loop),
              patch.object(browser.urllib.request,"urlopen",side_effect=responses),
              patch.object(browser,"_fetch_to",side_effect=transfer)):
            await browser.download(["https://cdn.example/a","https://cdn.example/b"],self.destination,
                                   on_progress=lambda *event: events.append(event))
        self.assertEqual([(row[0],row[1]) for row in events],[(0,None),(0,None),(4,4)])

    async def test_cancel_disables_queued_progress_after_writer_exit(self):
        pending, events = [], []
        started = threading.Event()
        queued_loop = SimpleNamespace(call_soon_threadsafe=lambda callback,*args: pending.append((callback,args)))

        def transfer(url,destination,chunk,stop,**kwargs):
            kwargs["on_progress"](8,20,.1)
            started.set()
            stop.wait(5)
            kwargs["on_progress"](12,20,.2)
            raise browser._Stopped()

        with (patch.object(browser.asyncio,"get_running_loop",return_value=queued_loop),
              patch.object(browser,"_fetch_to",side_effect=transfer)):
            task=asyncio.create_task(browser.download(["https://cdn.example/media"],self.destination,
                                                    on_progress=lambda *event: events.append(event)))
            self.assertTrue(await asyncio.to_thread(started.wait,5))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        completed=list(events)
        for callback,args in pending:
            callback(*args)
        self.assertEqual(events,completed)

    async def test_invalid_transfer_settings_never_request_media(self):
        for options in ({"timeout":0},{"timeout":float("nan")},{"timeout":float("inf")},
                        {"max_bytes":0},{"max_bytes":True},{"chunk":0}):
            with (self.subTest(options=options),
                  patch.object(browser.urllib.request,"urlopen") as opener,
                  self.assertRaises(browser.ResolveFailed)):
                await browser.download(["https://cdn.example/media"],self.destination,**options)
            opener.assert_not_called()


if __name__=="__main__":
    unittest.main()
