#!/bin/bash
# Pipecat Service Toolbar tester installer (interim, self-signed channel).
#
#   install_tester.sh --verify-only <app-bundle>   signer check only: no network, no install
#   install_tester.sh <tag>                        install a tester drop (added in a later phase)
#
# Plan: docs/plans/20261121-feature-selfsigned-tester-channel.md
#
# This file is fetched by `curl` at a full commit SHA on main, so it is a single file and its
# certificate pin lives here and nowhere else. The packager and the tests call --verify-only.
# Runs under stock macOS Bash 3.2.
set -euo pipefail

# SHA-1 of the owner's self-signed "Code Signing" certificate (login keychain). Every tester
# build must be signed by it; a bundle signed by anything else is refused.
TESTER_CERT_SHA1="AAC7E2B994E42BED55FAD8E292CF3BA1A4629CEE"
BUNDLE_ID="ai.pipecat.ServiceToolbar"

usage() {
    echo "usage: install_tester.sh --verify-only <app-bundle> | <tag>" >&2
}

# Return 0 only when the bundle is intact and signed by the pinned certificate.
# The signer check is a codesign requirement *verification*. The designated requirement text
# is never parsed: an ad-hoc bundle can be signed with a custom requirement that prints the
# pinned hash, and `codesign -R` is what rejects it.
verify_signer() {
    local app="$1"
    if [ ! -d "$app" ]; then
        echo "error: not an app bundle: $app" >&2
        return 2
    fi
    if ! codesign --verify --deep --strict "$app" >/dev/null 2>&1; then
        echo "error: signature is missing or broken: $app" >&2
        return 1
    fi
    if ! codesign --verify --strict \
        -R "=identifier \"$BUNDLE_ID\" and certificate leaf = H\"$TESTER_CERT_SHA1\"" \
        "$app" >/dev/null 2>&1; then
        echo "error: not signed by the pinned tester certificate: $app" >&2
        return 1
    fi
    echo "ok: signed by the pinned tester certificate"
}

main() {
    case "${1:-}" in
        --verify-only)
            if [ "$#" -ne 2 ]; then
                usage
                return 2
            fi
            verify_signer "$2"
            ;;
        *)
            usage
            return 2
            ;;
    esac
}

main "$@"
