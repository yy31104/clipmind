#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
  echo "The macOS app bundle must be built on macOS." >&2
  exit 1
fi

uv run --extra build pyinstaller --clean --noconfirm packaging/clipmind.spec

if [ -n "${CODE_SIGN_IDENTITY:-}" ]; then
  codesign --deep --force --options runtime --sign "$CODE_SIGN_IDENTITY" dist/ClipMind.app
fi

hdiutil create -volname ClipMind -srcfolder dist/ClipMind.app \
  -ov -format UDZO dist/ClipMind.dmg

echo "Built dist/ClipMind.dmg"
if [ -z "${CODE_SIGN_IDENTITY:-}" ]; then
  echo "The app is unsigned. Public releases must be signed and notarized." >&2
fi
