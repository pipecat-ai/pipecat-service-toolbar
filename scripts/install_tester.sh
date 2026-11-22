#!/bin/bash
# Pipecat Service Toolbar tester installer (interim, self-signed channel).
#
#   install_tester.sh --verify-only <app-bundle>   signer check only: no network, no install
#   install_tester.sh <tag>                        install a tester drop (APP_INSTALL_DIR overrides ~/Applications)
#
# Plan: docs/plans/20261121-feature-selfsigned-tester-channel.md
#
# This file is fetched by `curl` at a full commit SHA on main, so it is a single file and its
# certificate pin lives here and nowhere else. The packager and the tests call --verify-only.
# Runs under stock macOS Bash 3.2.
#
# What it touches: the app bundle in the install directory, a `.tester-previous` sibling while it
# swaps, and its own `.tester-install.*` staging directory. Nothing else: no state, caches, models
# or preferences, and never sudo. It deletes only its staging directory; a superseded app copy is
# moved into staging first.
set -euo pipefail

# SHA-1 of the owner's self-signed "Code Signing" certificate (login keychain). Every tester
# build must be signed by it; a bundle signed by anything else is refused.
TESTER_CERT_SHA1="AAC7E2B994E42BED55FAD8E292CF3BA1A4629CEE"
BUNDLE_ID="ai.pipecat.ServiceToolbar"

BUNDLE_NAME="Pipecat Service Toolbar.app"
REPO_URL="https://github.com/pipecat-ai/pipecat-service-toolbar"
TAG_PATTERN='^tester-([0-9]+\.[0-9]+\.[0-9]+)-b([0-9]+)-[0-9a-f]{7,12}$'
MIN_MACOS_MAJOR=15
MAX_ZIP_BYTES=262144000 # the app is ~45 MB; refuse anything wildly larger
MAX_SUM_BYTES=4096
STAGE=""

usage() {
    echo "usage: install_tester.sh --verify-only <app-bundle> | <tag>" >&2
}

die() {
    echo "error: $*" >&2
    exit 1
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

sha256_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        sha256sum "$1" | cut -d' ' -f1
    fi
}

# `defaults read <bundle>/Contents/Info <key>`: the path is given without the .plist suffix.
plist_value() {
    defaults read "$1/Contents/Info" "$2" 2>/dev/null
}

check_platform() {
    local arm major
    # Not `uname -m`: it reports x86_64 for a shell running under Rosetta on Apple silicon.
    arm="$(sysctl -n hw.optional.arm64 2>/dev/null || true)"
    [ "$arm" = "1" ] || die "this build needs a Mac with Apple silicon"
    major="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
    case "$major" in
        '' | *[!0-9]*) die "could not read the macOS version" ;;
    esac
    [ "$major" -ge "$MIN_MACOS_MAJOR" ] || die "this build needs macOS $MIN_MACOS_MAJOR or newer (found $major)"
}

# Refuse while the app runs: replacing a live bundle leaves the old build running. The same
# probe scripts/quit_app.sh uses. A Mac that has never seen the app (a first install) makes
# osascript fail with -1728 "Can't get application id": LaunchServices knows no app with that
# id, and a running app is always known to it, so that answer means "not running". stderr is
# read for it; any other failure or answer is "cannot tell" and refuses.
check_not_running() {
    local answer
    answer="$(osascript -e "application id \"$BUNDLE_ID\" is running" 2>&1 || true)"
    case "$answer" in
        false) ;;
        true) die "the app is running; quit Pipecat Service Toolbar, then run this again" ;;
        *"(-1728)"*) ;;
        *) die "could not tell whether the app is running; quit it and run this again" ;;
    esac
}

# -q first: ignore ~/.curlrc. HTTPS only, including redirects to GitHub's release CDN; trust
# comes from the certificate pin, not the host.
fetch() {
    curl -q --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' --max-redirs 5 \
        --max-filesize "$3" --max-time "$4" \
        -o "$2" "$1"
}

