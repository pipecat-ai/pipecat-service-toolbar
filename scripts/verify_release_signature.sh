#!/usr/bin/env bash
# Check that an app bundle is signed the way notarization requires.
#
#   scripts/verify_release_signature.sh <app> <team-id>
#
# For the bundle and every nested executable in Contents/Resources/bin:
#   - codesign --verify --strict passes (--deep for the bundle);
#   - the leaf authority is "Developer ID Application: …", chained to Apple's
#     Developer ID Certification Authority and Apple Root CA;
#   - TeamIdentifier is <team-id>;
#   - the hardened runtime flag is set;
#   - the signature carries a secure timestamp ("Timestamp=", not "Signed Time=");
#   - no com.apple.security.get-task-allow entitlement (notarization rejects it).
# The bundle's Identifier must be ai.pipecat.ServiceToolbar.
#
# It checks the signature only. Notarization and stapling are separate (scripts/package_dmg.sh).
# Exit 0: all checks pass. Exit 1: a check failed (each failure is named). Exit 2: usage.
# Bash 3.2 compatible.
set -euo pipefail

BUNDLE_ID_EXPECTED="ai.pipecat.ServiceToolbar"

usage() { echo "usage: verify_release_signature.sh <app> <team-id>" >&2; exit 2; }
(($# == 2)) || usage
app="$1"
team="$2"
[[ -d "$app" ]] || { echo "error: no app bundle at $app" >&2; exit 2; }
[[ "$team" =~ ^[A-Z0-9]{10}$ ]] || { echo "error: '$team' is not a 10-character Apple Team ID" >&2; exit 2; }

failures=0
fail() { echo "  FAIL $1: $2" >&2; failures=$((failures + 1)); }

check() {
    local path="$1" label="$2" deep="$3" info entitlements first_authority
    if ! codesign --verify $deep --strict "$path" 2>/dev/null; then
        fail "$label" "codesign --verify --strict failed"
        return
    fi
    info="$(codesign -dvvv "$path" 2>&1)" || { fail "$label" "codesign -dvvv failed"; return; }
    first_authority="$(printf '%s\n' "$info" | grep -m1 '^Authority=' || true)"
    case "$first_authority" in
        "Authority=Developer ID Application: "*) ;;
        *) fail "$label" "not signed with a Developer ID Application certificate (${first_authority:-no authority})" ;;
    esac
    printf '%s\n' "$info" | grep -qx 'Authority=Developer ID Certification Authority' \
        || fail "$label" "certificate chain lacks Developer ID Certification Authority"
    printf '%s\n' "$info" | grep -qx 'Authority=Apple Root CA' \
        || fail "$label" "certificate chain lacks Apple Root CA"
    printf '%s\n' "$info" | grep -qx "TeamIdentifier=$team" \
        || fail "$label" "TeamIdentifier is not $team"
    printf '%s\n' "$info" | grep -q '^CodeDirectory .*flags=.*(runtime)' \
        || fail "$label" "hardened runtime flag missing"
    printf '%s\n' "$info" | grep -q '^Timestamp=' \
        || fail "$label" "no secure timestamp (built with --timestamp=none?)"
    # No entitlements is an empty, successful read; a failed read must not pass as "none".
    if ! entitlements="$(codesign -d --entitlements - --xml "$path" 2>/dev/null)"; then
        fail "$label" "cannot read the entitlements (codesign -d --entitlements failed)"
        entitlements=""
    fi
    case "$entitlements" in
        *com.apple.security.get-task-allow*) fail "$label" "has the get-task-allow entitlement" ;;
    esac
    if [[ "$label" == "app" ]]; then
        printf '%s\n' "$info" | grep -qxF "Identifier=$BUNDLE_ID_EXPECTED" \
            || fail "$label" "Identifier is not $BUNDLE_ID_EXPECTED"
    fi
}

check "$app" "app" "--deep"
if [[ -d "$app/Contents/Resources/bin" ]]; then
    for exe in "$app"/Contents/Resources/bin/*; do
        [[ -f "$exe" && -x "$exe" ]] || continue
        check "$exe" "Contents/Resources/bin/$(basename "$exe")" ""
    done
fi

if ((failures > 0)); then
    echo "error: $failures release signature check(s) failed for $app" >&2
    exit 1
fi
echo "    release signature OK (Developer ID, team $team, hardened runtime, secure timestamp)"
