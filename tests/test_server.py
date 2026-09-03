import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from clipmind import server
from clipmind.asr import Segment, Transcript
from clipmind.config import Settings
from clipmind.fetch import FetchError
from clipmind.jobs import Job, JobStore
from clipmind.media import Frame
from tests.pack_fixture import make_complete_pack


def sse_payload(chunk: str | bytes) -> dict:
    if isinstance(chunk, bytes):
        chunk = chunk.decode()
    if not chunk.startswith("data: "):
        raise AssertionError(f"not an SSE data event: {chunk!r}")
    return json.loads(chunk.removeprefix("data: ").strip())


class ServerEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_serializes_resync_and_keeps_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            with patch.object(server, "store", test_store):
                response = await server.events()
                stream = response.body_iterator
                try:
                    self.assertEqual(
                        sse_payload(await anext(stream)), {"type": "hello"}
                    )
                    queue = next(iter(test_store._subscribers))
                    for index in range(queue.maxsize):
                        queue.put_nowait(
                            {"id": f"stale-{index}", "status": "running"}
                        )
                    current = Job(
                        "current",
                        "https://v.douyin.com/current",
                        "Current",
                        status="done",
                        stage="done",
                        progress=1.0,
                    )
                    test_store.jobs[current.id] = current

                    test_store._publish(current)

                    self.assertEqual(
                        sse_payload(await anext(stream)), {"type": "resync"}
                    )
                    self.assertIn(queue, test_store._subscribers)
                    later = Job(
                        "later",
                        "https://v.douyin.com/later",
                        "Later",
                        status="running",
                        stage="fetching",
                    )
                    test_store._publish(later)
                    self.assertEqual(sse_payload(await anext(stream))["id"], later.id)
                finally:
                    await stream.aclose()

            self.assertNotIn(queue, test_store._subscribers)

    def test_frontend_refreshes_snapshot_for_hello_and_resync(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'if (job.type === "hello" || job.type === "resync") {\n'
            "    await refreshJobs();\n"
            "    return;\n"
            "  }",
            source,
        )

    def test_frontend_only_claims_verified_builtin_sources(self) -> None:
        root = Path(__file__).resolve().parents[1] / "web"
        html = (root / "index.html").read_text(encoding="utf-8")
        javascript = (root / "app.js").read_text(encoding="utf-8")

        self.assertIn("YouTube, 抖音, 本地文件", html)
        self.assertNotIn("Bilibili", html)
        self.assertNotIn("TikTok", html)
        self.assertNotIn("小红书", html)
        self.assertIn('/static/style.css?v=3', html)
        self.assertIn('/static/app.js?v=3', html)
        self.assertIn('SOURCE_LABEL[item.platform] || item.platform', javascript)
        self.assertIn('supportedSourceLabels(health.supported_sources)', javascript)

    async def test_handoff_requires_an_explicit_inbox_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(
                Path(tempdir) / "out",
                config=Settings(knowledge_base_inbox=None),
            )
            with patch.object(server, "store", test_store):
                with self.assertRaises(HTTPException) as raised:
                    await server.send_to_knowledge_base("job-1")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("CLIPMIND_KB_INBOX", raised.exception.detail)

    async def test_visual_preview_route_serves_only_preview_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            test_store.jobs["job-1"] = Job(
                "job-1", "https://v.douyin.com/test", "Test", status="done"
            )
            preview = test_store.workdir("job-1") / "visual_states" / "preview"
            preview.mkdir(parents=True)
            frame = preview / "00-01-00000.jpg"
            frame.write_bytes(b"preview")
            outside = test_store.workdir("job-1") / "visual_states" / "all.jpg"
            outside.write_bytes(b"outside")

            with patch.object(server, "store", test_store):
                response = await server.visual_preview("job-1", frame.name)
                with self.assertRaises(HTTPException) as raised:
                    await server.visual_preview("job-1", "../../all.jpg")

        self.assertEqual(Path(response.path), frame.resolve())
        self.assertEqual(raised.exception.status_code, 404)

    async def test_canonical_visual_state_and_evidence_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            test_store.jobs["job-1"] = Job(
                "job-1", "https://v.douyin.com/test", "Test", status="done"
            )
            workdir = test_store.workdir("job-1")
            canonical = workdir / "visual_states" / "all" / "00-01-00000.jpg"
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b"canonical")
            evidence_path = workdir / "evidence.md"
            evidence_path.write_text("# Evidence", encoding="utf-8")

            with patch.object(server, "store", test_store):
                image_response = await server.visual_state("job-1", canonical.name)
                evidence_response = await server.evidence_file("job-1")

        self.assertEqual(Path(image_response.path), canonical.resolve())
        self.assertEqual(Path(evidence_response.path), evidence_path)

    async def test_artifact_routes_reject_unknown_job_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            with patch.object(server, "store", test_store):
                with self.assertRaises(HTTPException) as raised:
                    await server.evidence_file("unknown")

        self.assertEqual(raised.exception.status_code, 404)

    async def test_agent_api_reads_only_complete_pack_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "out"
            make_complete_pack(root)
            test_store = JobStore(root)
            with patch.object(server, "store", test_store):
                listing = await server.packs()
                detail = await server.pack_detail("pack-1")
                transcript = await server.pack_transcript("pack-1")
                timeline = await server.pack_timeline("pack-1")
                search = await server.pack_search("pack-1", "向量")
                frame = await server.pack_frame("pack-1", timestamp=3.0)

        self.assertEqual(listing["packs"][0]["pack_id"], "pack-1")
        self.assertEqual(detail["platform"], "youtube")
        self.assertEqual(transcript["segments"][0]["text"], "vector retrieval pipeline")
        self.assertEqual(timeline["visual_states"][0]["id"], "visual-00001")
        self.assertEqual(search["hits"][0]["kind"], "ocr")
        self.assertEqual(Path(frame.path).name, "00-02-00001.jpg")

    async def test_sse_serializes_done_and_error_terminal_events(self) -> None:
        async def fake_process(url, workdir, pools, report, **kwargs):
            report("fetching", 0.2, "fetching")
            if url.endswith("/error"):
                raise FetchError(
                    "link_unavailable",
                    "This Douyin share link is expired or unavailable.",
                    "Copy a fresh share link from Douyin and try again.",
                )
            return {"title": "Done"}

        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
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
            await test_store.close()

        done_event = terminal[done.id]
        error_event = terminal[failed.id]
        self.assertEqual(
            (done_event["status"], done_event["stage"], done_event["progress"]),
            ("done", "done", 1.0),
        )
        self.assertEqual(
            (
                error_event["status"],
                error_event["stage"],
                error_event["error_code"],
            ),
            ("error", "error", "link_unavailable"),
        )
        self.assertIn("fresh share link", error_event["error_action"])

    async def test_submit_reuses_a_complete_pack_and_reprocess_is_explicit(self) -> None:
        async def fake_process(url, workdir, pools, report, **kwargs):
            return {"id": "123", "title": "Reprocessed"}

        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            cached = Job(
                id="cached",
                url="https://v.douyin.com/AbC",
                title="Cached",
                status="done",
                stage="done",
                progress=1.0,
                result={"id": "123", "title": "Cached"},
                finished_at=10.0,
            )
            test_store.jobs[cached.id] = cached
            with (
                patch.object(server, "store", test_store),
                patch("clipmind.jobs.evidence.load_complete_pack", return_value={}),
                patch("clipmind.jobs.process", new=fake_process),
            ):
                response = await server.submit(
                    server.SubmitBody(text="https://v.douyin.com/AbC/")
                )
                stable_response = await server.submit(
                    server.SubmitBody(text="https://www.douyin.com/video/123")
                )
                replacement = await server.reprocess(cached.id)
                await asyncio.gather(*list(test_store._tasks))

        self.assertEqual(response["reused"], 1)
        self.assertEqual(response["jobs"][0]["id"], cached.id)
        self.assertEqual(stable_response["jobs"][0]["id"], cached.id)
        self.assertNotEqual(replacement["id"], cached.id)

    async def test_process_anyway_is_a_per_job_override(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            source = Job(
                id="expensive",
                url="https://youtu.be/expensive",
                title="Expensive",
                status="error",
                error_code="cost_limit_exceeded",
            )
            test_store.jobs[source.id] = source
            with (
                patch.object(server, "store", test_store),
                patch.object(test_store, "_schedule"),
            ):
                replacement = await server.reprocess(
                    source.id, server.ReprocessBody(force=True)
                )

        self.assertEqual(replacement["options"], {"force": True})
        self.assertEqual(replacement["status"], "queued")

    async def test_browser_upload_is_streamed_to_a_safe_durable_path(self) -> None:
        class Request:
            async def stream(self):
                yield b"first"
                yield b" second"

        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            with (
                patch.object(server, "store", test_store),
                patch.object(test_store, "_schedule"),
            ):
                uploaded = await server.upload(
                    Request(),
                    filename="../../My Recording.MOV",
                )
            source = Path(uploaded["url"])

            self.assertTrue(source.is_relative_to(test_store.storage.root / ".uploads"))
            self.assertEqual(source.suffix, ".mov")
            self.assertEqual(source.read_bytes(), b"first second")
            self.assertEqual(uploaded["title"], "My Recording")
            self.assertEqual(uploaded["options"]["uploaded_filename"], "My Recording.MOV")

    async def test_browser_upload_rejects_unsupported_files_without_residue(self) -> None:
        class Request:
            async def stream(self):
                yield b"not media"

        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            with patch.object(server, "store", test_store):
                with self.assertRaises(HTTPException) as raised:
                    await server.upload(Request(), filename="notes.txt")

            self.assertEqual(raised.exception.status_code, 415)
            self.assertFalse((test_store.storage.root / ".uploads").exists())

    async def test_browser_upload_limit_removes_the_partial_file(self) -> None:
        class Request:
            async def stream(self):
                yield b"too large"

        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(
                Path(tempdir) / "out",
                config=Settings(max_upload_mb=0),
            )
            with patch.object(server, "store", test_store):
                with self.assertRaises(HTTPException) as raised:
                    await server.upload(Request(), filename="recording.mp4")

            upload_root = test_store.storage.root / ".uploads"
            self.assertEqual(raised.exception.status_code, 413)
            self.assertEqual(list(upload_root.iterdir()), [])

    async def test_recovered_done_job_is_listed_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            out_dir = Path(tempdir) / "out"

            async def fake_process(url, workdir, pools, report, **kwargs):
                (workdir / "note.md").write_text("persisted note", encoding="utf-8")
                (workdir / "transcript.json").write_text(
                    '[{"start": 0, "end": 1, "text": "persisted"}]',
                    encoding="utf-8",
                )
                return {"title": "Recovered note", "duration": 1, "keyframes": []}

            first = JobStore(out_dir)
            queue = first.subscribe()
            with patch("clipmind.jobs.process", new=fake_process):
                job = first.submit("https://v.douyin.com/recovered", "Recovered")
                while True:
                    event = await asyncio.wait_for(queue.get(), timeout=1)
                    if event["id"] == job.id and event["status"] == "done":
                        break
            await first.close()

            recovered = JobStore(out_dir)
            recovered.start()
            with patch.object(server, "store", recovered):
                listing = await server.listing()
                detail = await server.detail(job.id)
            await recovered.close()

        self.assertEqual([item["id"] for item in listing["jobs"]], [job.id])
        self.assertEqual(detail["status"], "done")
        self.assertEqual(detail["result"]["title"], "Recovered note")
        self.assertEqual(detail["note_markdown"], "persisted note")
        self.assertEqual(detail["transcript"][0]["text"], "persisted")



if __name__ == "__main__":
    unittest.main()
