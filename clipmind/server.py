"""FastAPI app: paste text in, watch progress over SSE, read the note."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import handoff
from .config import WEB_DIR, settings
from .jobs import JobStore
from .links import extract_urls, guess_title, normalize_url

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


@app.post("/api/jobs")
async def submit(body: SubmitBody):
    urls = extract_urls(body.text)
    if not urls:
        raise HTTPException(400, "没有在这段文字里找到抖音链接")
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
        jobs.append(store.submit(url, guess_title(body.text, url) or url).public())
        active.add(cache_key)
    return {
        "jobs": jobs,
        "found": len(urls),
        "skipped": skipped,
        "reused": reused,
    }


@app.post("/api/jobs/{job_id}/reprocess")
async def reprocess(job_id: str):
    source = store.jobs.get(job_id)
    if not source:
        raise HTTPException(404, "no such job")
    if source.status in {"queued", "running"}:
        raise HTTPException(409, "This job is already processing.")
    return store.submit(source.url, source.title).public()


@app.get("/api/jobs")
async def listing():
    return {"jobs": store.listing(), "capacity": settings.max_videos}


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
    path = (store.workdir(job_id) / "keyframes" / name).resolve()
    root = (store.workdir(job_id) / "keyframes").resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise HTTPException(404, "no such frame")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/visual_states/preview/{name}")
async def visual_preview(job_id: str, name: str):
    root = (store.workdir(job_id) / "visual_states" / "preview").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "no such visual state")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/visual_states/all/{name}")
async def visual_state(job_id: str, name: str):
    root = (store.workdir(job_id) / "visual_states" / "all").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "no such visual state")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/evidence.md")
async def evidence_file(job_id: str):
    path = store.workdir(job_id) / "evidence.md"
    if not path.is_file():
        raise HTTPException(404, "evidence pack not ready")
    return FileResponse(
        path,
        media_type="text/markdown",
        filename=f"{job_id}-evidence.md",
    )


@app.get("/api/jobs/{job_id}/evidence.zip")
async def evidence_zip(job_id: str):
    workdir = store.workdir(job_id)
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
    if settings.knowledge_base_inbox is None:
        raise HTTPException(
            409,
            "Knowledge Base Inbox is not configured. Set CLIPMIND_KB_INBOX and restart.",
        )
    try:
        return handoff.send_to_inbox(
            store.workdir(job_id), settings.knowledge_base_inbox
        )
    except handoff.EvidencePackError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/jobs/{job_id}/note.md")
async def note_file(job_id: str):
    path = store.workdir(job_id) / "note.md"
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
    from . import asr, ocr
    import shutil
    return {
        "yt_dlp": bool(shutil.which("yt-dlp")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ocr": ocr.available(),
        "asr": asr.available(),
        "llm": bool(settings.anthropic_api_key),
        "summary_model": settings.summary_model if settings.anthropic_api_key else None,
        "knowledge_base_inbox": settings.knowledge_base_inbox is not None,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
