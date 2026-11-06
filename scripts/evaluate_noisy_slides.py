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

Without `--decode`, this is a VAD-only evaluation, not a transcription result.
With `--decode`, the production FasterWhisperProvider processes the same audio
with guards off, VAD only, and the default VAD plus silence threshold. VAD may
legitimately skip inference for the speech-free case. The speech control must
still be transcribed, so an empty transcript or a provider error cannot pass.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import hashlib
from importlib import metadata
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipmind import asr, media  # noqa: E402
from clipmind.config import Settings  # noqa: E402
from clipmind.demo import build_sample_video  # noqa: E402

DURATION = 30
SPEECH_TEXT = "Alpha plan. Beta system. Gamma data. Delta review."
SPEECH_START = 4.0
NOISE_SEED = 1729
DECODE_VARIANTS = (
    ("guards_off", False, None),
    ("vad_only", True, None),
    ("default_guards", True, 2.0),
)


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def _noise_bed(path: Path) -> None:
    """Pink noise plus a soft A3/E4 chord, normalized to a quiet but clearly non-silent level."""
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.06:duration={DURATION}:sample_rate=48000:seed={NOISE_SEED}",
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
                    "-f", "lavfi", "-i", f"anoisesrc=color={color}:amplitude={amplitude}:duration=30:sample_rate=48000:seed={NOISE_SEED}",
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
                    "-f", "lavfi", "-i", f"anoisesrc=color={color}:amplitude={amplitude}:duration=20:sample_rate=48000:seed={NOISE_SEED}",
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


async def _decode(wav: Path, config: Settings) -> dict:
    """Exercise the shipping provider, including consumption of lazy segments."""
    started = time.perf_counter()
    transcript = await asr.FasterWhisperProvider(config).transcribe(wav)
    elapsed = time.perf_counter() - started
    if transcript.error:
        raise RuntimeError(f"Whisper evaluation failed: {transcript.error}")
    return {
        "status": "ok",
        "error": None,
        "engine": transcript.engine,
        "language": transcript.language,
        "text": transcript.text,
        "segments": [asdict(segment) for segment in transcript.segments],
        "elapsed_seconds": round(elapsed, 4),
        "settings": {
            "vad_filter": config.asr_vad_filter,
            "hallucination_silence_threshold": config.asr_hallucination_silence_threshold,
        },
    }


async def _decode_variants(wav: Path, model: str) -> dict:
    # The synthetic sentence is English. Production's default language is zh,
    # which is not an appropriate evaluation setting for this fixture.
    base = replace(
        Settings(), asr_provider="faster-whisper", asr_language="en",
        faster_whisper_model=model,
    )
    results = {}
    for name, vad, threshold in DECODE_VARIANTS:
        config = replace(
            base, asr_vad_filter=vad,
            asr_hallucination_silence_threshold=threshold,
        )
        started = time.perf_counter()
        try:
            results[name] = await _decode(wav, config)
        except RuntimeError as exc:
            # Keep the failed row and continue the comparison. A provider error
            # is never an empty successful transcript, even for noise.
            results[name] = {
                "status": "error",
                "error": str(exc),
                "engine": "faster-whisper",
                "language": None,
                "text": None,
                "segments": [],
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "settings": {
                    "vad_filter": vad,
                    "hallucination_silence_threshold": threshold,
                },
            }
    return results


def _words(text: str) -> set[str]:
    # Whisper sometimes joins correctly capitalized words, e.g. AlphaPlan.
    # Split only an observed case boundary; do not repair misspelled words.
    separated = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return set(re.findall(r"[a-z]+", separated.casefold()))


def _control_word_measurements(cases: dict) -> dict:
    """Measure the negative baseline without requiring perfect recognition."""
    expected = _words(SPEECH_TEXT)
    measurements = {}
    for name, _, _ in DECODE_VARIANTS:
        row = cases["control"]["decoding"][name]
        words = _words(row["text"] or "")
        missing = expected - words
        measurements[name] = {
            "all_expected_words_present": row["status"] == "ok" and not missing,
            "missing_expected_words": sorted(missing),
            "expected_unique_word_coverage": round(len(expected & words) / len(expected), 4),
        }
    return measurements


def _decode_checks(cases: dict) -> dict[str, bool]:
    noise = cases["noise"]["decoding"]["default_guards"]
    checks = {
        "decoded_noise_transcript_empty_with_defaults": (
            noise["status"] == "ok" and not noise["text"].strip()
        ),
    }
    measurements = _control_word_measurements(cases)
    for name, _, _ in DECODE_VARIANTS:
        control = cases["control"]["decoding"][name]
        checks[f"{name}_retains_control_speech"] = (
            control["status"] == "ok" and bool(control["text"].strip())
        )
        # This paired test measures whether guards erase speech relative to the
        # same unguarded decode, not perfect ASR accuracy. Recognition errors in
        # every row stay visible in the word measurements and raw transcript.
        if name != "guards_off":
            coverage = measurements[name]["expected_unique_word_coverage"]
            baseline = measurements["guards_off"]["expected_unique_word_coverage"]
            checks[f"{name}_control_word_coverage_not_worse_than_baseline"] = (
                control["status"] == "ok" and coverage > 0 and coverage >= baseline
            )
        checks[f"{name}_retains_control_timing"] = control["status"] == "ok" and any(
            segment["start"] <= SPEECH_START + 1.0
            and segment["end"] >= SPEECH_START + 1.0
            for segment in control["segments"]
        )
    return checks


