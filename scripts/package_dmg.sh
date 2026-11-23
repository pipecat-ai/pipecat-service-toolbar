#!/bin/bash
# Build a notarized, stapled DMG of the app: dist/dmg/<name>.dmg, .dmg.sha256 and .manifest.json.
#
#   just app-dmg          (scripts/package_dmg.sh)           build, sign, notarize, package
#   just app-dmg-doctor   (scripts/package_dmg.sh --doctor)  check the setup only, build nothing
#
# Plan: docs/plans/20261123-feature-notarized-dmg.md. Setup: docs/development.md, "Notarized DMG".
#
# Settings come from the environment or the git-ignored .env (scripts/lib/release_env.sh):
# APPLE_TEAM_ID (required), SIGN_IDENTITY (optional, narrows the Developer ID choice) and
# NOTARY_PROFILE (the `xcrun notarytool store-credentials` keychain profile, default
# pipecat-notary). The scripts never read a .p12, .p8 or password: the signing identity and the
# notary credentials are used only through the login keychain.
#
# Order, each step failing closed:
#   1. preflight (before compiling): clean tree, APPLE_TEAM_ID, one Developer ID identity, the
#      notary profile answers;
#   2. SIGN_MODE=release build_app.sh (signs and verifies the app);
#   3. notarize the app (ditto ZIP), staple it, Gatekeeper-assess it;
#   4. DMG (app + Applications link) with hdiutil, sign it, notarize it, staple it, assess it;
#   5. mount the DMG read-only and re-verify the app inside;
#   6. move .dmg, .dmg.sha256 and .manifest.json (last) from scratch into dist/dmg/.
# A rejected submission saves Apple's log in dist/dmg/ and publishes nothing.
#
# Name: Pipecat-Service-Toolbar-<version>-b<build>.dmg when HEAD is the commit that last changed
# app-version.json, else with -<shortsha> appended. It reads app-version.json and never writes it
# or CHANGELOG.md.
#
# DMG_SKIP_NOTARIZE=1: rehearsal. Signs but skips Apple's notary service and the Gatekeeper
#   assessments; the name ends in -UNNOTARIZED. Never share one.
# DMG_APP_BUNDLE=<app>: TEST ONLY. Package this prebuilt bundle instead of building; the manifest
#   records it. Never share one.
# Bash 3.2 compatible.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPTS")"
OUT="$ROOT/dist/dmg"
APP_NAME="Pipecat Service Toolbar.app"
VOLNAME="Pipecat Service Toolbar"

step() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }

MODE=build
case "${1:-}" in
    "") ;;
    --doctor) MODE=doctor ;;
    *) echo "usage: package_dmg.sh [--doctor]" >&2; exit 2 ;;
