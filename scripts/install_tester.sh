#!/bin/bash
# Pipecat Service Toolbar tester installer (interim, self-signed channel).
#
#   install_tester.sh --verify-only <app-bundle>   signer check only: no network, no install
#   install_tester.sh --check-archive <zip>        archive shape and size checks only: no network, no install
#   install_tester.sh --check-tag <tag>            exit 0 if the installer accepts the tag name, else 1
#   install_tester.sh --print-pin                  print the certificate pin
#   install_tester.sh <tag>                        install a tester drop (APP_INSTALL_DIR overrides ~/Applications)
#
# Plan: docs/plans/20261121-feature-selfsigned-tester-channel.md
#
# This file is fetched by `curl` at a full commit SHA on main, so it is a single file and its
# certificate pin lives here and nowhere else. The packager and the tests call its modes
# (--verify-only, --check-archive, --check-tag, --print-pin) instead of reading this file.
# Runs under stock macOS Bash 3.2.
#
# What it touches: the app bundle in the install directory, a `.tester-previous` sibling while it
# swaps, its own `.tester-install.*` staging directory and the `.tester-install.lock` directory
# that keeps a second run out. Nothing else: no state, caches, models or preferences, and never
# sudo. It deletes only its staging directory; a superseded app copy is moved into staging first,
# and only once a verified app is in place.
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
MAX_UNZIPPED_BYTES=1073741824 # ~20x the unpacked app; bounds what extraction may write
MAX_SUM_BYTES=4096
STAGE=""
LOCK=""
TARGET=""
PREV=""

usage() {
    echo "usage: install_tester.sh --verify-only <app-bundle> | --check-archive <zip> | --check-tag <tag> | --print-pin | <tag>" >&2
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
    # `|| major=""`: under pipefail a failing sw_vers would otherwise end the script silently.
    major="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)" || major=""
    case "$major" in
        '' | *[!0-9]*) die "could not read the macOS version" ;;
    esac
    [ "$major" -ge "$MIN_MACOS_MAJOR" ] || die "this build needs macOS $MIN_MACOS_MAJOR or newer (found $major)"
}

# Refuse while the app runs: replacing a live bundle leaves the old build running. The same
# probe, read the same way, as scripts/quit_app.sh: stdout and stderr apart. A Mac that has never
# seen the app (a first install) makes osascript fail with -1728 "Can't get application id":
# LaunchServices knows no app with that id, and a running app is always known to it, so a
# non-zero exit with -1728 on stderr means "not running". Otherwise only a successful `false`
# on stdout proceeds; any other failure or answer is "cannot tell" and refuses. stderr goes to
# the staging directory, so this runs only once it exists.
check_not_running() {
    local answer rc=0
    answer="$(osascript -e "application id \"$BUNDLE_ID\" is running" 2>"$STAGE/probe.err")" || rc=$?
    if [ "$rc" -ne 0 ]; then
        grep -qF -- '(-1728)' "$STAGE/probe.err" ||
            die "could not tell whether the app is running; quit it and run this again"
        return 0
    fi
    case "$answer" in
        false) ;;
        true) die "the app is running; quit Pipecat Service Toolbar, then run this again" ;;
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

# Checks on the ZIP's central directory, before anything is extracted. They are not the last
# word: ditto reads other fields than unzip (local headers, a non-Unix host's mode bits), so
# check_extracted_tree checks what ditto actually wrote before anything is verified or moved.
check_archive_shape() {
    local zip="$1" names entry listing totals bytes
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
    # Bound what extraction may write: the download cap bounds compressed bytes only. The total
    # line reads "<n> files, <bytes> bytes uncompressed, <bytes> bytes compressed: <ratio>".
    totals="$(unzip -Zt "$zip")" || die "cannot read the archive"
    bytes="${totals#*, }"
    bytes="${bytes%% bytes uncompressed*}"
    case "$bytes" in
        '' | *[!0-9]*) die "cannot read the archive's unpacked size" ;;
    esac
    [ "$bytes" -le "$MAX_UNZIPPED_BYTES" ] ||
        die "the archive unpacks to $bytes bytes, more than the $MAX_UNZIPPED_BYTES allowed"
}

# What ditto wrote into $1: exactly the app bundle at the top, and inside it only directories
# and regular files. No symlink, device, FIFO or socket, whatever the central directory said.
check_extracted_tree() {
    local extract="$1" odd
    odd="$(find "$extract" -mindepth 1 \( -type l -o \( ! -type f ! -type d \) \) -print)" ||
        die "cannot read the unpacked archive"
    [ -z "$odd" ] || die "the archive unpacks to something other than files and folders: ${odd%%$'\n'*}"
    [ -d "$extract/$BUNDLE_NAME" ] || die "the archive does not unpack to $BUNDLE_NAME"
    [ "$(ls -A "$extract" | wc -l | tr -d ' ')" = "1" ] || die "the archive holds more than the app"
}

