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
#   2. SIGN_MODE=release build_app.sh (signs and verifies the app), then HEAD and the tree must
#      be unchanged, so the DMG matches the commit it is named after;
#   3. notarize the app (ditto ZIP), staple it, Gatekeeper-assess it;
#   4. DMG (app + Applications link) with hdiutil, sign it, notarize it, staple it, assess it;
#   5. mount the DMG read-only and re-verify the app inside;
#   6. publish .dmg, .dmg.sha256 and .manifest.json (last) from scratch into dist/dmg/.
#      A notarized DMG is published all or nothing and never over an existing file: each is
#      hard-linked into place (fails if the name exists), and a failure removes what this run
#      linked, so a retry is not blocked. A rehearsal overwrites an earlier rehearsal.
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
        || die "notary profile '$NOTARY_PROFILE' is missing, rejected by Apple, or Apple is unreachable (check the network); to create it, run
       xcrun notarytool store-credentials $NOTARY_PROFILE --key <AuthKey.p8> --key-id <KEYID> --issuer <ISSUER_ID>
       (docs/development.md, \"Notarized DMG\")"
}

# Days until the identity's certificate expires, or empty when it cannot be read.
days_to_expiry() {
    local hash="$1" pem end end_epoch now
    # awk reads to the end (no early exit): under pipefail an early exit would SIGPIPE `security`
    # on a keychain larger than the pipe buffer and silently skip this check. openssl reads the
    # first certificate when the same one is in two keychains.
    pem="$(security find-certificate -a -Z -p 2>/dev/null | awk -v h="$hash" '
        /^SHA-1 hash:/ { take = (toupper($3) == h) ; next }
        take { print }
        /-----END CERTIFICATE-----/ { take = 0 }')" || return 0
    [[ -n "$pem" ]] || return 0
    end="$(printf '%s\n' "$pem" | openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//')" || return 0
    [[ -n "$end" ]] || return 0
    # openssl prints English month names whatever the locale: parse them in the C locale.
    end_epoch="$(LC_ALL=C date -j -f '%b %e %T %Y %Z' "$end" +%s 2>/dev/null || LC_ALL=C date -d "$end" +%s 2>/dev/null)" || return 0
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
# Both become part of file names: refuse anything but a plain version and build number.
VERSION_RE='^[0-9]+(\.[0-9]+)*([-+.][0-9A-Za-z.]+)*$'
[[ "$VERSION" =~ $VERSION_RE ]] || die "app-version.json: version '$VERSION' is not a plain version (like 1.2.3)"
[[ "$BUILD" =~ ^[0-9]+$ ]] || die "app-version.json: build '$BUILD' is not a whole number"
COMMIT="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short=7 HEAD)"
NAME="Pipecat-Service-Toolbar-$VERSION-b$BUILD"
[[ "$(git log -1 --format=%H -- app-version.json)" == "$COMMIT" ]] || NAME="$NAME-$SHORT"
[[ -z "$SKIP_NOTARIZE" ]] || NAME="$NAME-UNNOTARIZED"
# A leftover DMG_APP_BUNDLE must never yield a normally named, shareable DMG.
[[ -z "${DMG_APP_BUNDLE:-}" ]] || NAME="$NAME-TESTBUNDLE"
DMG="$NAME.dmg"
echo "    $DMG"
# A notarized DMG may already have been shared: a rebuild would change its bytes and checksum under
# the same name, so refuse before spending two notarizations. Rehearsals are never shared.
if [[ -z "$SKIP_NOTARIZE" && ( -e "$OUT/$DMG" || -e "$OUT/$DMG.sha256" || -e "$OUT/$NAME.manifest.json" ) ]]; then
    die "dist/dmg/$DMG already exists (this commit was built before; it may have been shared). Move it, its .sha256 and its manifest out of dist/dmg to rebuild, or commit a change and bump the build in app-version.json."
fi

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
# The name and manifest record COMMIT, read before the build: the build must have seen that commit.
[[ "$(git rev-parse HEAD)" == "$COMMIT" ]] \
    || die "HEAD moved off $COMMIT during the build; the DMG would not match its name. Nothing was published."
TREE_STATUS="$(git status --porcelain)" || die "cannot check whether the working tree is clean"
[[ -z "$TREE_STATUS" ]] \
    || die "the working tree changed during the build; the DMG would not match commit $COMMIT. Nothing was published."

# --- scratch, cleanup ---------------------------------------------------------------------

SCRATCH=""
MOUNT=""
CREATED=()  # files this run linked into dist/dmg while publishing; emptied once all are in place
cleanup() {
    if [[ -n "$MOUNT" ]]; then
        hdiutil detach -quiet "$MOUNT" 2>/dev/null || hdiutil detach -force -quiet "$MOUNT" 2>/dev/null || true
    fi
    local f
    if ((${#CREATED[@]} > 0)); then
        for f in "${CREATED[@]}"; do rm -f "$OUT/$f"; done
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
            # Apple has no log until the submission finishes (a --wait timeout leaves it In Progress).
            local log="$OUT/$NAME.$label.notary-log.json"
            if xcrun notarytool log "$id" --keychain-profile "$NOTARY_PROFILE" \
                "$SCRATCH/notary-log.json" >/dev/null 2>&1 && [[ -s "$SCRATCH/notary-log.json" ]]; then
                mv -f "$SCRATCH/notary-log.json" "$log"
                die "notarization of the $label ended '${status:-unknown}' (submission $id).
       Apple's log: $log"
            fi
            die "notarization of the $label ended '${status:-unknown}' (submission $id); Apple has no log for it yet.
       Check on it: xcrun notarytool info $id --keychain-profile $NOTARY_PROFILE
       Once it has finished: xcrun notarytool log $id --keychain-profile $NOTARY_PROFILE"
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
# A just-assessed volume is often still busy (Spotlight, Gatekeeper): retry, then force. The image
# is read-only and already checked, so forcing loses nothing, and failing here would throw away
# two finished notarizations.
for _ in 1 2 3; do
    if hdiutil detach -quiet "$MOUNT" 2>/dev/null; then MOUNT=""; break; fi
    sleep 2
done
if [[ -n "$MOUNT" ]]; then
    hdiutil detach -force -quiet "$MOUNT"
    MOUNT=""
fi

# --- 6. checksum, manifest, publish -------------------------------------------------------

step "Writing the checksum and manifest"
SHA256="$(python3 -c 'import hashlib,sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
print(h.hexdigest())' "$SCRATCH/$DMG")"
printf '%s  %s\n' "$SHA256" "$DMG" >"$SCRATCH/$DMG.sha256"
# Every value reaches Python as an argument, never as source text.
python3 - "$SCRATCH/$NAME.manifest.json" "$DMG" "$SHA256" "$VERSION" "$BUILD" "$COMMIT" \
    "$SKIP_NOTARIZE" "$SUBMISSIONS" "${DMG_APP_BUNDLE:-}" <<'PY'
import datetime, json, sys
out, dmg, sha256, version, build, commit, skip, submissions, test_bundle = sys.argv[1:]
json.dump({
    "dmg": dmg,
    "sha256": sha256,
    "version": version,
    "build": int(build),
    "commit": commit,
    "notarized": not skip,
    "notary_submissions": dict(s.split(":", 1) for s in submissions.split()),
    "test_bundle": bool(test_bundle),
    "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(out, "w"), indent=2)
PY

step "Publishing into dist/dmg"
if [[ -n "$SKIP_NOTARIZE" ]]; then
    # A rehearsal is never shared, so it may replace an earlier rehearsal of the same name.
    rm -f "$OUT/$NAME.manifest.json"
    mv -f "$SCRATCH/$DMG" "$OUT/$DMG"
    mv -f "$SCRATCH/$DMG.sha256" "$OUT/$DMG.sha256"
    mv -f "$SCRATCH/$NAME.manifest.json" "$OUT/$NAME.manifest.json"
else
    # All or nothing, and never over a file another run published since the preflight check: ln
    # refuses an existing name (scratch is in dist/dmg, so it is the same file system), and on any
    # failure cleanup removes the files this run linked, so a retry is not blocked.
    for f in "$DMG" "$DMG.sha256" "$NAME.manifest.json"; do
        ln "$SCRATCH/$f" "$OUT/$f" 2>/dev/null \
            || die "cannot publish dist/dmg/$f (did another run of this commit publish it?). Nothing of this run was published."
        CREATED+=("$f")
    done
    CREATED=()
fi

step "Done"
echo "    $OUT/$DMG"
echo "    sha256 $SHA256"
if [[ -n "$SKIP_NOTARIZE" ]]; then
    echo "    REHEARSAL: not notarized. Do not share this DMG."
elif [[ -n "${DMG_APP_BUNDLE:-}" ]]; then
    echo "    TEST BUNDLE: packaged from a prebuilt test bundle, not built from commit $COMMIT. Do not share this DMG."
else
    echo "    Share the .dmg (and .sha256). The manifest is your record."
fi
