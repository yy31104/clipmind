"""Small desktop launcher for packaged builds and local installations."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def _prepare_dependency_path() -> None:
    """Make Homebrew tools visible when Finder supplies its minimal PATH."""
    if sys.platform != "darwin":
        return
    current = os.environ.get("PATH", "")
    entries = ["/opt/homebrew/bin", "/usr/local/bin"]
    entries.extend(item for item in current.split(os.pathsep) if item)
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(entries))


def entrypoint(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="clipmind-app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    _prepare_dependency_path()

    if not args.no_browser:
        url = f"http://{args.host}:{args.port}"
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    import uvicorn
    from .server import app

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    entrypoint()
