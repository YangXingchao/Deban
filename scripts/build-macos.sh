#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v uv >/dev/null
command -v npm >/dev/null
command -v swiftc >/dev/null
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { printf '%s\n' 'This build requires Apple Silicon macOS.' >&2; exit 1; }
TAG=20260901
VERSION=3.12.14
ARCHIVE="cpython-${VERSION}+${TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
CHECKSUM=81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b
CACHE="$ROOT/.build-cache"
APP="$ROOT/dist/Deban.app"
mkdir -p "$CACHE" "$ROOT/dist"
if [ ! -f "$CACHE/$ARCHIVE" ]; then
  curl -fL --retry 3 "https://github.com/astral-sh/python-build-standalone/releases/download/$TAG/$ARCHIVE" -o "$CACHE/$ARCHIVE.part"
  mv "$CACHE/$ARCHIVE.part" "$CACHE/$ARCHIVE"
fi
printf '%s  %s\n' "$CHECKSUM" "$CACHE/$ARCHIVE" | shasum -a 256 -c -
npm --prefix ui ci --no-audit --no-fund
npm --prefix ui run build
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/backend/approot/ui"
tar -xzf "$CACHE/$ARCHIVE" -C "$APP/Contents/Resources/backend"
PY="$APP/Contents/Resources/backend/python/bin/python3"
uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt --quiet -o "$CACHE/requirements.txt"
uv pip install --python "$PY" -r "$CACHE/requirements.txt"
cp -R app "$APP/Contents/Resources/backend/approot/"
find "$APP/Contents/Resources/backend/approot/app" -type d -name __pycache__ -prune -exec rm -rf {} +
cp main.py "$APP/Contents/Resources/backend/approot/"
cp -R ui/dist "$APP/Contents/Resources/backend/approot/ui/"
cp src-tauri/icons/icon.icns "$APP/Contents/Resources/AppIcon.icns"
cp native/mac/Info.plist "$APP/Contents/Info.plist"
swiftc -O -target arm64-apple-macos15.0 native/mac/main.swift native/mac/NativeAudio.swift -o "$APP/Contents/MacOS/Deban"
codesign --force --deep --sign - "$APP"
printf 'Built: %s\n' "$APP"
