from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from clipmind.mcp import MCPServer, SUPPORTED_PROTOCOL_VERSIONS
from tests.pack_fixture import make_complete_pack


class MCPServerTests(unittest.TestCase):
    def test_initialize_and_tools_are_protocol_responses(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            server = MCPServer(tempdir)
            initialized = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            )
            tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        names = {item["name"] for item in tools["result"]["tools"]}
        self.assertEqual(
            names,
            {
                "clipmind.extract_video",
                "clipmind.get_transcript",
                "clipmind.get_visual_timeline",
                "clipmind.search_evidence",
                "clipmind.get_frame",
                "clipmind.export_pack",
            },
        )

    def test_initialize_negotiates_an_unknown_protocol_version(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            server = MCPServer(tempdir)
            initialized = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2099-01-01"},
                }
            )

        self.assertEqual(
            initialized["result"]["protocolVersion"],
            SUPPORTED_PROTOCOL_VERSIONS[0],
        )

    def test_stdio_emits_only_json_rpc_responses(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        environment = os.environ.copy()
        environment["CLIPMIND_OUT"] = tempfile.mkdtemp()
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "clipmind.mcp"],
                input="".join(json.dumps(item) + "\n" for item in requests),
                text=True,
                capture_output=True,
                check=True,
                env=environment,
            )
        finally:
            Path(environment["CLIPMIND_OUT"]).rmdir()

        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(completed.stderr, "")

    def test_tools_read_structured_text_and_image_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            make_complete_pack(root)
            server = MCPServer(root)

            transcript = server.call_tool(
                "clipmind.get_transcript", {"pack_id": "pack-1"}
            )
            search = server.call_tool("clipmind.search_evidence", {"query": "向量"})
            frame = server.call_tool(
                "clipmind.get_frame", {"pack_id": "pack-1", "timestamp": 3}
            )

        self.assertIn("vector retrieval pipeline", transcript["content"][0]["text"])
        self.assertEqual(search["structuredContent"]["results"][0]["job_id"], "pack-1")
        self.assertEqual(frame["content"][1]["type"], "image")
        self.assertTrue(base64.b64decode(frame["content"][1]["data"]).startswith(b"\xff\xd8"))

    def test_resources_expose_only_complete_evidence_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            make_complete_pack(root)
            server = MCPServer(root)
            listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
            uri = listed["result"]["resources"][0]["uri"]
            read = server.handle(
                {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}}
            )

        self.assertEqual(uri, "clipmind://packs/pack-1/evidence")
        self.assertEqual(read["result"]["contents"][0]["text"], "# Evidence\n")


if __name__ == "__main__":
    unittest.main()
