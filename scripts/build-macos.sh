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
mkdir -p "$CACHE" "$ROOT/dist"
STAGE="$(mktemp -d "$ROOT/dist/.deban-build.XXXXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/Deban.app"
if [ ! -f "$CACHE/$ARCHIVE" ]; then
  curl -fL --retry 3 "https://github.com/astral-sh/python-build-standalone/releases/download/$TAG/$ARCHIVE" -o "$CACHE/$ARCHIVE.part"
  mv "$CACHE/$ARCHIVE.part" "$CACHE/$ARCHIVE"
fi
printf '%s  %s\n' "$CHECKSUM" "$CACHE/$ARCHIVE" | shasum -a 256 -c -
npm --prefix ui ci --no-audit --no-fund
npm --prefix ui run build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/backend/approot/ui"
tar -xzf "$CACHE/$ARCHIVE" -C "$APP/Contents/Resources/backend"
PY="$APP/Contents/Resources/backend/python/bin/python3"
uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt --quiet -o "$CACHE/requirements.txt"
uv pip install --python "$PY" -r "$CACHE/requirements.txt"
cp -R app "$APP/Contents/Resources/backend/approot/"
find "$APP/Contents/Resources/backend/approot/app" -type d -name __pycache__ -prune -exec rm -rf {} +
cp main.py "$APP/Contents/Resources/backend/approot/"
cp -R ui/dist "$APP/Contents/Resources/backend/approot/ui/"
# Match the production packaging: ship Deban's bytecode without its Python
# sources. Third-party sources stay intact because some libraries inspect them.
APPROOT="$APP/Contents/Resources/backend/approot"
"$PY" -m compileall -q -b -s "$APPROOT" -p . "$APPROOT/app" "$APPROOT/main.py"
find "$APPROOT/app" -type f -name '*.py' -delete
rm "$APPROOT/main.py"
find "$APPROOT/app" -type d -name __pycache__ -prune -exec rm -rf {} +
[ -f "$APPROOT/main.pyc" ] && ! find "$APPROOT/app" -name '*.py' -print -quit | grep -q .
cp src-tauri/icons/icon.icns "$APP/Contents/Resources/AppIcon.icns"
cp native/mac/Info.plist "$APP/Contents/Info.plist"
swiftc -O -target arm64-apple-macos15.0 native/mac/main.swift native/mac/NativeAudio.swift -o "$APP/Contents/MacOS/Deban"
# TCC identifies ad-hoc builds by their code hash, which changes on each build.
# Prefer the same Developer ID identity as the validated desktop build so
# permissions continue to match across rebuilds. CI can still build ad-hoc.
SIGN_ID="${DEBAN_MAC_SIGN_ID:-}"
if [ -z "$SIGN_ID" ]; then
  IDENTITIES="$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p')"
  if [ "$(printf '%s\n' "$IDENTITIES" | grep -c . || true)" = 1 ]; then SIGN_ID="$IDENTITIES"; fi
fi
if [ -n "$SIGN_ID" ] && security find-identity -v -p codesigning | grep -Fq "\"$SIGN_ID\""; then
  codesign --force --deep --sign "$SIGN_ID" "$APP"
else
  printf '%s\n' 'No configured Developer ID found; using ad-hoc signing. macOS permissions may need to be granted again after a rebuild.' >&2
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"
rm -rf "$ROOT/dist/Deban.app"
mv "$APP" "$ROOT/dist/Deban.app"
APP="$ROOT/dist/Deban.app"
# Register only the complete bundle so Finder does not cache its placeholder.
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
if [ -x "$LSREGISTER" ]; then "$LSREGISTER" -f "$APP" || true; fi
touch "$APP"
printf 'Built: %s\n' "$APP"
