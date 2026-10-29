import asyncio
import json
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch
from types import SimpleNamespace

from clipmind import server


async def post(endpoint, *, headers=None, payload=None, host="127.0.0.1:8420", method="POST"):
    url = urlsplit(endpoint)
    messages = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": json.dumps(payload or {}).encode(), "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": method, "scheme": "http", "path": url.path,
             "raw_path": url.path.encode(), "root_path": "", "query_string": url.query.encode(),
             "server": ("127.0.0.1", 8420), "client": ("127.0.0.1", 40000),
             "headers": [(b"host", host.encode()), (b"content-type", b"application/json"),
                         *[(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]]}
    await asyncio.wait_for(server.app(scope, receive, send), timeout=3)
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


class LocalWriteBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_saved_evidence_is_an_actionable_422(self):
        with patch.object(server, "_pack", side_effect=server.EvidencePackError("invalid transcript.jsonl")):
            self.assertEqual(await post("/api/packs/saved/transcript", method="GET"), 422)
        response = await server.invalid_evidence(None, server.EvidencePackError("invalid transcript.jsonl"))
        message = json.loads(response.body)
        self.assertEqual(message["code"], "invalid_evidence_pack")
        self.assertIn("transcript.jsonl", message["detail"])

    async def test_partial_batch_storage_failure_does_not_claim_nothing_started(self):
        accepted = SimpleNamespace(public=lambda: {"id": "accepted", "status": "queued"})
        with patch.object(server.store, "reusable", return_value=None), \
             patch.object(server.store, "submit", side_effect=[accepted, server.JobPersistenceError()]) as submit:
            status = await post("/api/jobs", payload={"text": "https://youtu.be/first https://youtu.be/second"})
            self.assertEqual(status, 503)
            self.assertEqual(submit.call_count, 2)
        response = await server.persistence_error(None, server.JobPersistenceError())
        message = json.loads(response.body)["detail"]
        self.assertIn("已接收", message)
        self.assertNotIn("尚未开始", message)

    async def test_same_origin_rebinding_host_is_rejected(self):
        status = await post("/api/jobs", host="foreign.example:8420",
                            headers={"Origin": "http://foreign.example:8420"},
                            payload={"text": "https://youtu.be/video"})
        self.assertEqual(status, 400)

    async def test_foreign_origin_cannot_submit_upload_retry_or_handoff(self):
        with patch.object(server.store, "submit") as submit:
            for endpoint in ("/api/jobs", "/api/uploads?filename=video.mp4",
                             "/api/jobs/missing/reprocess", "/api/jobs/missing/handoff"):
                status = await post(endpoint, headers={"Origin": "https://foreign.example"},
                                    payload={"text": "https://youtu.be/video"})
                self.assertEqual(status, 403)
            submit.assert_not_called()

    async def test_same_origin_and_cli_can_reach_normal_validation(self):
        for headers in ({}, {"Origin": "http://127.0.0.1:8420"}):
            status = await post("/api/jobs", headers=headers, payload={"text": "not a source"})
            self.assertEqual(status, 400)
        status = await post("/api/jobs", headers={"Sec-Fetch-Site": "cross-site"},
                            payload={"text": "not a source"})
        self.assertEqual(status, 403)

    async def test_unbounded_paste_rejected_before_jobs_created(self):
        with patch.object(server.store, "submit") as submit:
            status = await post("/api/jobs", payload={"text": "x" * 65_537})
            self.assertEqual(status, 422)
            submit.assert_not_called()
