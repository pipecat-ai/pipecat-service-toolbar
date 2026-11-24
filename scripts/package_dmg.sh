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
#      A notarized DMG is published all or nothing and never over an existing name: each is
#      hard-linked into place, and cleanup removes any published name that is this run's own
#      scratch file unless all three are in place, so a retry is not blocked. A rehearsal
#      overwrites an earlier rehearsal.
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
# Only one `just app-dmg` per checkout at a time: runs share build/.
# Bash 3.2 compatible.
set -euo pipefail
unset CDPATH  # `cd dir` would print the path and corrupt the command substitution below

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

# Git-ignored files under Sources/ are invisible to `git status` yet SwiftPM compiles them, so they
# could ship in an app named after a commit that lacks them. Build output elsewhere stays ignored.
check_no_ignored_sources() {
    local ignored
    ignored="$(git status --porcelain --ignored=matching -- Sources Package.swift | grep '^!!' || true)"
    [[ -z "$ignored" ]] || die "git-ignored files under Sources/ would be compiled into the app:
$ignored
       Remove them first (every DMG maps to one commit)."
}

step "Checking the working tree and signing setup"
# --untracked-files=all: status.showUntrackedFiles=no must not hide a source file from the check.
TREE_STATUS="$(git status --porcelain --untracked-files=all)" || die "cannot check whether the working tree is clean"
[[ -z "$TREE_STATUS" ]] || die "working tree is not clean; commit or stash first (every DMG maps to one commit)"
check_no_ignored_sources
check_team
HASH="$(resolve_identity)" || exit 1
echo "    identity $HASH"
if [[ -z "$SKIP_NOTARIZE" ]]; then
    check_notary_profile
    echo "    notary profile '$NOTARY_PROFILE' OK"
fi

