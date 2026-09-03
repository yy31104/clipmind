"""FastAPI app: paste text in, watch progress over SSE, read the note."""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import handoff
from .config import WEB_DIR
from .jobs import JobStore
from .links import extract_sources, guess_title, normalize_url
from .sources import supported_sources

store = JobStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.start()
    try:
        yield
    finally:
        await store.close()


app = FastAPI(title="ClipMind", lifespan=lifespan)


class SubmitBody(BaseModel):
    text: str
    reprocess: bool = False
    force: bool = False


class ReprocessBody(BaseModel):
    force: bool = False


MEDIA_EXTENSIONS = frozenset(
    {
        ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    }
)


def _workdir(job_id: str) -> Path:
    job = store.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return store.workdir(job.id)


@app.post("/api/jobs")
async def submit(body: SubmitBody):
    urls = extract_sources(body.text)
    if not urls:
        raise HTTPException(400, "No supported URL or local media source was found.")
    active = {
        normalize_url(j.url)
        for j in store.jobs.values()
        if j.status in ("queued", "running")
    }
    jobs, skipped, reused = [], 0, 0
    for url in urls:
        cache_key = normalize_url(url)
        if cache_key in active:
            skipped += 1
            continue
        cached = None if body.reprocess else store.reusable(url)
        if cached is not None:
            jobs.append(cached.public())
            reused += 1
            continue
        jobs.append(
            store.submit(
                url,
                guess_title(body.text, url) or url,
                options={"force": body.force},
            ).public()
        )
        active.add(cache_key)
    return {
        "jobs": jobs,
        "found": len(urls),
        "skipped": skipped,
        "reused": reused,
    }


@app.post("/api/uploads")
async def upload(request: Request, filename: str, force: bool = False):
    """Stream one browser-selected local file into the durable local library."""
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.casefold()
    if not safe_name or suffix not in MEDIA_EXTENSIONS:
        raise HTTPException(
            415,
            "Unsupported media type. Choose a video or audio file supported by FFmpeg.",
        )
    upload_root = store.storage.root / ".uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    destination = upload_root / f"{uuid.uuid4().hex}{suffix}"
    maximum = store.config.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with destination.open("xb") as handle:
            async for chunk in request.stream():
                written += len(chunk)
                if written > maximum:
                    raise HTTPException(
                        413,
                        f"File exceeds the configured {store.config.max_upload_mb} MB upload limit.",
                    )
                handle.write(chunk)
        if written == 0:
            raise HTTPException(400, "The uploaded file is empty.")
        job = store.submit(
            str(destination),
            Path(safe_name).stem,
            options={"force": force, "uploaded_filename": safe_name},
        )
        return job.public()
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@app.post("/api/jobs/{job_id}/reprocess")
async def reprocess(job_id: str, body: ReprocessBody | None = None):
    source = store.jobs.get(job_id)
    if not source:
        raise HTTPException(404, "no such job")
    if source.status in {"queued", "running"}:
        raise HTTPException(409, "This job is already processing.")
    return store.submit(
        source.url,
        source.title,
        options={"force": bool(body and body.force)},
    ).public()


@app.get("/api/jobs")
async def listing():
    return {"jobs": store.listing(), "capacity": store.config.max_videos}


@app.get("/api/search")
async def search(q: str, limit: int = 20):
    return {"query": q, "results": store.search(q, limit=limit)}


@app.get("/api/jobs/{job_id}")
async def detail(job_id: str):
    job = store.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    data = job.public()
    note = store.workdir(job_id) / "note.md"
    data["note_markdown"] = note.read_text(encoding="utf-8") if note.exists() else None
    transcript = store.workdir(job_id) / "transcript.json"
    data["transcript"] = (
        json.loads(transcript.read_text(encoding="utf-8")) if transcript.exists() else []
    )
    return data


@app.get("/api/jobs/{job_id}/keyframes/{name}")
async def keyframe(job_id: str, name: str):
    root = (_workdir(job_id) / "keyframes").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "no such frame")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/visual_states/preview/{name}")
async def visual_preview(job_id: str, name: str):
    root = (_workdir(job_id) / "visual_states" / "preview").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "no such visual state")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/visual_states/all/{name}")
async def visual_state(job_id: str, name: str):
    root = (_workdir(job_id) / "visual_states" / "all").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "no such visual state")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/evidence.md")
async def evidence_file(job_id: str):
    path = _workdir(job_id) / "evidence.md"
    if not path.is_file():
        raise HTTPException(404, "evidence pack not ready")
    return FileResponse(
        path,
        media_type="text/markdown",
        filename=f"{job_id}-evidence.md",
    )


@app.get("/api/jobs/{job_id}/evidence.zip")
async def evidence_zip(job_id: str):
    workdir = _workdir(job_id)
    try:
        path = handoff.export_zip(workdir)
    except handoff.EvidencePackError as exc:
        raise HTTPException(409, str(exc)) from exc
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{job_id}-evidence.zip",
    )


@app.post("/api/jobs/{job_id}/handoff")
async def send_to_knowledge_base(job_id: str):
    if store.config.knowledge_base_inbox is None:
        raise HTTPException(
            409,
            "Knowledge Base Inbox is not configured. Set CLIPMIND_KB_INBOX and restart.",
        )
    try:
        return handoff.send_to_inbox(
            _workdir(job_id), store.config.knowledge_base_inbox
        )
    except handoff.EvidencePackError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/jobs/{job_id}/note.md")
async def note_file(job_id: str):
    path = _workdir(job_id) / "note.md"
    if not path.exists():
        raise HTTPException(404, "note not ready")
    return FileResponse(path, media_type="text/markdown",
                        filename=f"{job_id}.md")


@app.get("/api/events")
async def events():
    async def stream():
        queue = store.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            store.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/health")
async def health():
    import shutil
    return {
        "yt_dlp": bool(shutil.which("yt-dlp")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ocr": store.providers.text.available(),
        "asr": store.providers.transcript.available(),
        "ocr_provider": store.providers.text.name,
        "asr_provider": store.providers.transcript.name,
        "knowledge_base_inbox": store.config.knowledge_base_inbox is not None,
        "supported_sources": supported_sources(),
        "max_upload_mb": store.config.max_upload_mb,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