def _decoder_provenance() -> dict:
    """Identify an unreleased dependency fix even if its version is unchanged."""
    result = {"archive_url": None, "archive_sha256": None, "transcribe_source_sha256": None}
    try:
        package_file = getattr(__import__("faster_whisper"), "__file__", None)
        if package_file:
            source = Path(package_file).with_name("transcribe.py")
            if source.is_file():
                with source.open("rb") as handle:
                    result["transcribe_source_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
        direct = json.loads(metadata.distribution("faster-whisper").read_text("direct_url.json") or "{}")
    except (ImportError, OSError, ValueError, metadata.PackageNotFoundError):
        return result
    parsed = urlsplit(direct.get("url", ""))
    if parsed.scheme == "https" and parsed.netloc == "github.com" and parsed.path.casefold().startswith(
        "/systran/faster-whisper/archive/"
    ):
        # Only the public upstream archive is reportable: omit arbitrary local
        # install paths, credentials, query strings, and other repository URLs.
        result["archive_url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        sha256 = direct.get("archive_info", {}).get("hashes", {}).get("sha256")
        if isinstance(sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            result["archive_sha256"] = sha256
    return result


def _model_metadata(model: str) -> dict:
    # These are observations of the instance the production provider loaded;
    # device='auto' alone would not identify the backend actually used.
    loaded = asr._faster_models.get(model)
    backend = getattr(loaded, "model", None)
    local = Path(model).expanduser()
    revision = local.name if local.parent.name == "snapshots" else None
    assets = {}
    for name in ("model.bin", "config.json"):
        path = local / name
        if path.is_file():
            with path.open("rb") as handle:
                assets[name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return {
        "requested_model": model,
        "local_snapshot_revision": revision,
        "local_model_sha256": assets,
        "language": "en",
        "provider_entry": "clipmind.asr.FasterWhisperProvider.transcribe",
        "device": getattr(backend, "device", None),
        "compute_type": getattr(backend, "compute_type", None),
        "faster_whisper": _version("faster_whisper"),
        "faster_whisper_source": _decoder_provenance(),
        "ctranslate2": _version("ctranslate2"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "timing_note": (
            "Sequential calls share the production model cache; the first call "
            "may include model loading. Times are not a speed comparison."
        ),
    }


async def evaluate(decode: bool, sweep: bool, model: str | None = None) -> dict:
    # Evaluate declared defaults, independent of the user's environment or .env.
    defaults = Settings()
    model = model or defaults.faster_whisper_model
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
                "audio_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
                "audio_rms_dbfs": level,
                "vad_speech_spans": spans,
                "seconds_decoded_with_default_vad": kept,
            }
            if decode:
                case["decoding"] = await _decode_variants(wav, model)
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
    decode_errors = []
    if decode:
        checks.update(_decode_checks(cases))
        decode_errors = [
            {"case": case_name, "variant": name, "error": row["error"]}
            for case_name, case in cases.items()
            for name, row in case["decoding"].items()
            if row["status"] != "ok"
        ]
    guard_checks_passed = all(checks.values())
    if decode:
        checks["all_decodes_succeeded"] = not decode_errors
    return {
        "fixture": "generated-four-slide-video-30s-with-noise-bed",
        "passed": all(checks.values()),
        "checks": checks,
        "guard_checks_passed": guard_checks_passed,
        "measurements": {
            "control_word_coverage": _control_word_measurements(cases),
        } if decode else None,
        "decode_errors": decode_errors,
        "all_decodes_succeeded": not decode_errors if decode else None,
        "cases": cases,
        "settings": {
            "vad_filter": defaults.asr_vad_filter,
            "hallucination_silence_threshold": defaults.asr_hallucination_silence_threshold,
            "vad_options": "faster_whisper.vad.VadOptions() defaults",
            "noise_seed": NOISE_SEED,
            "speech_text": SPEECH_TEXT,
            "speech_start_seconds": SPEECH_START,
        },
        "model_decoding": "production provider run" if decode else "not run (VAD stage only)",
        "model": _model_metadata(model) if decode else None,
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
        help="Run the production Whisper provider with guards off, VAD only, and defaults.",
    )
    parser.add_argument(
        "--model", default=Settings().faster_whisper_model,
        help="faster-whisper model name or local model directory (may download if not cached).",
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
    report = asyncio.run(evaluate(args.decode, args.sweep, args.model))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