esac
(($# <= 1)) || { echo "usage: package_dmg.sh [--doctor]" >&2; exit 2; }

cd "$ROOT"

# shellcheck source=lib/release_env.sh
source "$SCRIPTS/lib/release_env.sh"
load_release_env "$ROOT/.env" || exit 1
NOTARY_PROFILE="${NOTARY_PROFILE:-pipecat-notary}"
SKIP_NOTARIZE=""
[[ "${DMG_SKIP_NOTARIZE:-}" == "1" ]] && SKIP_NOTARIZE=1

# --- setup checks (shared by --doctor and the build preflight) -----------------------------

check_team() {
    [[ -n "${APPLE_TEAM_ID:-}" ]] || die "APPLE_TEAM_ID is not set: copy .env.example to .env and fill it in"
    valid_team_id "$APPLE_TEAM_ID" || die "APPLE_TEAM_ID is not a 10-character Apple Team ID"
}

# Prints the identity's SHA-1; resolve_release_identity.sh explains any refusal.
resolve_identity() {
    "$SCRIPTS/resolve_release_identity.sh" "$APPLE_TEAM_ID"
}

check_notary_profile() {
    xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
        || die "notary profile '$NOTARY_PROFILE' is missing or rejected by Apple: run
       xcrun notarytool store-credentials $NOTARY_PROFILE --key <AuthKey.p8> --key-id <KEYID> --issuer <ISSUER_ID>
       (docs/development.md, \"Notarized DMG\")"
}

# Days until the identity's certificate expires, or empty when it cannot be read.
days_to_expiry() {
    local hash="$1" pem end end_epoch now
    pem="$(security find-certificate -a -Z -p 2>/dev/null | awk -v h="$hash" '
        /^SHA-1 hash:/ { take = (toupper($3) == h) ; next }
        take { print } /-----END CERTIFICATE-----/ { if (take) exit }')" || return 0
    [[ -n "$pem" ]] || return 0
    end="$(printf '%s\n' "$pem" | openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//')" || return 0
    [[ -n "$end" ]] || return 0
    end_epoch="$(date -j -f '%b %e %T %Y %Z' "$end" +%s 2>/dev/null || date -d "$end" +%s 2>/dev/null)" || return 0
    now="$(date +%s)"
    echo $(( (end_epoch - now) / 86400 ))
}

if [[ "$MODE" == "doctor" ]]; then
    step "Checking the notarized DMG setup (nothing is built)"
    check_team
    echo "    ok  APPLE_TEAM_ID set"
    HASH="$(resolve_identity)" || exit 1
    echo "    ok  Developer ID Application identity $HASH"
    DAYS="$(days_to_expiry "$HASH")"
    if [[ -n "$DAYS" ]]; then
        if ((DAYS < 30)); then
            echo "    !!  certificate expires in $DAYS days: ask the Account Holder for a new one"
        else
            echo "    ok  certificate valid for $DAYS more days"
        fi
    fi
    check_notary_profile
    echo "    ok  notary profile '$NOTARY_PROFILE' accepted by Apple"
    echo "Ready: just app-dmg"
    exit 0
fi

# --- 1. preflight -------------------------------------------------------------------------

step "Checking the working tree and signing setup"
TREE_STATUS="$(git status --porcelain)" || die "cannot check whether the working tree is clean"
[[ -z "$TREE_STATUS" ]] || die "working tree is not clean; commit or stash first (every DMG maps to one commit)"
check_team
HASH="$(resolve_identity)" || exit 1
echo "    identity $HASH"
if [[ -z "$SKIP_NOTARIZE" ]]; then
    check_notary_profile
    echo "    notary profile '$NOTARY_PROFILE' OK"
fi

read_json() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
    "$ROOT/app-version.json" "$1"; }
VERSION="$(read_json version)"
BUILD="$(read_json build)"
COMMIT="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short=7 HEAD)"
NAME="Pipecat-Service-Toolbar-$VERSION-b$BUILD"
[[ "$(git log -1 --format=%H -- app-version.json)" == "$COMMIT" ]] || NAME="$NAME-$SHORT"
[[ -z "$SKIP_NOTARIZE" ]] || NAME="$NAME-UNNOTARIZED"
DMG="$NAME.dmg"
echo "    $DMG"

# --- 2. build -----------------------------------------------------------------------------

if [[ -n "${DMG_APP_BUNDLE:-}" ]]; then
    BUNDLE="$DMG_APP_BUNDLE"
    echo "TEST-ONLY: packaging prebuilt bundle $BUNDLE"
    [[ "$(basename "$BUNDLE")" == "$APP_NAME" ]] || die "bundle must be named '$APP_NAME'"
    "$SCRIPTS/verify_release_signature.sh" "$BUNDLE" "$APPLE_TEAM_ID"
else
    # The resolved hash, not a name: build_app.sh must sign with the identity checked above.
    SIGN_IDENTITY="$HASH" SIGN_MODE=release CONFIG=release "$SCRIPTS/build_app.sh"
    BUNDLE="$ROOT/build/$APP_NAME"
fi

# --- scratch, cleanup ---------------------------------------------------------------------

SCRATCH=""
MOUNT=""
cleanup() {
    if [[ -n "$MOUNT" ]]; then
        hdiutil detach -quiet "$MOUNT" 2>/dev/null || hdiutil detach -force -quiet "$MOUNT" 2>/dev/null || true
    fi
    [[ -z "$SCRATCH" ]] || rm -rf "$SCRATCH"
}
trap cleanup EXIT
mkdir -p "$OUT"
SCRATCH="$(mktemp -d "$OUT/.dmg-package.XXXXXX")"
STAGE="$SCRATCH/stage"
mkdir "$STAGE"
ditto "$BUNDLE" "$STAGE/$APP_NAME"

SUBMISSIONS=""

# notarize <file> <label>: submit and wait; on anything but Accepted, save Apple's log and stop.
notarize() {
    local file="$1" label="$2" json rc=0 id status
    step "Notarizing the $label (Apple's notary service; usually a few minutes)"
    json="$(xcrun notarytool submit "$file" --keychain-profile "$NOTARY_PROFILE" \
        --wait --timeout 30m --output-format json 2>"$SCRATCH/notary.err")" || rc=$?
    id="$(printf '%s' "$json" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("id", ""))
except Exception: print("")')"
    status="$(printf '%s' "$json" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("status", ""))
except Exception: print("")')"
    if [[ "$status" != "Accepted" ]]; then
        if [[ -n "$id" ]]; then
            xcrun notarytool log "$id" --keychain-profile "$NOTARY_PROFILE" \
                "$OUT/$NAME.$label.notary-log.json" >/dev/null 2>&1 || true
            die "notarization of the $label ended '${status:-unknown}' (submission $id).
       Apple's log: $OUT/$NAME.$label.notary-log.json (or: xcrun notarytool log $id --keychain-profile $NOTARY_PROFILE)"
        fi
        die "notarization of the $label failed (exit $rc): $(tail -n 3 "$SCRATCH/notary.err" 2>/dev/null)"
    fi
    echo "    accepted (submission $id)"
    SUBMISSIONS="${SUBMISSIONS:+$SUBMISSIONS }$label:$id"
}

# assess <kind> <path>: Gatekeeper must accept it as notarized Developer ID.
assess() {
    local kind="$1" path="$2" out
    if [[ "$kind" == "open" ]]; then
        out="$(spctl -a -vvv -t open --context context:primary-signature "$path" 2>&1)" \
            || die "Gatekeeper rejects $path: $out"
    else
        out="$(spctl -a -vvv -t exec "$path" 2>&1)" || die "Gatekeeper rejects $path: $out"
    fi
    case "$out" in
        *"source=Notarized Developer ID"*) ;;
        *) die "Gatekeeper does not see $path as notarized: $out" ;;
    esac
}

