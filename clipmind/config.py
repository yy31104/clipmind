"""Central configuration. Everything tunable lives here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.getenv("CLIPMIND_OUT", ROOT / "out"))
WEB_DIR = ROOT / "web"


def _optional_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- acquisition ---
    # Cookie sources tried in order. "-" means "no cookies at all".
    cookie_sources: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip()
            for s in os.getenv("CLIPMIND_COOKIE_SOURCES", "chrome,-").split(",")
            if s.strip()
        )
    )
    cookie_file: str | None = os.getenv("CLIPMIND_COOKIE_FILE") or None
    fetch_format: str = os.getenv("CLIPMIND_FORMAT", "bv*+ba/b")

    # --- vision ---
    sample_fps: float = float(os.getenv("CLIPMIND_SAMPLE_FPS", "2"))
    sample_width: int = _int("CLIPMIND_SAMPLE_WIDTH", 640)
    # Canonical evidence and OCR use a readable size; low-res samples remain
    # the cheap input for change detection.
    evidence_width: int = _int("CLIPMIND_EVIDENCE_WIDTH", 1280)
    # dHash hamming distance below which two frames count as duplicates.
    dedupe_threshold: int = _int("CLIPMIND_DEDUPE_THRESHOLD", 6)
    max_keyframes: int = _int("CLIPMIND_MAX_KEYFRAMES", 10)
    ocr_languages: tuple[str, ...] = ("zh-Hans", "zh-Hant", "en-US")

    # --- speech ---
    asr_model: str = os.getenv(
        "CLIPMIND_ASR_MODEL", "mlx-community/whisper-large-v3-turbo"
    )
    asr_language: str | None = os.getenv("CLIPMIND_ASR_LANGUAGE", "zh") or None

    # --- summarisation ---
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    summary_model: str = os.getenv("CLIPMIND_SUMMARY_MODEL", "claude-sonnet-5")

    # --- concurrency ---
    # Distinct resource pools so one heavy stage cannot starve the others.
    max_videos: int = _int("CLIPMIND_MAX_VIDEOS", 4)
    max_fetch: int = _int("CLIPMIND_MAX_FETCH", 4)
    max_asr: int = _int("CLIPMIND_MAX_ASR", 1)  # single GPU
    max_ocr: int = _int("CLIPMIND_MAX_OCR", 2)
    max_llm: int = _int("CLIPMIND_MAX_LLM", 4)

    # --- housekeeping ---
    keep_source_video: bool = os.getenv("CLIPMIND_KEEP_VIDEO", "0") == "1"
    knowledge_base_inbox: Path | None = field(
        default_factory=lambda: _optional_path("CLIPMIND_KB_INBOX")
    )


settings = Settings()
