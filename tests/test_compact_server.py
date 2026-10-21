from __future__ import annotations

import tempfile
import asyncio
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from clipmind import server
from clipmind.evidence import EvidencePackError


class CompactDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_compact_choice_and_existing_full_api_remain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(server, "_workdir", return_value=root), \
                 patch.object(server.compact, "export_compact_zip", side_effect=lambda pack, dest: dest) as small, \
                 patch.object(server.handoff, "export_zip", side_effect=lambda pack, dest: dest) as full:
                response = await server.evidence_zip("sample", "compact")
                self.assertEqual(response.path, small.call_args.args[1])
                self.assertIn("sample-compact-evidence.zip", response.headers["content-disposition"])
                self.assertEqual(small.call_args.args[0], root)
                self.assertTrue(small.call_args.args[1].is_file())
                full.assert_not_called()
                response.path.unlink()
                response = await server.evidence_zip("sample")
                self.assertEqual(response.path, full.call_args.args[1])
                self.assertEqual(full.call_args.args[0], root)
                self.assertNotEqual(small.call_args.args[1], full.call_args.args[1])
                response.path.unlink()

    async def test_invalid_profile_never_exports(self):
        with patch.object(server, "_workdir", return_value=Path("unused")), \
             patch.object(server.compact, "export_compact_zip") as small:
            with self.assertRaises(HTTPException) as raised:
                await server.evidence_zip("sample", "invalid")
            self.assertEqual(raised.exception.status_code, 400)
            small.assert_not_called()

    async def test_explicit_text_download_passes_text_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "_workdir", return_value=Path(tmp)), \
                 patch.object(server.compact, "export_compact_zip", side_effect=lambda pack, dest, **kw: dest) as export:
                response = await server.evidence_zip("sample", "text")
                self.assertEqual(export.call_args.kwargs, {"image_policy": "text"})
                self.assertIn("sample-text-evidence.zip", response.headers["content-disposition"])
                response.path.unlink()

    async def test_invalid_pack_reports_conflict(self):
        with patch.object(server, "_workdir", return_value=Path("unused")), \
             patch.object(server.compact, "export_compact_zip", side_effect=EvidencePackError("invalid pack")):
            with self.assertRaises(HTTPException) as raised:
                await server.evidence_zip("sample", "compact")
            self.assertEqual(raised.exception.status_code, 409)

    async def test_response_cleans_archive_on_range_error_or_send_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "owned.zip"
            scope = {"type": "http", "method": "GET", "asgi": {"spec_version": "2.4"},
                     "headers": [(b"range", b"bytes=200-300")]}
            messages = []

            async def receive():
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)

            path.write_bytes(b"zip")
            await server._DownloadResponse(path)(scope, receive, send)
            self.assertEqual(messages[0]["status"], 416)
            self.assertFalse(path.exists())

            async def failed_send(message):
                raise RuntimeError("client disconnected")

            path.write_bytes(b"zip")
            scope["headers"] = []
            with self.assertRaises(RuntimeError):
                await server._DownloadResponse(path)(scope, receive, failed_send)
            self.assertFalse(path.exists())

    async def test_cancelled_request_cleans_after_background_writer_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            started, release, finished = threading.Event(), threading.Event(), threading.Event()
            destinations = []

            def slow_export(pack, destination):
                destinations.append(destination)
                started.set()
                release.wait(timeout=3)
                destination.write_bytes(b"complete archive")
                finished.set()
                return destination

            with patch.object(server, "_workdir", return_value=Path(tmp)), \
                 patch.object(server.compact, "export_compact_zip", side_effect=slow_export):
                request = asyncio.create_task(server.evidence_zip("sample", "compact"))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
                for _ in range(100):
                    if not destinations[0].exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(destinations[0].exists())


if __name__ == "__main__":
    unittest.main()
