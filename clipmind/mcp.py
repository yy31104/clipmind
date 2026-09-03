"""Dependency-free MCP stdio server backed by the public Python SDK."""
from __future__ import annotations

import base64
import json
import mimetypes
import sys
from pathlib import Path

from .config import OUT_DIR
from .evidence import EvidencePackError
from .sdk import ClipMind, ClipMindError, PackLibrary


SERVER_INFO = {"name": "clipmind", "version": "1.2.0-dev"}
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2024-11-05")


TOOLS = [
    {
        "name": "clipmind.extract_video",
        "description": "Extract complete local Evidence Packs from one or more video URLs or local media paths.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["source"],
            "properties": {
                "source": {"type": "string", "description": "URL, share text, or local media path."},
                "reprocess": {"type": "boolean", "default": False},
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Explicitly process the complete source when the cost preflight refuses it.",
                },
            },
        },
    },
    {
        "name": "clipmind.get_transcript",
        "description": "Read timestamped transcript segments from a complete Evidence Pack.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["pack_id"],
            "properties": {"pack_id": {"type": "string"}},
        },
    },
    {
        "name": "clipmind.get_visual_timeline",
        "description": "Read canonical visual-state timing, OCR references, and transcript overlap.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["pack_id"],
            "properties": {
                "pack_id": {"type": "string"},
                "start": {"type": "number", "minimum": 0},
                "end": {"type": "number", "minimum": 0},
            },
        },
    },
    {
        "name": "clipmind.search_evidence",
        "description": "Search titles, speech, and on-screen OCR across the local library or within one pack.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "pack_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    },
    {
        "name": "clipmind.get_frame",
        "description": "Return a canonical or preview image by visual-state id or video timestamp.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["pack_id"],
            "anyOf": [{"required": ["visual_state_id"]}, {"required": ["timestamp"]}],
            "properties": {
                "pack_id": {"type": "string"},
                "visual_state_id": {"type": "string"},
                "timestamp": {"type": "number", "minimum": 0},
                "preview": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "clipmind.export_pack",
        "description": "Export a deterministic ZIP containing only the canonical Evidence Pack contract.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["pack_id"],
            "properties": {
                "pack_id": {"type": "string"},
                "destination": {"type": "string"},
            },
        },
    },
]


def _text(value: object) -> dict:
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return {"type": "text", "text": rendered}


def _result(value: object, *, text: str | None = None) -> dict:
    return {
        "content": [_text(text if text is not None else value)],
        "structuredContent": value,
    }


