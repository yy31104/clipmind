#!/usr/bin/env python3
"""Check that the default ASR guard leaves a noisy, speech-free slide video with no transcript.

The silent-slides evaluation has no audio track at all, so it never reaches ASR.
Real slide recordings usually do have audio: room tone, fan noise or quiet music
under long silences, which is where Whisper tends to invent text (Koenecke et
al., "Careless Whisper", FAccT 2024). This evaluation builds two 30-second videos
from the generated four-slide sample:

- noise:   pink noise plus a quiet two-tone "music bed", no speech;
- control: the same bed with synthetic speech (espeak-ng) mixed in.

Both go through ClipMind's own audio extraction (`media.extract_audio`, 16 kHz
mono). The audio is then passed to the Silero VAD that faster-whisper runs when
`vad_filter=True`, with faster-whisper's default `VadOptions` - exactly what
ClipMind's default `CLIPMIND_ASR_VAD_FILTER=1` requests, since it passes no VAD
parameters.

When the VAD returns no speech, faster-whisper decodes an empty array (its
`collect_chunks` returns one zero-length chunk), so the transcript is empty
without running the language model. The control proves the VAD is not simply
returning nothing: it must find the synthetic speech.

The Whisper model itself is not run here, so the transcript *without* the guard
is not measured by this script. Pass `--decode` on a machine that can load the
model to add that comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import media  # noqa: E402
from clipmind.config import Settings  # noqa: E402
from clipmind.demo import build_sample_video  # noqa: E402

DURATION = 30
SPEECH_TEXT = "Alpha plan. Beta system. Gamma data. Delta review."
SPEECH_START = 4.0


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def _noise_bed(path: Path) -> None:
    """Pink noise plus a soft A3/E4 chord, normalized to a quiet but clearly non-silent level."""
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.06:duration={DURATION}:sample_rate=48000",
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={DURATION}:sample_rate=48000",
        "-f", "lavfi", "-i", f"sine=frequency=330:duration={DURATION}:sample_rate=48000",
        "-filter_complex",
        "[1]volume=0.03[a];[2]volume=0.02[b];[0][a][b]amix=inputs=3:normalize=0[out]",
        "-map", "[out]", "-ac", "1", str(path),
    ])


def _speech_over_bed(bed: Path, path: Path, workdir: Path) -> None:
    speech = workdir / "speech.wav"
    _run(["espeak-ng", "-v", "en", "-s", "140", "-w", str(speech), SPEECH_TEXT])
    delay_ms = int(SPEECH_START * 1000)
    _run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(bed), "-i", str(speech),
        "-filter_complex",
        f"[1]adelay={delay_ms}|{delay_ms},volume=0.8[s];[0][s]amix=inputs=2:normalize=0:duration=first[out]",
        "-map", "[out]", "-ac", "1", str(path),
    ])


def _mux(video: Path, audio: Path, path: Path) -> None:
    _run([
        "ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(video),
        "-i", str(audio), "-t", str(DURATION), "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ])


def _rms_dbfs(samples) -> float:
    import numpy as np

    rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
    return round(float(20 * np.log10(rms)), 1) if rms > 0 else float("-inf")


def _speech_chunks(wav: Path) -> tuple[list[dict], float, float]:
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

    audio = decode_audio(str(wav), sampling_rate=16000)
    chunks = get_speech_timestamps(audio, VadOptions())
    kept, _ = collect_chunks(audio, chunks)
    kept_seconds = sum(len(chunk) for chunk in kept) / 16000
    spans = [
        {"start": round(c["start"] / 16000, 2), "end": round(c["end"] / 16000, 2)} for c in chunks
    ]
    return spans, round(kept_seconds, 2), _rms_dbfs(audio)


def _sweep(workdir: Path) -> dict:
    """Where the default VAD stops being right, in both directions.

    false_speech: stationary noise and tones at rising levels, no speech at all.
    missed_speech: the synthetic sentence under rising noise, at three loudness
    levels; reports how many seconds of it the VAD keeps.
    """
    import numpy as np
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    def detect(path: Path) -> tuple[int, float, np.ndarray]:
        audio = decode_audio(str(path), sampling_rate=16000)
        spans = get_speech_timestamps(audio, VadOptions())
        return len(spans), round(sum(s["end"] - s["start"] for s in spans) / 16000, 1), audio

    false_speech = []
    for color in ("pink", "white", "brown"):
        for amplitude in (0.06, 0.15, 0.3, 0.6):
            for music in (0.0, 0.15):
                path = workdir / "sweep-noise.wav"
                _run([
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"anoisesrc=color={color}:amplitude={amplitude}:duration=30:sample_rate=48000",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=30:sample_rate=48000",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=30:sample_rate=48000",
                    "-filter_complex",
                    f"[1]volume={music}[a];[2]volume={round(music * 0.66, 3)}[b];[0][a][b]amix=inputs=3:normalize=0[o]",
                    "-map", "[o]", "-ac", "1", "-ar", "16000", str(path),
                ])
                spans, seconds, audio = detect(path)
                false_speech.append({
                    "noise": color, "noise_amplitude": amplitude, "tone_volume": music,
                    "rms_dbfs": _rms_dbfs(audio), "speech_spans": spans, "speech_seconds": seconds,
                })

    speech = workdir / "sweep-speech.wav"
    _run(["espeak-ng", "-v", "en", "-s", "140", "-w", str(speech), SPEECH_TEXT])
    clean = decode_audio(str(speech), sampling_rate=16000)
    missed_speech = []
    for color in ("pink", "white"):
        for amplitude in (0.06, 0.15, 0.3, 0.6):
            for volume in (0.8, 0.3, 0.1):
                path = workdir / "sweep-mix.wav"
                _run([
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"anoisesrc=color={color}:amplitude={amplitude}:duration=20:sample_rate=48000",
                    "-i", str(speech), "-filter_complex",
                    f"[1]adelay=4000|4000,volume={volume}[s];[0][s]amix=inputs=2:normalize=0:duration=first[o]",
                    "-map", "[o]", "-ac", "1", "-ar", "16000", str(path),
                ])
                spans, seconds, audio = detect(path)
                noise_rms = float(np.sqrt(np.mean(np.square(audio[:64000]))))
                speech_rms = float(np.sqrt(np.mean(np.square(clean * volume))))
                missed_speech.append({
                    "noise": color, "noise_amplitude": amplitude, "speech_volume": volume,
                    "snr_db": round(20 * float(np.log10(speech_rms / max(noise_rms, 1e-9))), 1),
                    "speech_spans": spans, "speech_seconds_kept": seconds,
                })
    return {"false_speech": false_speech, "missed_speech": missed_speech}


def _decode(wav: Path, *, vad_filter: bool, threshold: float | None) -> list[str]:
    from faster_whisper import WhisperModel

    model = WhisperModel(Settings().faster_whisper_model, device="auto", compute_type="auto")
    segments, _ = model.transcribe(
        str(wav),
        language=Settings().asr_language,
        condition_on_previous_text=False,
        word_timestamps=True,
        vad_filter=vad_filter,
        hallucination_silence_threshold=threshold,
    )
    return [segment.text.strip() for segment in segments if segment.text.strip()]


async def evaluate(decode: bool, sweep: bool) -> dict:
    for name in ("CLIPMIND_ASR_VAD_FILTER", "CLIPMIND_ASR_HALLUCINATION_SILENCE_THRESHOLD"):
        os.environ.pop(name, None)
    defaults = Settings.from_env()
    with tempfile.TemporaryDirectory(prefix="clipmind-noisy-eval-") as tempdir:
        root = Path(tempdir)
        slides = build_sample_video(root)
        bed, control_audio = root / "bed.wav", root / "control.wav"
        _noise_bed(bed)
        _speech_over_bed(bed, control_audio, root)
        cases = {}
        for name, audio in (("noise", bed), ("control", control_audio)):
            video = root / f"{name}.mp4"
            _mux(slides, audio, video)
            wav = await media.extract_audio(video, root / f"{name}-16k.wav")
            if wav is None:
                raise RuntimeError(f"{name}: ClipMind found no audio track")
            spans, kept, level = _speech_chunks(wav)
            case = {
                "audio_rms_dbfs": level,
                "vad_speech_spans": spans,
                "seconds_decoded_with_default_vad": kept,
            }
            if decode:
                case["transcript_default_guards"] = _decode(
                    wav,
                    vad_filter=defaults.asr_vad_filter,
                    threshold=defaults.asr_hallucination_silence_threshold,
                )
                case["transcript_guards_off"] = _decode(wav, vad_filter=False, threshold=None)
            cases[name] = case
        sweep_result = _sweep(root) if sweep else None

    noise, control = cases["noise"], cases["control"]
    checks = {
        "defaults_enable_vad": defaults.asr_vad_filter is True,
        "defaults_set_silence_threshold": defaults.asr_hallucination_silence_threshold == 2.0,
        "noise_is_audible_not_digital_silence": noise["audio_rms_dbfs"] > -45,
        "noise_has_no_detected_speech": noise["vad_speech_spans"] == [],
        "noise_decodes_zero_seconds_so_transcript_is_empty": (
            noise["seconds_decoded_with_default_vad"] == 0
        ),
        "control_speech_is_detected": bool(control["vad_speech_spans"]),
        "control_speech_found_where_it_was_placed": any(
            span["start"] <= SPEECH_START + 1.0 and span["end"] >= SPEECH_START + 1.0
            for span in control["vad_speech_spans"]
        ),
    }
    if decode:
        checks["decoded_noise_transcript_empty_with_defaults"] = (
            noise["transcript_default_guards"] == []
        )
    return {
        "fixture": "generated-four-slide-video-30s-with-noise-bed",
        "passed": all(checks.values()),
        "checks": checks,
        "cases": cases,
        "settings": {
            "vad_filter": defaults.asr_vad_filter,
            "hallucination_silence_threshold": defaults.asr_hallucination_silence_threshold,
            "vad_options": "faster_whisper.vad.VadOptions() defaults",
        },
        "model_decoding": "run" if decode else "not run (VAD stage only)",
        "sweep": sweep_result,
        "tools": {
            "faster_whisper": _version("faster_whisper"),
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "espeak_ng": shutil.which("espeak-ng") is not None,
        },
    }


def _version(module: str) -> str | None:
    try:
        return __import__(module).__version__
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--decode",
        action="store_true",
        help="Also run the Whisper model with guards on and off (downloads the model).",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Also measure false speech on louder noise and missed speech at lower SNR.",
    )
    args = parser.parse_args()
    for tool in ("ffmpeg", "espeak-ng"):
        if shutil.which(tool) is None:
            print(f"{tool} is required for this evaluation", file=sys.stderr)
            return 2
    report = asyncio.run(evaluate(args.decode, args.sweep))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
