"""Central configuration. Everything tunable lives here."""
from __future__ import annotations

import os
from dataclasses import dataclass
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


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    )


@dataclass(frozen=True)
class Settings:
    # --- acquisition ---
    # Cookie sources tried in order. "-" means "no cookies at all".
    cookie_sources: tuple[str, ...] = ("chrome", "-")
    cookie_file: str | None = None
    fetch_format: str = "bv*+ba/b"

    # --- vision ---
    sample_fps: float = 2.0
    sample_width: int = 640
    # Canonical evidence and OCR use a readable size; low-res samples remain
    # the cheap input for change detection.
    evidence_width: int = 1280
    # dHash hamming distance below which two frames count as duplicates.
    dedupe_threshold: int = 6
    ocr_languages: tuple[str, ...] = ("zh-Hans", "zh-Hant", "en-US")

    # --- speech ---
    asr_model: str = "mlx-community/whisper-large-v3-turbo"
    asr_language: str | None = "zh"

    # --- concurrency ---
    # Distinct resource pools so one heavy stage cannot starve the others.
    max_videos: int = 4
    max_fetch: int = 4
    max_asr: int = 1  # single GPU
    max_ocr: int = 2

    # --- housekeeping ---
    keep_source_video: bool = False
    knowledge_base_inbox: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            cookie_sources=_csv("CLIPMIND_COOKIE_SOURCES", "chrome,-"),
            cookie_file=os.getenv("CLIPMIND_COOKIE_FILE") or None,
            fetch_format=os.getenv("CLIPMIND_FORMAT", "bv*+ba/b"),
            sample_fps=_float("CLIPMIND_SAMPLE_FPS", 2.0),
            sample_width=_int("CLIPMIND_SAMPLE_WIDTH", 640),
            evidence_width=_int("CLIPMIND_EVIDENCE_WIDTH", 1280),
            dedupe_threshold=_int("CLIPMIND_DEDUPE_THRESHOLD", 6),
            ocr_languages=_csv(
                "CLIPMIND_OCR_LANGUAGES", "zh-Hans,zh-Hant,en-US"
            ),
            asr_model=os.getenv(
                "CLIPMIND_ASR_MODEL", "mlx-community/whisper-large-v3-turbo"
            ),
            asr_language=os.getenv("CLIPMIND_ASR_LANGUAGE", "zh") or None,
            max_videos=_int("CLIPMIND_MAX_VIDEOS", 4),
            max_fetch=_int("CLIPMIND_MAX_FETCH", 4),
            max_asr=_int("CLIPMIND_MAX_ASR", 1),
            max_ocr=_int("CLIPMIND_MAX_OCR", 2),
            keep_source_video=os.getenv("CLIPMIND_KEEP_VIDEO", "0") == "1",
            knowledge_base_inbox=_optional_path("CLIPMIND_KB_INBOX"),
        )


settings = Settings.from_env()
