#!/usr/bin/env bash
# Build "Pipecat Service Toolbar.app": the SwiftUI menu bar app plus everything
# it needs to install speech services on demand: a pinned uv, the pinned TTS
# server wheel, a recorded TTS constraints file and the
# STT environments' hash-pinned constraints (constraints/).
# Python itself is NOT bundled; uv installs a managed CPython on first use.
#
#   scripts/build_app.sh            # release build, ad-hoc signed
#   SIGN_IDENTITY="Developer ID Application: …" scripts/build_app.sh
#
# Output: build/Pipecat Service Toolbar.app
set -euo pipefail

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
MACOS_DIR="$(dirname "$SCRIPTS")"
REPO_ROOT="$MACOS_DIR"
CONFIG="${CONFIG:-release}"
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
APP="$MACOS_DIR/build/Pipecat Service Toolbar.app"
EXE=PipecatServiceToolbar
WORK="$MACOS_DIR/.build/app-staging"

step() { printf '\033[1m==> %s\033[0m\n' "$*"; }

step "Preparing the pinned server wheel"
rm -rf "$WORK" && mkdir -p "$WORK/wheels"
uv run --no-project --python 3.12 "$SCRIPTS/prepare_tools.py" --output "$WORK/wheels"
WHEEL="$(cat "$WORK/wheels/MANIFEST")"
VERSION="$(uv run --no-project --python 3.12 python -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$MACOS_DIR/app-version.json")"
BUILD_NUMBER="$(uv run --no-project --python 3.12 python -c 'import json,sys; print(json.load(open(sys.argv[1]))["build"])' "$MACOS_DIR/app-version.json")"

step "Fetching uv"
UV_BIN="$("$SCRIPTS/fetch_uv.sh")"
echo "    $("$UV_BIN" --version)"

step "Compiling the app ($CONFIG)"
swift build --package-path "$MACOS_DIR" -c "$CONFIG" --product "$EXE"
BIN_DIR="$(swift build --package-path "$MACOS_DIR" -c "$CONFIG" --show-bin-path)"

step "Rendering the icon"
ICONSET="$WORK/AppIcon.iconset"
"$BIN_DIR/$EXE" --render-icon "$ICONSET"
iconutil -c icns "$ICONSET" -o "$WORK/AppIcon.icns"

step "Assembling $(basename "$APP")"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/bin" "$APP/Contents/Resources/wheels"
cp "$BIN_DIR/$EXE" "$APP/Contents/MacOS/$EXE"
cp "$SCRIPTS/Info.plist" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP/Contents/Info.plist"
if [[ -n "${BUNDLE_ID:-}" ]]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" "$APP/Contents/Info.plist"
fi
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp "$WORK/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
install -m 0755 "$UV_BIN" "$APP/Contents/Resources/bin/uv"
cp "$WORK/wheels/$WHEEL" "$WORK/wheels/constraints.txt" "$APP/Contents/Resources/wheels/"
echo "$WHEEL" > "$APP/Contents/Resources/wheels/MANIFEST"
# Hash-pinned STT constraints: checked in (regenerate with
# scripts/compile_stt_constraints.py), so the build itself stays offline-reproducible.
mkdir -p "$APP/Contents/Resources/constraints"
cp "$MACOS_DIR"/constraints/stt-*.txt "$APP/Contents/Resources/constraints/"

step "Signing (identity: $SIGN_IDENTITY)"
# Inside-out: the nested uv binary first, then the bundle seal.
codesign --force --options runtime --timestamp=none --sign "$SIGN_IDENTITY" "$APP/Contents/Resources/bin/uv"
codesign --force --options runtime --timestamp=none --sign "$SIGN_IDENTITY" \
    --entitlements "$SCRIPTS/PipecatServiceToolbar.entitlements" "$APP"
codesign --verify --deep --strict "$APP"

step "Done"
echo "    $APP"
echo "    open \"$APP\""
