"""MCP must use UTF-8 on the wire even with a Windows legacy code page."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipmind import mcp
from tests.pack_fixture import make_complete_pack


class MCPUnicodeTests(unittest.TestCase):
    def test_writer_uses_utf8_bytes_instead_of_the_text_stream_encoding(self):
        raw = io.BytesIO()
        output = io.TextIOWrapper(raw, encoding="cp936", errors="strict")
        response = {"jsonrpc": "2.0", "id": 1, "result": {"text": "中文资料 📈"}}
        with patch.object(mcp.sys, "stdout", output):
            mcp._write_response(response)
        data = raw.getvalue()
        output.detach()
        self.assertEqual(json.loads(data.decode("utf-8")), response)
        self.assertTrue(data.endswith(b"\n"))

    def test_writer_keeps_unicode_only_streams_usable(self):
        output = io.StringIO()
        response = {"jsonrpc": "2.0", "id": 1, "result": {"text": "中文资料 📈"}}
        with patch.object(mcp.sys, "stdout", output):
            mcp._write_response(response)
        self.assertEqual(json.loads(output.getvalue()), response)

    def test_real_stdio_cp936_handles_chinese_and_emoji_resources_and_transcript(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            library = root / "library"
            pack = make_complete_pack(library, title="中文资料 📈")
            evidence = "# 中文证据 📈\n"
            text = "量化研究方法 📊"
            (pack / "evidence.md").write_text(evidence, encoding="utf-8")
            (pack / "transcript.jsonl").write_text(
                json.dumps({"id": "transcript-00001", "start": 1, "end": 4, "text": text},
                           ensure_ascii=False) + "\n", encoding="utf-8",
            )
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
                {"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                 "params": {"uri": "clipmind://packs/pack-1/evidence"}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "clipmind.get_transcript", "arguments": {"pack_id": "pack-1"},
                }},
            ]
            environment = dict(os.environ)
            environment.update(
                CLIPMIND_OUT=str(library), PYTHONIOENCODING="cp936:strict", PYTHONUTF8="0",
                PYTHONPATH=str(Path(__file__).resolve().parents[1]),
            )
            completed = subprocess.run(
                [sys.executable, "-m", "clipmind.mcp"], cwd=root,
                input="".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests).encode("utf-8"),
                capture_output=True, timeout=20, env=environment,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("ascii", errors="replace"))
        self.assertEqual(completed.stderr, b"")
        responses = [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3])
        self.assertEqual(responses[0]["result"]["resources"][0]["name"], "中文资料 📈")
        self.assertEqual(responses[1]["result"]["contents"][0]["text"], evidence)
        self.assertEqual(responses[2]["result"]["structuredContent"]["segments"][0]["text"], text)


if __name__ == "__main__":
    unittest.main()
