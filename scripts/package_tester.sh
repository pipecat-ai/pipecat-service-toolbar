#!/bin/bash
# Package a self-signed tester build: dist/tester/<tag>.zip, .zip.sha256 and .manifest.json.
#
#   just app-tester-package            (owner command; builds with the self-signed identity)
#   TESTER_APP_BUNDLE=<app> scripts/package_tester.sh   (TEST-ONLY: package a prebuilt bundle)
#
# Plan: docs/plans/20261121-feature-selfsigned-tester-channel.md
#
# Refuses, before building, a missing self-signed identity, a dirty tree, a HEAD that is not
# pushed, and a tag that already exists. After building it runs install_tester.sh --verify-only
# on the bundle and again on the bundle unpacked from the ZIP it just wrote, so what ships is
# what was verified. It never touches app-version.json, rulesets or release evidence.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPTS")"
OUT="$ROOT/dist/tester"
NAME="Pipecat Service Toolbar.app"
INSTALLER="$SCRIPTS/install_tester.sh"

step() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }

cd "$ROOT"

step "Checking the working tree"
[ -z "$(git status --porcelain)" ] || die "working tree is not clean; commit or stash first"
git fetch --quiet origin || die "cannot fetch origin; the pushed and on-main checks need current refs"
COMMIT="$(git rev-parse HEAD)"
if [ -z "$(git for-each-ref --contains "$COMMIT" refs/remotes/origin)" ]; then
    die "HEAD $COMMIT is not pushed to origin; push it first so the installer source and the drop name a fetchable commit"
fi
if git merge-base --is-ancestor "$COMMIT" refs/remotes/origin/main 2>/dev/null; then
    ON_MAIN=true
else
    ON_MAIN=false
fi

# app-version.json is the only version source (AGENTS.md); read it, never write it.
read_json() { python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$ROOT/app-version.json" "$1"; }
VERSION="$(read_json version)"
BUILD="$(read_json build)"
SHORT="$(git rev-parse --short=7 HEAD)"
TAG="tester-$VERSION-b$BUILD-$SHORT"

if git rev-parse --quiet --verify "refs/tags/$TAG" >/dev/null \
    || git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
    die "tag $TAG already exists; a second drop needs a new commit"
fi

if [ -n "${TESTER_APP_BUNDLE:-}" ]; then
    BUNDLE="$TESTER_APP_BUNDLE"
    echo "TEST-ONLY: packaging prebuilt bundle $BUNDLE"
else
    step "Resolving the self-signed identity"
    # Two steps: `export X="$(…)"` would hide the helper's failure from set -e.
    SIGN_IDENTITY="$("$SCRIPTS/resolve_sign_identity.sh" --require)"
    export SIGN_IDENTITY
    [ "$SIGN_IDENTITY" != "-" ] || die "ad-hoc signing is not a tester build; unset SIGN_IDENTITY"
    "$SCRIPTS/build_app.sh"
    BUNDLE="$ROOT/build/$NAME"
fi
[ "$(basename "$BUNDLE")" = "$NAME" ] || die "bundle must be named '$NAME'"

step "Verifying the built bundle against the pin"
"$INSTALLER" --verify-only "$BUNDLE"

step "Writing the ZIP"
ZIP="$TAG.zip"
mkdir -p "$OUT"
rm -f "$OUT/$ZIP" "$OUT/$ZIP.sha256" "$OUT/$TAG.manifest.json"
ditto -c -k --keepParent "$BUNDLE" "$OUT/$ZIP"

step "Verifying the bundle unpacked from the ZIP"
CHECK="$(mktemp -d "${TMPDIR:-/tmp}/tester-package.XXXXXX")"
trap 'rm -rf "$CHECK"' EXIT
ditto -x -k "$OUT/$ZIP" "$CHECK"
[ -d "$CHECK/$NAME" ] || die "the ZIP does not unpack to '$NAME'"
"$INSTALLER" --verify-only "$CHECK/$NAME"

# The bundle inside the ZIP must carry the version and build the tag names.
plist_value() { python3 -c \
    'import plistlib,sys; print(plistlib.load(open(sys.argv[1],"rb"))[sys.argv[2]])' \
    "$CHECK/$NAME/Contents/Info.plist" "$1"; }
[ "$(plist_value CFBundleShortVersionString)" = "$VERSION" ] \
    || die "bundle version does not match app-version.json ($VERSION)"
[ "$(plist_value CFBundleVersion)" = "$BUILD" ] \
    || die "bundle build does not match app-version.json ($BUILD)"

step "Writing the checksum and manifest"
if command -v shasum >/dev/null 2>&1; then
    ZIP_SHA256="$(shasum -a 256 "$OUT/$ZIP" | cut -d' ' -f1)"
else
    ZIP_SHA256="$(sha256sum "$OUT/$ZIP" | cut -d' ' -f1)"
fi
printf '%s  %s\n' "$ZIP_SHA256" "$ZIP" > "$OUT/$ZIP.sha256"
# The pin is read from the one committed copy, never duplicated here.
CERT_SHA1="$(sed -n 's/^TESTER_CERT_SHA1="\([0-9A-F]*\)"$/\1/p' "$INSTALLER")"
printf '{\n  "tag": "%s",\n  "version": "%s",\n  "build": %s,\n  "commit": "%s",\n  "on_main": %s,\n  "cert_sha1": "%s",\n  "zip": "%s",\n  "zip_sha256": "%s",\n  "created_utc": "%s"\n}\n' \
    "$TAG" "$VERSION" "$BUILD" "$COMMIT" "$ON_MAIN" "$CERT_SHA1" "$ZIP" "$ZIP_SHA256" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT/$TAG.manifest.json"

echo
echo "tag:     $TAG"
echo "on_main: $ON_MAIN"
echo "files:   $OUT/$ZIP"
echo "         $OUT/$ZIP.sha256"
echo "         $OUT/$TAG.manifest.json"
[ "$ON_MAIN" = true ] || echo "note: HEAD is not on origin/main; this is a rehearsal drop, not for testers"
