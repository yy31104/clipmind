"""Backwards-compatible source checkout entry point."""
from clipmind.cli import entrypoint, main

__all__ = ["entrypoint", "main"]


if __name__ == "__main__":
    raise SystemExit(entrypoint())
