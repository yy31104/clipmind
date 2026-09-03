#!/bin/sh
set -eu

source_path=${1:-.}

if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "FFmpeg is required. Run: brew install ffmpeg" >&2
  else
    echo "FFmpeg is required. Install it with your system package manager." >&2
  fi
  exit 1
fi

uv tool install --force "$source_path"
echo "ClipMind installed. Run: clipmind doctor"
echo "Then launch the app with: clipmind-app"
