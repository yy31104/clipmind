"""Internal Vision interpreter. JSON stdio avoids caller-main bootstrap rules."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import ocr


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = ocr.recognize(Path(request["image"]), languages=tuple(request["languages"]))
            response = {"status": "ok", "blocks": [asdict(block) for block in result.blocks],
                        "ocr_module": str(Path(ocr.__file__).resolve())}
        except Exception as exc:
            response = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
