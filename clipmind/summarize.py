"""Fuse transcript + on-screen text into a study note.

An LLM does this well, but the tool must stay useful without an API key, so a
deterministic fallback builds a structured note from the same material.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from .asr import Transcript
from .config import settings
from .media import Frame

SYSTEM = """You turn short videos into study notes. You are given a speech
transcript with timestamps and text read off the screen (subtitles, slides,
code). Both are noisy: ASR mishears terms and OCR garbles characters. Use each
to correct the other.

Write the note in the same language the video is in. Output Markdown with
exactly these sections:

## 摘要 / Summary
Three to five sentences on what the video actually argues or teaches.

## 要点 / Key points
Four to eight bullets. Concrete claims, not topic labels. Prefix each with the
timestamp where it is made, as [mm:ss].

## 术语 / Terms
Technical terms, tools, or named concepts that appear, each with a one-line
gloss. Omit the section entirely if there are none.

## 可行动 / Takeaways
Two to four things a viewer could actually do next. Omit if the video is not
actionable.

Do not invent anything absent from the material. If the input is too thin to
support a section, say so plainly instead of padding."""


@dataclass
class Summary:
    markdown: str
    engine: str
    error: str | None = None


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def build_context(transcript: Transcript, frames: list[Frame], title: str,
                  duration: float) -> str:
    parts = [f"TITLE: {title}", f"DURATION: {_clock(duration)}", ""]

    parts.append("SPEECH TRANSCRIPT:")
    if transcript.segments:
        parts += [f"[{_clock(s.start)}] {s.text}" for s in transcript.segments]
    else:
        parts.append(f"(none - {transcript.error or 'no speech detected'})")

    parts += ["", "ON-SCREEN TEXT (OCR, in order):"]
    on_screen = [f for f in frames if f.text.strip()]
    if on_screen:
        for frame in on_screen:
            flat = " / ".join(line for line in frame.lines if line.strip())
            parts.append(f"[{_clock(frame.timestamp)}] {flat}")
    else:
        parts.append("(no legible on-screen text)")

    return "\n".join(parts)


def _fallback(transcript: Transcript, frames: list[Frame]) -> Summary:
    """No API key: assemble the material into something still worth reading."""
    lines = ["## 摘要 / Summary", ""]
    if transcript.segments:
        head = transcript.text
        lines.append(head[:400] + ("…" if len(head) > 400 else ""))
    else:
        lines.append(
            f"没有可用的语音内容（{transcript.error or '未检测到语音'}）。"
            "以下要点来自画面文字。"
        )

    lines += ["", "## 要点 / Key points", ""]
    bullets = 0
    for frame in frames:
        if not frame.text.strip() or frame.novelty < 4:
            continue
        flat = " ".join(line for line in frame.lines if line.strip())
        lines.append(f"- [{_clock(frame.timestamp)}] {flat[:120]}")
        bullets += 1
        if bullets >= 8:
            break
    if not bullets:
        lines.append("- 画面中没有识别到足够的文字信息。")

    lines += [
        "",
        "> 未配置 `ANTHROPIC_API_KEY`，这份笔记由转写与 OCR 直接拼接生成，"
        "没有经过模型归纳。配置 key 后重跑即可得到真正的摘要。",
    ]
    return Summary(markdown="\n".join(lines), engine="fallback (no API key)")


def _client():
    from anthropic import AsyncAnthropic

    kwargs: dict = {"api_key": settings.anthropic_api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncAnthropic(**kwargs)


async def summarize(transcript: Transcript, frames: list[Frame], title: str,
                    duration: float, semaphore: asyncio.Semaphore) -> Summary:
    context = build_context(transcript, frames, title, duration)

    if not settings.anthropic_api_key:
        return _fallback(transcript, frames)

    try:
        async with semaphore:
            client = _client()
            response = await client.messages.create(
                model=settings.summary_model,
                max_tokens=2000,
                system=SYSTEM,
                messages=[{"role": "user", "content": context}],
            )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        if not text:
            raise RuntimeError("empty response")
        return Summary(markdown=text, engine=settings.summary_model)
    except Exception as exc:  # noqa: BLE001
        note = _fallback(transcript, frames)
        return Summary(
            markdown=note.markdown,
            engine="fallback (LLM failed)",
            error=f"{type(exc).__name__}: {exc}",
        )
