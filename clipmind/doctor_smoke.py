"""Offline, bounded-by-parent ASR smoke test for ``clipmind doctor --deep``."""
from __future__ import annotations

import json
import math
import tempfile
import wave
from pathlib import Path

from .config import Settings


def main() -> int:
    config = Settings.from_env()
    try:
        from faster_whisper import WhisperModel

        # Never download model weights during diagnostics. The parent also sets
        # HF_HUB_OFFLINE and imposes a process timeout.
        model = WhisperModel(
            config.faster_whisper_model,
            device=config.faster_whisper_device,
            compute_type=config.faster_whisper_compute_type,
            local_files_only=True,
        )
        with tempfile.TemporaryDirectory(prefix="clipmind-asr-smoke-") as tmp:
            audio = Path(tmp) / "tone.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                samples = bytearray()
                for index in range(32000):
                    value = int(12000 * math.sin(2 * math.pi * 440 * index / 16000))
                    samples.extend(value.to_bytes(2, "little", signed=True))
                output.writeframes(samples)
            segments, _ = model.transcribe(str(audio), language="en")
            list(segments)  # CTranslate2 can fail lazily during generation.
    except Exception as exc:  # noqa: BLE001 - native runtime errors are the result
        print(json.dumps({"passed": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"passed": True, "reason": "cached model loaded and audio decoded"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