check_archive_shape() {
    local zip="$1" names entry listing
    names="$(unzip -Z1 "$zip")" || die "cannot read the archive"
    [ -n "$names" ] || die "the archive is empty"
    while IFS= read -r entry; do
        case "$entry" in
            "$BUNDLE_NAME" | "$BUNDLE_NAME"/*) ;;
            *) die "the archive holds an entry outside $BUNDLE_NAME: $entry" ;;
        esac
        case "/$entry/" in
            */../* | */./*) die "the archive holds an unsafe path: $entry" ;;
        esac
    done <<EOF
$names
EOF
    # A symlink entry could redirect a later entry out of the staging directory. The listing is
    # read in full and searched through a here-string: `unzip -Z | grep -q` under pipefail
    # reports the pipeline as failed (unzip dies of SIGPIPE once grep exits on the first match)
    # whenever the listing outgrows the pipe buffer, and the symlink would go unseen.
    listing="$(unzip -Z "$zip")" || die "cannot read the archive"
    if grep -q '^l' <<<"$listing"; then
        die "the archive holds a symbolic link"
    fi
}

cleanup() {
    if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
        rm -rf "$STAGE"
    fi
}

# Put the previous copy back at $1 from $2 after a failed swap. Prints the sentence that ends
# the caller's error message, so it never claims a restore that did not happen. It refuses to
# `mv` onto an existing $1: that would nest the previous copy inside it.
restore_previous() {
    local target="$1" prev="$2"
    if [ ! -e "$prev" ]; then
        echo "; there was no previous copy to restore"
    elif [ -e "$target" ] || ! mv "$prev" "$target"; then
        echo "; could not restore the previous copy, it is at $prev"
    else
        echo "; the previous copy was restored"
    fi
}

install_drop() {
    local tag="$1" version build dest target prev zip sum expected actual extract
    local new_version new_build old_version="" old_build=""

    if ! [[ "$tag" =~ $TAG_PATTERN ]]; then
        die "not a tester tag: $tag"
    fi
    version="${BASH_REMATCH[1]}"
    build="${BASH_REMATCH[2]}"

    check_platform
    check_not_running

    dest="${APP_INSTALL_DIR:-$HOME/Applications}"
    target="$dest/$BUNDLE_NAME"
    prev="$dest/.tester-previous"
    mkdir -p "$dest"
    STAGE="$(mktemp -d "$dest/.tester-install.XXXXXX")"
    trap cleanup EXIT

    # An earlier run killed mid-swap leaves the old copy at $prev.
    if [ -e "$prev" ]; then
        if [ -e "$target" ]; then
            mv "$prev" "$STAGE/stale-previous" || die "could not clear $prev"
        else
            mv "$prev" "$target" || die "could not restore the previous copy from $prev"
            echo "restored the previous copy left by an interrupted install"
        fi
    fi

    zip="$STAGE/$tag.zip"
    sum="$STAGE/$tag.zip.sha256"
    echo "downloading $tag"
    fetch "$REPO_URL/releases/download/$tag/$tag.zip" "$zip" "$MAX_ZIP_BYTES" 600 ||
        die "could not download the ZIP for $tag"
    fetch "$REPO_URL/releases/download/$tag/$tag.zip.sha256" "$sum" "$MAX_SUM_BYTES" 60 ||
        die "could not download the checksum for $tag"

    # The checksum only catches corruption; the certificate pin below is the authenticity check.
    expected="$(cut -d' ' -f1 <"$sum")"
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "the checksum file is malformed"
    actual="$(sha256_of "$zip")"
    [ "$expected" = "$actual" ] || die "the download is corrupt (SHA-256 mismatch)"

    check_archive_shape "$zip"
    extract="$STAGE/extract"
    mkdir "$extract"
    ditto -x -k "$zip" "$extract" || die "could not unpack the archive"
    [ -d "$extract/$BUNDLE_NAME" ] || die "the archive does not unpack to $BUNDLE_NAME"
    [ "$(ls -A "$extract" | wc -l | tr -d ' ')" = "1" ] || die "the archive holds more than the app"

    verify_signer "$extract/$BUNDLE_NAME" >/dev/null ||
        die "refusing a build that is not signed by the pinned tester certificate"
    new_version="$(plist_value "$extract/$BUNDLE_NAME" CFBundleShortVersionString)" || new_version=""
    new_build="$(plist_value "$extract/$BUNDLE_NAME" CFBundleVersion)" || new_build=""
    if [ "$new_version" != "$version" ] || [ "$new_build" != "$build" ]; then
        die "the app inside is $new_version ($new_build), not the $version ($build) the tag names"
    fi

    if [ -d "$target" ]; then
        old_version="$(plist_value "$target" CFBundleShortVersionString)" || old_version="?"
        old_build="$(plist_value "$target" CFBundleVersion)" || old_build="?"
    fi

    check_not_running # it may have been started during the download

    if [ -e "$target" ]; then
        mv "$target" "$prev" ||
            die "could not move the installed app aside; if macOS asked about App Management, allow Terminal in System Settings > Privacy & Security > App Management, or remove the app by hand, then run this again"
    fi
    if ! mv "$extract/$BUNDLE_NAME" "$target"; then
        die "could not install$(restore_previous "$target" "$prev")"
    fi
    if ! verify_signer "$target" >/dev/null; then
        mv "$target" "$STAGE/rejected" || true
        die "the installed copy failed verification$(restore_previous "$target" "$prev")"
    fi
    if [ -e "$prev" ]; then
        mv "$prev" "$STAGE/replaced" || echo "note: the previous copy is left at $prev" >&2
    fi

    echo "installed $tag: version $new_version, build $new_build"
    [ -z "$old_version" ] || echo "replaced version $old_version, build $old_build"
    echo "open it from $dest"
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
        tester-*)
            [ "$#" -eq 1 ] || {
                usage
                return 2
            }
            install_drop "$1"
            ;;
        *)
            usage
            return 2
            ;;
    esac
}

# No stdin for children: run as `curl ... | bash -s -- <tag>`, stdin is this script.
main "$@" </dev/null
