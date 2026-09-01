import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import server, summarize
from clipmind.asr import Segment, Transcript
from clipmind.jobs import JobStore
from clipmind.media import Frame


def sse_payload(chunk: str | bytes) -> dict:
    if isinstance(chunk, bytes):
        chunk = chunk.decode()
    if not chunk.startswith("data: "):
        raise AssertionError(f"not an SSE data event: {chunk!r}")
    return json.loads(chunk.removeprefix("data: ").strip())


class ServerEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_serializes_done_and_error_terminal_events(self) -> None:
        async def fake_process(url, workdir, pools, report):
            report("fetching", 0.2, "fetching")
            if url.endswith("/error"):
                raise RuntimeError("fatal ingestion failure")
            return {"title": "Done"}

        test_store = JobStore()
        with (
            patch.object(server, "store", test_store),
            patch("clipmind.jobs.process", new=fake_process),
        ):
            response = await server.events()
            stream = response.body_iterator
            try:
                hello = sse_payload(await anext(stream))
                self.assertEqual(hello, {"type": "hello"})

                done = test_store.submit(
                    "https://v.douyin.com/done",
                    "Done",
                )
                failed = test_store.submit(
                    "https://v.douyin.com/error",
                    "Error",
                )
                terminal: dict[str, dict] = {}
                while len(terminal) < 2:
                    event = sse_payload(
                        await asyncio.wait_for(anext(stream), timeout=1)
                    )
                    if event["status"] in {"done", "error"}:
                        terminal[event["id"]] = event
            finally:
                await stream.aclose()

        done_event = terminal[done.id]
        error_event = terminal[failed.id]
        self.assertEqual(
            (done_event["status"], done_event["stage"], done_event["progress"]),
            ("done", "done", 1.0),
        )
        self.assertEqual(
            (error_event["status"], error_event["stage"], error_event["error"]),
            ("error", "error", "fatal ingestion failure"),
        )


class ExtractiveSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_api_key_uses_current_deterministic_fallback(self) -> None:
        transcript = Transcript(
            [
                Segment(0.0, 1.0, "第一句"),
                Segment(1.0, 2.0, "第二句"),
            ]
        )
        frame = Frame(
            index=0,
            timestamp=12.0,
            path=Path("frame.jpg"),
            text="屏幕要点\n第二行",
            lines=("屏幕要点", "第二行"),
            novelty=8,
        )
        expected = "\n".join(
            [
                "## 摘要 / Summary",
                "",
                "第一句 第二句",
                "",
                "## 要点 / Key points",
                "",
                "- [00:12] 屏幕要点 第二行",
                "",
                "> 未配置 `ANTHROPIC_API_KEY`，这份笔记由转写与 OCR 直接拼接生成，"
                "没有经过模型归纳。配置 key 后重跑即可得到真正的摘要。",
            ]
        )

        settings = SimpleNamespace(anthropic_api_key=None)
        with (
            patch.object(summarize, "settings", settings),
            patch.object(summarize, "_client", side_effect=AssertionError("API must not be called")) as client,
        ):
            result = await summarize.summarize(
                transcript,
                [frame],
                "Title",
                52.0,
                asyncio.Semaphore(1),
            )

        client.assert_not_called()
        self.assertEqual(result.engine, "fallback (no API key)")
        self.assertIsNone(result.error)
        self.assertEqual(result.markdown, expected)


if __name__ == "__main__":
    unittest.main()