class MCPServer:
    def __init__(self, out_dir: Path | str = OUT_DIR) -> None:
        self.library = PackLibrary(out_dir)
        self.client = ClipMind(out_dir)

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                requested = (request.get("params") or {}).get("protocolVersion")
                value = {
                    "protocolVersion": (
                        requested
                        if requested in SUPPORTED_PROTOCOL_VERSIONS
                        else SUPPORTED_PROTOCOL_VERSIONS[0]
                    ),
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": SERVER_INFO,
                    "instructions": (
                        "ClipMind exposes only complete local Evidence Packs. "
                        "Canonical visual states fail open rather than silently losing evidence."
                    ),
                }
            elif method == "ping":
                value = {}
            elif method == "tools/list":
                value = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                value = self.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
            elif method == "resources/list":
                value = {
                    "resources": [
                        {
                            "uri": f"clipmind://packs/{pack.id}/evidence",
                            "name": str(pack.source.get("title") or pack.id),
                            "mimeType": "text/markdown",
                            "description": f"Complete Evidence Pack {pack.id}",
                        }
                        for pack in self.library.list()
                    ]
                }
            elif method == "resources/read":
                value = self.read_resource(str((request.get("params") or {}).get("uri") or ""))
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": value}
        except (ClipMindError, EvidencePackError, OSError, ValueError, TypeError) as exc:
            return self._error(request_id, -32602, str(exc))

    def call_tool(self, name: str, arguments: dict) -> dict:
        try:
            if name == "clipmind.extract_video":
                source = str(arguments.get("source") or "").strip()
                if not source:
                    raise ValueError("source is required")
                packs = self.client.analyze_sync(
                    source,
                    reprocess=bool(arguments.get("reprocess", False)),
                    force=bool(arguments.get("force", False)),
                )
                summaries = [pack.summary() for pack in packs]
                return _result(
                    {"packs": summaries},
                    text="\n".join(f"pack_id={pack['pack_id']}" for pack in summaries),
                )
            if name == "clipmind.get_transcript":
                pack = self.library.get(str(arguments.get("pack_id") or ""))
                transcript = pack.transcript
                rendered = "\n".join(
                    f"[{float(item.get('start') or 0):.3f}] {item.get('text') or ''}"
                    for item in transcript
                )
                return _result({"pack_id": pack.id, "segments": transcript}, text=rendered)
            if name == "clipmind.get_visual_timeline":
                pack = self.library.get(str(arguments.get("pack_id") or ""))
                start = float(arguments.get("start", 0))
                end = float(arguments["end"]) if arguments.get("end") is not None else None
                records = [
                    item
                    for item in pack.visual_timeline
                    if float(item.get("end") or item.get("start") or 0) >= start
                    and (end is None or float(item.get("start") or 0) <= end)
                ]
                return _result({"pack_id": pack.id, "visual_states": records})
            if name == "clipmind.search_evidence":
                query = str(arguments.get("query") or "").strip()
                if not query:
                    raise ValueError("query is required")
                limit = max(1, min(int(arguments.get("limit", 20)), 100))
                pack_id = arguments.get("pack_id")
                if pack_id:
                    pack = self.library.get(str(pack_id))
                    value = {"pack_id": pack.id, "query": query, "hits": pack.search(query, limit=limit)}
                else:
                    value = {"query": query, "results": self.library.search(query, limit=limit)}
                return _result(value)
            if name == "clipmind.get_frame":
                pack = self.library.get(str(arguments.get("pack_id") or ""))
                path, record = pack.frame(
                    visual_state_id=arguments.get("visual_state_id"),
                    timestamp=arguments.get("timestamp"),
                    preview=bool(arguments.get("preview", False)),
                )
                mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                return {
                    "content": [
                        _text({"pack_id": pack.id, "visual_state": record, "file": path.name}),
                        {"type": "image", "mimeType": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")},
                    ],
                    "structuredContent": {"pack_id": pack.id, "visual_state": record, "file": path.name},
                }
            if name == "clipmind.export_pack":
                pack = self.library.get(str(arguments.get("pack_id") or ""))
                destination = arguments.get("destination")
                path = pack.export(destination)
                return _result({"pack_id": pack.id, "path": str(path)})
            raise ValueError(f"unknown tool: {name}")
        except (ClipMindError, EvidencePackError, OSError, ValueError, TypeError) as exc:
            details = {"error": str(exc)}
            if isinstance(exc, ClipMindError):
                details.update(code=exc.code, action=exc.action, details=exc.details)
            return {
                "content": [_text(details)],
                "structuredContent": details,
                "isError": True,
            }

    def read_resource(self, uri: str) -> dict:
        prefix, suffix = "clipmind://packs/", "/evidence"
        if not uri.startswith(prefix) or not uri.endswith(suffix):
            raise ValueError("unsupported resource URI")
        pack_id = uri[len(prefix):-len(suffix)]
        pack = self.library.get(pack_id)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": pack.evidence_markdown,
                }
            ]
        }

    @staticmethod
    def _error(request_id: object, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }


def serve_stdio() -> None:
    server = MCPServer()
    for raw in sys.stdin.buffer:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = server.handle(request)
        except (json.JSONDecodeError, ValueError) as exc:
            response = MCPServer._error(None, -32700, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def entrypoint() -> None:
    serve_stdio()


if __name__ == "__main__":
    entrypoint()