# --check-archive: the archive checks alone, for the packager. No staging directory, no network.
check_archive() {
    if [ ! -f "$1" ]; then
        echo "error: not a file: $1" >&2
        return 2
    fi
    check_archive_shape "$1"
    echo "ok: the archive holds only $BUNDLE_NAME, within the size limit"
}

# Only one installer at a time per install directory: a second run would share $PREV with the
# first. mkdir is atomic. A lock left by a run that was killed is never removed here: the
# message names it, and the person removes it once no install is running.
acquire_lock() {
    local lock="$1"
    if ! mkdir "$lock" 2>/dev/null; then
        [ -d "$lock" ] || die "could not create $lock"
        die "another tester install is running in $(dirname "$lock"), or one was interrupted; if none is running, remove the folder $lock and run this again"
    fi
    LOCK="$lock"
}

# EXIT trap. Deletes the staging directory, never a copy that may be the only good one: a copy
# moved into staging (superseded, replaced) went there only once a verified app was in place,
# so if neither the app nor $PREV exists now, it goes back to $PREV for the next run to restore.
# Then releases the lock, if this run took it.
cleanup() {
    local copy keep=""
    set +e
    if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
        for copy in replaced superseded; do
            [ -e "$STAGE/$copy" ] || continue
            [ ! -e "$TARGET" ] && [ ! -e "$PREV" ] || continue
            if mv "$STAGE/$copy" "$PREV"; then
                echo "note: no app is installed; the previous copy is kept at $PREV" >&2
            else
                keep=1
                echo "note: the previous copy is kept at $STAGE/$copy" >&2
            fi
        done
        [ -n "$keep" ] || rm -rf "$STAGE"
    fi
    if [ -n "$LOCK" ]; then
        rmdir "$LOCK" 2>/dev/null || echo "note: could not remove $LOCK" >&2
    fi
}

# Start-up: an earlier run that stopped mid-swap can leave the old copy at $PREV. With no app,
# it is put back. With an app, the app decides: $PREV is cleared only if the app verifies
# against the pin; if it does not, $PREV may be the last good copy, so nothing is touched.
recover_previous() {
    [ -e "$PREV" ] || return 0
    if [ ! -e "$TARGET" ]; then
        mv "$PREV" "$TARGET" || die "could not restore the previous copy from $PREV"
        echo "restored the previous copy left by an interrupted install"
    elif verify_signer "$TARGET" >/dev/null 2>&1; then
        mv "$PREV" "$STAGE/superseded" || die "could not clear $PREV"
    else
        die "the installed app at $TARGET does not pass the certificate check, and $PREV holds the copy from before it; nothing was changed. To go back to that copy, move $TARGET to the Trash and rename $PREV to \"$BUNDLE_NAME\"; or tell the owner what this printed"
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

    dest="${APP_INSTALL_DIR:-$HOME/Applications}"
    TARGET="$dest/$BUNDLE_NAME"
    PREV="$dest/.tester-previous"
    target="$TARGET"
    prev="$PREV"
    mkdir -p "$dest"
    trap cleanup EXIT
    acquire_lock "$dest/.tester-install.lock"
    STAGE="$(mktemp -d "$dest/.tester-install.XXXXXX")"
    check_not_running
    recover_previous

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
    check_extracted_tree "$extract"

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

    # recover_previous cleared $prev and the lock keeps other runs out; `mv` onto an existing
    # $prev would nest the app inside it.
    [ ! -e "$prev" ] || die "$prev appeared during the install; nothing was changed"
    if [ -e "$target" ]; then
        mv "$target" "$prev" ||
            die "could not move the installed app aside; if macOS asked about App Management, allow Terminal in System Settings > Privacy & Security > App Management, or remove the app by hand, then run this again"
    fi
    if ! mv "$extract/$BUNDLE_NAME" "$target"; then
        die "could not install$(restore_previous "$target" "$prev")"
    fi
    if ! verify_signer "$target" >/dev/null; then
        if ! mv "$target" "$STAGE/rejected"; then
            if [ -e "$prev" ]; then
                die "the installed copy failed verification and could not be moved aside: $target is an unverified build, do not open it; the previous copy is at $prev"
            fi
            die "the installed copy failed verification and could not be moved aside: $target is an unverified build, do not open it; there was no previous copy"
        fi
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
        --check-archive)
            if [ "$#" -ne 2 ]; then
                usage
                return 2
            fi
            check_archive "$2"
            ;;
        --check-tag)
            if [ "$#" -ne 2 ]; then
                usage
                return 2
            fi
            [[ "$2" =~ $TAG_PATTERN ]] || {
                echo "error: not a tester tag: $2" >&2
                return 1
            }
            ;;
        --print-pin)
            if [ "$#" -ne 1 ]; then
                usage
                return 2
            fi
            echo "$TESTER_CERT_SHA1"
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
