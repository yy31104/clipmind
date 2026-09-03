"""Small desktop launcher for packaged builds and local installations."""
from __future__ import annotations

import argparse
import threading
import webbrowser


def entrypoint(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="clipmind-app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if not args.no_browser:
        url = f"http://{args.host}:{args.port}"
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run("clipmind.server:app", host=args.host, port=args.port)


if __name__ == "__main__":
    entrypoint()
