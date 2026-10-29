"""Read-only artifact integrity audit; no claim of semantic/OCR correctness."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path, PurePosixPath

from PIL import Image

from .evidence import EvidencePackError, load_complete_pack


def audit_pack(pack_dir: Path, *, decode_images: bool = True, check_job: bool = True) -> dict:
    root = Path(pack_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    report = {"audit_version": 1, "passed": False,
              "scope": "artifact_integrity_not_semantic_accuracy",
              "images_decoded": decode_images, "job_checked": check_job,
              "errors": errors, "warnings": warnings}

    def problem(message):
        # Bound diagnostics for damaged libraries without echoing source text,
        # signed URLs, paths, or exception messages into a shareable report.
        if len(errors) < 100:
            errors.append(message)

    def safe_path(value, *, image=False):
        if not isinstance(value, str) or "\\" in value or ":" in value:
            raise ValueError("invalid path")
        relative = PurePosixPath(value)
        if (relative.is_absolute() or ".." in relative.parts
                or (image and (len(relative.parts) != 3
                               or relative.parts[:2] not in {("visual_states", "all"),
                                                            ("visual_states", "preview")}))):
            raise ValueError("invalid path")
        path = root.joinpath(*relative.parts)
        if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
            raise ValueError("linked artifact")
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("missing artifact")
        return path

    def rows(name):
        try:
            values = [json.loads(line) for line in safe_path(name).read_text(encoding="utf-8").splitlines()
                      if line.strip()]
            if not all(isinstance(row, dict) for row in values):
                raise ValueError("row type")
            return values
        except (OSError, UnicodeError, ValueError, RecursionError):
            problem(f"Invalid or unreadable {name}")
            return []

    def identities(values, label):
        ids = [row.get("id") for row in values]
        if any(not isinstance(value, str) or not value for value in ids):
            problem(f"Missing/invalid {label} identity")
        valid = [value for value in ids if isinstance(value, str)]
        if len(set(valid)) != len(valid):
            problem(f"Duplicate {label} identity")
        return {row["id"]: row for row in values if isinstance(row.get("id"), str)}

    def finite(value):
        try:
            return type(value) in {int, float} and math.isfinite(value) and value >= 0
        except OverflowError:
            return False

    def sha256(path):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    try:
        manifest = load_complete_pack(root)
        source = json.loads(safe_path("source.json").read_text(encoding="utf-8"))
        if not isinstance(source, dict):
            raise ValueError("source type")
    except (EvidencePackError, OSError, UnicodeError, ValueError, RecursionError):
        problem("Invalid completion manifest or source record")
        return report
    identity = manifest.get("source")
    if (not isinstance(identity, dict) or not source.get("source_id") or not source.get("platform")
            or identity.get("id") != source.get("source_id")
            or identity.get("platform") != source.get("platform")):
        problem("Manifest/source identity mismatch")
    if check_job:
        try:
            payload = json.loads(safe_path("job.json").read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("job"), dict):
                raise ValueError("job type")
            if payload["job"].get("status") != "done":
                problem("Job state is not done; startup recovery may be required")
        except (OSError, UnicodeError, ValueError, RecursionError):
            problem("Invalid or unreadable job.json")
    speech = rows("transcript.jsonl")
    timeline = rows("visual_timeline.jsonl")
    ocr = rows("ocr.jsonl")
    speech_by_id = identities(speech, "transcript")
    visual_by_id = identities(timeline, "visual")
    ocr_by_id = identities(ocr, "OCR")
    duration = source.get("duration", 0)
    previous = -1.0
    for position, row in enumerate(speech):
        start, end = row.get("start"), row.get("end")
        if not finite(start) or not finite(end) or end < start or start < previous:
            problem(f"Invalid transcript time/order at row {position + 1}")
        else:
            previous = start
            if finite(duration) and duration > 0 and end > duration + 1:
                problem(f"Transcript beyond source duration at row {position + 1}")
        if not isinstance(row.get("text"), str):
            problem(f"Invalid transcript text at row {position + 1}")
    digests = Counter()
    checked_paths = set()
    previews = 0
    previous = -1.0
    for position, row in enumerate(timeline):
        label = f"visual row {position + 1}"
        start, end = row.get("start"), row.get("end")
        if not finite(start) or not finite(end) or end < start or start < previous:
            problem(f"Invalid time/order in {label}")
        else:
            previous = start
        ref = row.get("ocr_ref")
        text = ocr_by_id.get(ref) if isinstance(ref, str) else None
        if not text or text.get("visual_state_ref") != row.get("id") or text.get("timestamp") != start:
            problem(f"Broken OCR association in {label}")
        refs = row.get("transcript_refs", [])
        if (not isinstance(refs, list)
                or any(not isinstance(ref, str) or ref not in speech_by_id for ref in refs)):
            problem(f"Broken transcript association in {label}")
        images = [row.get("file")]
        canonical_digest = None
        if row.get("in_preview"):
            previews += 1
            images.append(row.get("preview_file"))
        for number, value in enumerate(images):
            try:
                path = safe_path(value, image=True)
                digest = sha256(path)
                if number == 0:
                    canonical_digest = digest
                    digests[digest] += 1
                elif canonical_digest is not None and digest != canonical_digest:
                    problem(f"Preview differs from its canonical image in {label}")
                if path in checked_paths:
                    continue
                checked_paths.add(path)
                if decode_images:
                    with Image.open(path) as image:
                        image.verify()
                    with Image.open(path) as image:
                        image.load()
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                problem(f"Missing, unsafe, or unreadable image in {label}")
    for position, row in enumerate(ocr):
        if not isinstance(row.get("visual_state_ref"), str) or row["visual_state_ref"] not in visual_by_id:
            problem(f"Orphan OCR row {position + 1}")
    actual = {"transcript_segments": len(speech), "canonical_visual_states": len(timeline),
              "ocr_records": len(ocr), "preview_visual_states": previews}
    counts = manifest.get("counts", {})
    if not isinstance(counts, dict):
        problem("Invalid manifest counts")
    else:
        for name, value in actual.items():
            if name in counts and counts[name] != value:
                problem(f"Manifest count mismatch: {name}")
    if len(ocr) != len(timeline):
        problem("Visual and OCR record counts differ")
    duplicates = sum(value - 1 for value in digests.values())
    if duplicates:
        warnings.append(f"{duplicates} byte-identical canonical images; repeated image pages may be intentional")
    if any(row.get("error") for row in ocr):
        warnings.append("OCR errors are recorded; image integrity does not imply text accuracy")
    report.update(passed=not errors, counts=actual, exact_duplicate_images=duplicates,
                  checked_images=len(checked_paths))
    return report
