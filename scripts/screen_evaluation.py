"""Versioned screen metrics and immutable evaluation-run records.

This module does not extract media or infer error causes. Source-frame review
supplies the attribution; an unexamined event stays unreviewed.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading

PROTOCOL = "screen-evidence-v1"
NON_INFORMATION_KINDS = frozenset({"layout", "speaker", "animation"})
CAUSES = (
    "progressive_build", "speaker", "layout", "animation", "small_text",
    "background_text", "reading_order", "transition", "duplicate", "timing",
    "other", "unresolved",
)


def boundary_inventory(reference: list[float], states: list[dict], *,
                       start: float, end: float, tolerance: float = 1.0,
                       information_only: bool = False) -> dict:
    """Keep every event while using the existing sorted one-to-one matcher.

    Missing or unfamiliar change kinds are included conservatively. Only an
    explicit non-information kind without extraction warnings can remove a
    state from the information score; it remains in the canonical control.
    """
    truth = [{"id": f"reference-{index:04d}", "timestamp": float(value)}
             for index, value in enumerate(sorted(reference), 1)]
    guesses = []
    for index, state in enumerate(states, 1):
        timestamp = float(state["start"])
        if not start < timestamp < end:
            continue
        kind = state.get("change_kind")
        warning = any(state.get(key) for key in ("dedupe_warning", "ocr_warning", "ocr_error", "error"))
        included = (not information_only or warning or not isinstance(kind, str)
                    or kind not in NON_INFORMATION_KINDS)
        guesses.append({"id": f"prediction-{index:04d}", "timestamp": timestamp,
                        "visual_state_id": state.get("id"), "change_kind": kind,
                        "extraction_warning": warning, "included": included})
    guesses.sort(key=lambda row: row["timestamp"])
    eligible = [row for row in guesses if row["included"]]
    i = j = 0
    matches = []
    while i < len(truth) and j < len(eligible):
        expected, predicted = truth[i], eligible[j]
        if abs(expected["timestamp"] - predicted["timestamp"]) <= tolerance:
            matches.append({"reference_id": expected["id"], "prediction_id": predicted["id"],
                            "reference_timestamp": expected["timestamp"],
                            "prediction_timestamp": predicted["timestamp"]})
            i += 1
            j += 1
        elif predicted["timestamp"] < expected["timestamp"]:
            j += 1
        else:
            i += 1
    found_truth = {row["reference_id"] for row in matches}
    found_guesses = {row["prediction_id"] for row in matches}
    errors = []
    for kind, rows, matched in (("missed_change", truth, found_truth),
                                ("extra_boundary", eligible, found_guesses)):
        for row in rows:
            if row["id"] not in matched:
                errors.append({"event_id": f"{kind}-{row['id']}", "event_kind": kind,
                               "timestamp": row["timestamp"], "status": "unreviewed",
                               "cause": "", "explanation": "", "source_frames": []})
    score = {"reference_count": len(truth), "prediction_count": len(eligible),
             "matched": len(matches),
             "matches": [[row["reference_timestamp"], row["prediction_timestamp"]] for row in matches],
             "tolerance_seconds": tolerance,
             "recall": len(matches) / len(truth) if truth else None,
             "precision": len(matches) / len(eligible) if eligible else None}
    return {"protocol": PROTOCOL, "score": score, "references": truth,
            "predictions": guesses, "matches": matches, "errors": errors}


def reserve_record(path: Path, record: dict) -> None:
    """Claim a new output or campaign token without replacing an older run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def case_source_url(case: dict) -> str:
    for key in ("page_url", "source_page", "source_url"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def safe_report(value, workdir: Path):
    """Remove machine-specific paths from a shareable result, including errors."""
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        value = value.replace(str(workdir.resolve()), "<WORKDIR>")
        # A relative workdir of '.' must not replace punctuation in every string.
        return value.replace(str(workdir), "<WORKDIR>") if workdir.is_absolute() else value
    if isinstance(value, list):
        return [safe_report(item, workdir) for item in value]
    if isinstance(value, tuple):
        return [safe_report(item, workdir) for item in value]
    if isinstance(value, dict):
        return {key: safe_report(item, workdir) for key, item in value.items()}
    return value


def resolve_pack_path(value: str, workdir: Path) -> Path:
    path = Path(value.replace("<WORKDIR>", str(workdir.resolve()))).resolve()
    if not path.is_relative_to(workdir.resolve()):
        raise ValueError("rescore pack must be inside --workdir")
    return path


def attribution_summary(events: list[dict]) -> dict:
    """Reject incomplete claims rather than invent categories from timestamps."""
    counts = Counter()
    for row in events:
        if row.get("status") != "reviewed":
            counts["unreviewed"] += 1
            continue
        if (row.get("cause") not in CAUSES or not row.get("explanation", "").strip()
                or len(row.get("source_frames", [])) < 2):
            raise ValueError(f"{row.get('event_id')}: reviewed attribution needs a cause, explanation and source frame pair")
        counts[row["cause"]] += 1
    return {"event_count": len(events), "reviewed": len(events) - counts["unreviewed"],
            "causes": dict(sorted(counts.items()))}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tesseract_metadata() -> dict:
    """Collect optional engine/model provenance without recognizing an image."""
    executable = shutil.which("tesseract")
    if executable is None:
        return {"status": "unavailable", "executable": "tesseract"}
    try:
        version = subprocess.run([executable, "--version"], check=True, capture_output=True,
                                 text=True, timeout=10)
        languages = subprocess.run([executable, "--list-langs"], check=True, capture_output=True,
                                   text=True, timeout=10)
        details = (version.stdout or version.stderr).strip().splitlines()
        lines = (languages.stdout or languages.stderr).strip().splitlines()
        header = next((n for n, line in enumerate(lines) if "List of available languages" in line), None)
        available = [line.strip() for line in lines[header + 1:] if line.strip()] if header is not None else []
        match = re.search(r'List of available languages in "([^"]+)"', "\n".join(lines))
        hashes, failures = {}, {}
        if match:
            directory = Path(match.group(1))
            for language in available:
                if not re.fullmatch(r"[A-Za-z0-9_/-]+", language) or ".." in Path(language).parts:
                    continue
                try:
                    hashes[language] = sha256(directory / f"{language}.traineddata")
                except OSError as exc:
                    failures[language] = type(exc).__name__
        return {"status": "measured", "version": details[0] if details else "unknown",
                "version_details": details, "available_languages": sorted(available),
                "traineddata_sha256": hashes, "traineddata_errors": failures,
                "traineddata_status": "measured" if match and len(hashes) == len(available) else "incomplete"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "executable": "tesseract", "error_type": type(exc).__name__}


def ocr_exception_category(exc: Exception) -> tuple[str, str, str, tuple]:
    """Keep finite diagnostic types, never exception text or image paths."""
    text = str(exc)
    lower = text.casefold()
    if isinstance(exc, TimeoutError) or any(value in lower for value in ("deadline", "timed out", "timeout")):
        category = "recognition_deadline"
    elif "could not start" in lower:
        category = "worker_start"
    elif any(value in lower for value in ("invalid output", "valid result", "returning a result")):
        category = "worker_protocol_or_exit"
    elif "unavailable" in lower:
        category = "provider_unavailable"
    else:
        category = "recognition_error"
    cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else ""
    if not cause:
        match = re.match(r"([A-Za-z_][A-Za-z0-9_.]{0,79}):", text)
        cause = match.group(1) if match else ""
    diagnostic_text = "\n".join([text, *getattr(exc, "__notes__", [])])
    domains = tuple(sorted(set(re.findall(r"(?:Error\s+)?Domain=([A-Za-z0-9_.]+)\s+Code=(-?\d+)", diagnostic_text))))
    return category, type(exc).__name__, cause, domains


class OCRDiagnosticProxy:
    """Observe the same real provider calls without retries or altered results."""

    def __init__(self, recognizer) -> None:
        self._recognizer = recognizer
        self._lock = threading.Lock()
        self._started = self._successful = 0
        self._errors = Counter()

    def __getattr__(self, name):
        attribute = getattr(self._recognizer, name)
        if name not in {"recognize", "read_text"} or not callable(attribute):
            return attribute

        def observed(*args, **kwargs):
            with self._lock:
                self._started += 1
            try:
                result = attribute(*args, **kwargs)
            except Exception as exc:
                with self._lock:
                    self._errors[ocr_exception_category(exc)] += 1
                raise
            with self._lock:
                self._successful += 1
            return result
        return observed

    def snapshot(self) -> dict:
        with self._lock:
            errors = [{"category": key[0], "exception_type": key[1], "cause_type": key[2],
                       "native_error_codes": [{"domain": domain, "code": int(code)} for domain, code in key[3]],
                       "count": count} for key, count in sorted(self._errors.items())]
            failed = sum(row["count"] for row in errors)
            return {"provider": getattr(self._recognizer, "name", type(self._recognizer).__name__),
                    "unit": "frame_recognition_calls", "started": self._started,
                    "successful": self._successful, "failed": failed,
                    "unfinished": self._started - self._successful - failed,
                    "failure_types": errors}
