#!/bin/bash
# Package an existing desktop build for direct distribution, then notarize it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/dist/Deban.app"
SIGN_ID="${DEBAN_MAC_SIGN_ID:-}"
if [ -z "$SIGN_ID" ]; then
  IDENTITIES="$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p')"
  if [ "$(printf '%s\n' "$IDENTITIES" | grep -c . || true)" = 1 ]; then SIGN_ID="$IDENTITIES"; fi
fi
PROFILE="${DEBAN_NOTARY_PROFILE:-deban}"
ENTS="$ROOT/native/mac/entitlements.plist"
[ -d "$SOURCE" ] || { echo 'Build dist/Deban.app first with scripts/build-macos.sh' >&2; exit 1; }
[ -n "$SIGN_ID" ] && security find-identity -v -p codesigning | grep -Fq "\"$SIGN_ID\"" || { echo 'Set DEBAN_MAC_SIGN_ID to an available Developer ID signing identity' >&2; exit 1; }
# Fail before copying/signing if Apple cannot access this account.
xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null
VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$SOURCE/Contents/Info.plist")
case "$VERSION" in *[!a-zA-Z0-9._-]*|'') echo 'Invalid application version' >&2; exit 1;; esac
STAGE="$(mktemp -d "$ROOT/dist/.deban-release.XXXXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/Deban.app"
ditto "$SOURCE" "$APP"
BACKEND="$APP/Contents/Resources/backend"
# Match deban-dev's direct-distribution signing: timestamp and enable the
# hardened runtime for all embedded Mach-O code before signing the outer app.
while IFS= read -r -d '' file_path; do
  kind="$(file -b "$file_path")"
  case "$kind" in *Mach-O*) ;; *) continue ;; esac
  case "$kind" in
    *library*|*'shared library'*|*bundle*)
      codesign --force --timestamp --options runtime --sign "$SIGN_ID" "$file_path" ;;
    *) codesign --force --timestamp --options runtime --entitlements "$ENTS" --sign "$SIGN_ID" "$file_path" ;;
  esac
done < <(find "$BACKEND" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0)
codesign --force --timestamp --options runtime --entitlements "$ENTS" --sign "$SIGN_ID" "$APP"
codesign --verify --deep --strict --verbose=1 "$APP"
DMG="$ROOT/dist/Deban-${VERSION}-macos-arm64.dmg"
command -v uv >/dev/null || { echo 'uv is required to create the installer layout' >&2; exit 1; }
uv run --with 'dmgbuild==2.0.0' dmgbuild -s "$ROOT/native/mac/dmg_settings.py" \
  -D "app=$APP" -D "bg=$ROOT/native/mac/dmg-background.png" Deban "$STAGE/Deban.dmg"
codesign --force --timestamp --sign "$SIGN_ID" "$STAGE/Deban.dmg"
xcrun notarytool submit "$STAGE/Deban.dmg" --keychain-profile "$PROFILE" --wait
xcrun stapler staple "$STAGE/Deban.dmg"
xcrun stapler validate "$STAGE/Deban.dmg"
spctl --assess --type open --context context:primary-signature -v "$STAGE/Deban.dmg"
mv -f "$STAGE/Deban.dmg" "$DMG"
(cd "$ROOT/dist" && shasum -a 256 "$(basename "$DMG")") > "$DMG.sha256"
printf 'Release package: %s\n' "$DMG"