# The version and build come from the commit the DMG is named after, not the working tree, so a
# checkout finishing between the two reads cannot pair one commit with another's version.
COMMIT="$(git rev-parse HEAD)"
read_json() { git show "$COMMIT:./app-version.json" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
VERSION="$(read_json version)" || die "cannot read app-version.json at $COMMIT"
BUILD="$(read_json build)" || die "cannot read app-version.json at $COMMIT"
# Both become part of file names: refuse anything but a plain version and build number.
VERSION_RE='^[0-9]+(\.[0-9]+)*([-+.][0-9A-Za-z.]+)*$'
[[ "$VERSION" =~ $VERSION_RE ]] || die "app-version.json: version '$VERSION' is not a plain version (like 1.2.3)"
[[ "$BUILD" =~ ^[0-9]+$ ]] || die "app-version.json: build '$BUILD' is not a whole number"
SHORT="$(git rev-parse --short=7 "$COMMIT")"
NAME="Pipecat-Service-Toolbar-$VERSION-b$BUILD"
[[ "$(git log -1 --format=%H "$COMMIT" -- app-version.json)" == "$COMMIT" ]] || NAME="$NAME-$SHORT"
[[ -z "$SKIP_NOTARIZE" ]] || NAME="$NAME-UNNOTARIZED"
# A leftover DMG_APP_BUNDLE must never yield a normally named, shareable DMG.
[[ -z "${DMG_APP_BUNDLE:-}" ]] || NAME="$NAME-TESTBUNDLE"
DMG="$NAME.dmg"
echo "    $DMG"
# A notarized DMG may already have been shared: a rebuild would change its bytes and checksum under
# the same name, so refuse before spending two notarizations. Rehearsals are never shared.
taken() { [[ -e "$OUT/$1" || -L "$OUT/$1" ]]; }  # in any form, a dangling link included
if [[ -z "$SKIP_NOTARIZE" ]] && { taken "$DMG" || taken "$DMG.sha256" || taken "$NAME.manifest.json"; }; then
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
TREE_STATUS="$(git status --porcelain --untracked-files=all)" \
    || die "cannot check whether the working tree is clean"
[[ -z "$TREE_STATUS" ]] \
    || die "the working tree changed during the build; the DMG would not match commit $COMMIT. Nothing was published."
check_no_ignored_sources

# --- scratch, cleanup ---------------------------------------------------------------------

SCRATCH=""
MOUNT=""
PUBLISH=("$DMG" "$DMG.sha256" "$NAME.manifest.json")  # manifest last

# ours <name>: dist/dmg/<name> is this run's scratch file (same inode), whoever's link put it there.
ours() { [[ -n "$SCRATCH" && -e "$OUT/$1" && "$OUT/$1" -ef "$SCRATCH/$1" ]]; }

# Removes this run's files from dist/dmg unless all of them are published. Identity, not a record
# of what was linked: a signal between `ln` and any bookkeeping cannot leave an unknown file, and a
# file another run published is never this run's inode, so it is never removed.
unpublish_partial() {
    local f complete=1
    for f in "${PUBLISH[@]}"; do ours "$f" || complete=""; done
    [[ -z "$complete" ]] || return 0
    for f in "${PUBLISH[@]}"; do
        if ours "$f"; then rm -f "$OUT/$f"; fi
    done
}

cleanup() {
    if [[ -n "$MOUNT" ]]; then
        hdiutil detach -quiet "$MOUNT" 2>/dev/null || hdiutil detach -force -quiet "$MOUNT" 2>/dev/null || true
    fi
    unpublish_partial
    [[ -z "$SCRATCH" ]] || rm -rf "$SCRATCH"
}
trap cleanup EXIT
mkdir -p "$OUT"
SCRATCH="$(mktemp -d "$OUT/.dmg-package.XXXXXX")"
STAGE="$SCRATCH/stage"
mkdir "$STAGE"
ditto "$BUNDLE" "$STAGE/$APP_NAME"

# build/ is shared: another build may have replaced it after the Git check above. The staged copy
# is what gets notarized, so it must carry this commit's version and build.
plist_value() {
    python3 -c 'import plistlib,sys
try:
    with open(sys.argv[1], "rb") as f: print(plistlib.load(f).get(sys.argv[2], ""))
except Exception: print("")' "$STAGE/$APP_NAME/Contents/Info.plist" "$1"
}
STAGED_VERSION="$(plist_value CFBundleShortVersionString)"
STAGED_BUILD="$(plist_value CFBundleVersion)"
[[ "$STAGED_VERSION" == "$VERSION" && "$STAGED_BUILD" == "$BUILD" ]] \
    || die "the app to package is version '${STAGED_VERSION:-?}' build '${STAGED_BUILD:-?}', not $VERSION build $BUILD of commit $COMMIT (did another build replace it?). Nothing was published."

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
# Unlike package_tester.sh, this needs no lock and no signal masking: a hard link never replaces a
# name, so two runs cannot publish over each other, and cleanup decides by inode what is this run's,
# so an interrupt at any point leaves either the full set or none of this run's files.
if [[ -n "$SKIP_NOTARIZE" ]]; then
    # A rehearsal is never shared, so it may replace an earlier rehearsal of the same name.
    rm -f "$OUT/$NAME.manifest.json"
    mv -f "$SCRATCH/$DMG" "$OUT/$DMG"
    mv -f "$SCRATCH/$DMG.sha256" "$OUT/$DMG.sha256"
    mv -f "$SCRATCH/$NAME.manifest.json" "$OUT/$NAME.manifest.json"
else
    # All or nothing, and never over a name another run published since the preflight check. ln
    # refuses an existing file, but links INTO a directory (or a link to one), so any existing name
    # is refused first and the link is checked afterwards. Scratch is in dist/dmg: same file system.
    for f in "${PUBLISH[@]}"; do
        if taken "$f"; then
            die "cannot publish dist/dmg/$f: it appeared during this run (another run of this commit?). Nothing of this run was published."
        fi
        if ! LN_ERR="$(ln "$SCRATCH/$f" "$OUT/$f" 2>&1)"; then
            if taken "$f"; then
                die "cannot publish dist/dmg/$f: $LN_ERR
       It exists now (another run of this commit?). Nothing of this run was published."
            fi
            # The file system cannot hard-link: keep the twice-notarized DMG under a name that is
            # plainly unpublished rather than losing it with scratch.
            unpublish_partial
            mv -f "$SCRATCH/$DMG" "$OUT/$NAME.unpublished.dmg"
            die "cannot publish dist/dmg/$f: $LN_ERR
       The file system holding dist/dmg does not support hard links, which publishing needs. The
       notarized DMG was kept as dist/dmg/$NAME.unpublished.dmg (sha256 $SHA256);
       move the checkout to an APFS or HFS+ volume and run again. Nothing was published."
        fi
        if ! ours "$f"; then
            # A directory appeared between the check and ln: ln linked into it. Take that back.
            if [[ -d "$OUT/$f" && "$OUT/$f/$f" -ef "$SCRATCH/$f" ]]; then rm -f "$OUT/$f/$f"; fi
            die "cannot publish dist/dmg/$f: something else appeared there during this run. Nothing of this run was published."
        fi
    done
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