# --- 3. notarize and staple the app -------------------------------------------------------

if [[ -z "$SKIP_NOTARIZE" ]]; then
    ditto -c -k --keepParent "$STAGE/$APP_NAME" "$SCRATCH/app.zip"
    notarize "$SCRATCH/app.zip" app
    rm -f "$SCRATCH/app.zip"
    step "Stapling the app"
    xcrun stapler staple "$STAGE/$APP_NAME"
    xcrun stapler validate "$STAGE/$APP_NAME"
    assess exec "$STAGE/$APP_NAME"
fi

# --- 4. the DMG ---------------------------------------------------------------------------

step "Creating the disk image"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "$VOLNAME" -srcfolder "$STAGE" -fs APFS -format ULFO -ov "$SCRATCH/$DMG"
step "Signing the disk image"
codesign --force --sign "$HASH" --timestamp "$SCRATCH/$DMG"
codesign --verify --strict "$SCRATCH/$DMG"

if [[ -z "$SKIP_NOTARIZE" ]]; then
    notarize "$SCRATCH/$DMG" dmg
    step "Stapling the disk image"
    xcrun stapler staple "$SCRATCH/$DMG"
    xcrun stapler validate "$SCRATCH/$DMG"
    assess open "$SCRATCH/$DMG"
fi

# --- 5. check what a recipient gets -------------------------------------------------------

step "Checking the app inside the mounted disk image"
MOUNT="$SCRATCH/mnt"
mkdir "$MOUNT"
hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "$MOUNT" "$SCRATCH/$DMG"
[[ -d "$MOUNT/$APP_NAME" ]] || die "the disk image does not contain $APP_NAME"
[[ -L "$MOUNT/Applications" ]] || die "the disk image lacks the Applications link"
"$SCRIPTS/verify_release_signature.sh" "$MOUNT/$APP_NAME" "$APPLE_TEAM_ID"
if [[ -z "$SKIP_NOTARIZE" ]]; then
    xcrun stapler validate "$MOUNT/$APP_NAME"
    assess exec "$MOUNT/$APP_NAME"
fi
hdiutil detach -quiet "$MOUNT"
MOUNT=""

# --- 6. checksum, manifest, publish -------------------------------------------------------

step "Writing the checksum and manifest"
SHA256="$(python3 -c 'import hashlib,sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
print(h.hexdigest())' "$SCRATCH/$DMG")"
printf '%s  %s\n' "$SHA256" "$DMG" >"$SCRATCH/$DMG.sha256"
python3 - "$SCRATCH/$NAME.manifest.json" <<PY
import json, sys, datetime
subs = dict(s.split(":", 1) for s in "$SUBMISSIONS".split()) if "$SUBMISSIONS" else {}
json.dump({
    "dmg": "$DMG",
    "sha256": "$SHA256",
    "version": "$VERSION",
    "build": int("$BUILD"),
    "commit": "$COMMIT",
    "notarized": not bool("$SKIP_NOTARIZE"),
    "notary_submissions": subs,
    "test_bundle": bool("${DMG_APP_BUNDLE:-}"),
    "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(sys.argv[1], "w"), indent=2)
PY

step "Publishing into dist/dmg"
rm -f "$OUT/$NAME.manifest.json"
mv -f "$SCRATCH/$DMG" "$OUT/$DMG"
mv -f "$SCRATCH/$DMG.sha256" "$OUT/$DMG.sha256"
mv -f "$SCRATCH/$NAME.manifest.json" "$OUT/$NAME.manifest.json"

step "Done"
echo "    $OUT/$DMG"
echo "    sha256 $SHA256"
if [[ -n "$SKIP_NOTARIZE" ]]; then
    echo "    REHEARSAL: not notarized. Do not share this DMG."
else
    echo "    Share the .dmg (and .sha256). The manifest is your record."
fi
