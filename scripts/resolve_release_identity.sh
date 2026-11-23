#!/usr/bin/env bash
# Print the SHA-1 hash of the Developer ID Application identity a release build signs with.
#
#   scripts/resolve_release_identity.sh <team-id>
#
# Reads the keychain's valid code-signing identities (`security find-identity -v -p codesigning`)
# and keeps those named "Developer ID Application: <name> (<team-id>)". Expired, revoked or
# untrusted identities are not listed by -v, so they never qualify. SIGN_IDENTITY, when set,
# narrows the choice to one of them: its full SHA-1 hash (any case) or its exact name. A value
# matching no qualifying identity (a self-signed "Code Signing", ad hoc "-", another team's
# Developer ID) is refused, never passed through. Printing the hash, not the name, keeps
# codesign from matching a second identity that shares the name.
#
# Exit 0: hash printed. Exit 1: no qualifying identity, or several and SIGN_IDENTITY unset.
# Exit 2: usage, or the keychain query failed. Bash 3.2 compatible.
set -euo pipefail

usage() { echo "usage: resolve_release_identity.sh <team-id>" >&2; exit 2; }
(($# == 1)) || usage
team="$1"
[[ "$team" =~ ^[A-Z0-9]{10}$ ]] || { echo "error: '$team' is not a 10-character Apple Team ID" >&2; exit 2; }

listing="$(security find-identity -v -p codesigning)" || {
    echo "error: the keychain query failed (security find-identity)" >&2
    exit 2
}

want="${SIGN_IDENTITY:-}"
want_hash=""
if [[ "$want" =~ ^[0-9A-Fa-f]{40}$ ]]; then
    want_hash="$(printf '%s' "$want" | tr 'a-f' 'A-F')"
fi

re='^[[:space:]]*[0-9]+\)[[:space:]]+([0-9A-Fa-f]{40})[[:space:]]+"(Developer ID Application: .* \(([A-Z0-9]{10})\))"[[:space:]]*$'
found=""
names=""
count=0
while IFS= read -r line; do
    [[ "$line" =~ $re ]] || continue
    hash="$(printf '%s' "${BASH_REMATCH[1]}" | tr 'a-f' 'A-F')"
    name="${BASH_REMATCH[2]}"
    [[ "${BASH_REMATCH[3]}" == "$team" ]] || continue
    if [[ -n "$want" ]]; then
        if [[ -n "$want_hash" ]]; then
            [[ "$hash" == "$want_hash" ]] || continue
        else
            [[ "$name" == "$want" ]] || continue
        fi
    fi
    case " $found " in *" $hash "*) continue ;; esac
    found="${found:+$found }$hash"
    names="$names    $hash \"$name\""$'\n'
    count=$((count + 1))
done <<< "$listing"

if ((count == 1)); then
    printf '%s\n' "$found"
    exit 0
fi
if ((count == 0)); then
    if [[ -n "$want" ]]; then
        echo "error: SIGN_IDENTITY matches no valid Developer ID Application identity of team $team" >&2
    else
        echo "error: no valid Developer ID Application identity of team $team in the keychain" >&2
        echo "       import the Developer ID .p12 (docs/development.md, \"Notarized DMG\")" >&2
    fi
    exit 1
fi
echo "error: $count Developer ID Application identities of team $team; set SIGN_IDENTITY to one hash:" >&2
printf '%s' "$names" >&2
exit 1
