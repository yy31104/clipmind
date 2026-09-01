"""FastAPI app: paste text in, watch progress over SSE, read the note."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import OUT_DIR, WEB_DIR, settings
from .jobs import JobStore
from .links import extract_urls, guess_title

app = FastAPI(title="ClipMind")
store = JobStore()


class SubmitBody(BaseModel):
    text: str


@app.post("/api/jobs")
async def submit(body: SubmitBody):
    urls = extract_urls(body.text)
    if not urls:
        raise HTTPException(400, "没有在这段文字里找到抖音链接")
    active = {j.url for j in store.jobs.values() if j.status in ("queued", "running")}
    jobs, skipped = [], 0
    for url in urls:
        if url in active:
            skipped += 1
            continue
        jobs.append(store.submit(url, guess_title(body.text, url) or url).public())
    return {"jobs": jobs, "found": len(urls), "skipped": skipped}


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
    root = (OUT_DIR / job_id / "keyframes").resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise HTTPException(404, "no such frame")
    return FileResponse(path)


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
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
