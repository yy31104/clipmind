from __future__ import annotations

import json
from pathlib import Path

from clipmind import evidence


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_complete_pack(
    root: Path,
    *,
    name: str = "pack-1",
    source_id: str = "video-1",
    title: str = "Vector retrieval lesson",
) -> Path:
    pack = root / name
    pack.mkdir(parents=True)
    for artifact in evidence.PACK_ARTIFACTS:
        path = pack / artifact
        if artifact.endswith("/"):
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    source = {
        "platform": "youtube",
        "source_id": source_id,
        "title": title,
        "url": f"https://youtu.be/{source_id}",
        "duration": 20.0,
    }
    transcript = [
        {"id": "transcript-00001", "start": 1.0, "end": 4.0, "text": "vector retrieval pipeline"}
    ]
    ocr = [
        {
            "id": "ocr-00001",
            "visual_state_ref": "visual-00001",
            "timestamp": 2.0,
            "text": "向量数据库",
            "lines": ["向量数据库"],
        }
    ]
    timeline = [
        {
            "id": "visual-00001",
            "start": 2.0,
            "end": 8.0,
            "file": "visual_states/all/00-02-00001.jpg",
            "preview_file": "visual_states/preview/00-02-00001.jpg",
            "ocr_ref": "ocr-00001",
            "transcript_refs": ["transcript-00001"],
            "in_preview": True,
        }
    ]
    write_json(pack / "source.json", source)
    write_json(pack / "job.json", {"job": {"id": name, "status": "done"}})
    (pack / "transcript.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in transcript),
        encoding="utf-8",
    )
    (pack / "ocr.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ocr),
        encoding="utf-8",
    )
    (pack / "visual_timeline.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in timeline),
        encoding="utf-8",
    )
    (pack / "transcript.md").write_text("# Transcript\n", encoding="utf-8")
    (pack / "evidence.md").write_text("# Evidence\n", encoding="utf-8")
    frame_bytes = b"\xff\xd8fixture\xff\xd9"
    (pack / timeline[0]["file"]).write_bytes(frame_bytes)
    (pack / timeline[0]["preview_file"]).write_bytes(frame_bytes)
    write_json(
        pack / "manifest.json",
        {
            "schema": {"name": evidence.SCHEMA_NAME, "version": evidence.SCHEMA_VERSION},
            "source": {"platform": "youtube", "id": source_id},
            "status": "complete",
            "artifacts": list(evidence.PACK_ARTIFACTS),
            "counts": {"transcript_segments": 1, "canonical_visual_states": 1},
            "completeness": {"transcript": "complete", "ocr": "complete", "visual_states": "complete"},
            "diagnostics": {},
            "configuration": {},
        },
    )
    return pack
