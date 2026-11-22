#!/bin/bash
# Pipecat Service Toolbar tester installer (interim, self-signed channel).
#
#   install_tester.sh --verify-only <app-bundle>   signer check only: no network, no install
#   install_tester.sh --check-drop <zip> <tag>     every check the installer makes on a downloaded
#                                                  drop: no network, no install
#   install_tester.sh --check-tag <tag>            exit 0 if the installer accepts the tag name, else 1
#   install_tester.sh --print-pin                  print the certificate pin
#   install_tester.sh <tag>                        install a tester drop (APP_INSTALL_DIR overrides ~/Applications)
#
# Plan: docs/plans/20261121-feature-selfsigned-tester-channel.md
#
# This file is fetched by `curl` at a full commit SHA on main, so it is a single file and its
# certificate pin and limits live here and nowhere else. The packager and the tests call its modes
# (--verify-only, --check-drop, --check-tag, --print-pin) instead of reading this file.
# Runs under stock macOS Bash 3.2.
#
# What it touches: the app bundle in the install directory, a `.tester-previous` sibling while it
# swaps, its own `.tester-install.*` staging directory, the `.tester-install.lock` directory that
# keeps a second run out, and a `.tester-leftover.*` directory when a failed run has to keep an
# app copy it moved. Nothing else: no state, caches, models or preferences, and never sudo.
#
# What it deletes: only what the run itself created (its download, checksum, probe output and
# unpacked copy), and the app copies it replaced once its own copy is verified in place. A copy
# it moved out of the way on a run that did not finish is put back where possible, otherwise
# kept in a `.tester-leftover.*` directory and named; never deleted.
set -euo pipefail

# SHA-1 of the owner's self-signed "Code Signing" certificate (login keychain). Every tester
# build must be signed by it; a bundle signed by anything else is refused.
TESTER_CERT_SHA1="AAC7E2B994E42BED55FAD8E292CF3BA1A4629CEE"
BUNDLE_ID="ai.pipecat.ServiceToolbar"

BUNDLE_NAME="Pipecat Service Toolbar.app"
REPO_URL="https://github.com/pipecat-ai/pipecat-service-toolbar"
TAG_PATTERN='^tester-([0-9]+\.[0-9]+\.[0-9]+)-b([0-9]+)-[0-9a-f]{7,12}$'
MIN_MACOS_MAJOR=15
MAX_ZIP_BYTES=262144000       # 250 MiB; the app's ZIP is ~20 MB
MAX_UNZIPPED_BYTES=1073741824 # 1 GiB, ~20x the unpacked app; bounds what extraction may write
MAX_ENTRIES=10000             # the app's ZIP has ~45 entries; bounds what extraction may create
MAX_PATH_BYTES=1024           # macOS PATH_MAX; bounds the listing the checks read
MAX_SUM_BYTES=4096

# State of one install run, read by rollback and cleanup.
STAGE=""
LOCK=""
TARGET=""
PREV=""
OWN_INODE=""        # inode of the app this run unpacked: proves a copy is this run's own
STALE_PREV=""       # start-up found .tester-previous beside an app that verifies
HAD_APP=""          # an app was at the target when the swap began
AT_MOVE_IN=""       # the swap reached step 3
MOVED_SUPERSEDED="" # swap step 1 done: .tester-previous is in staging as superseded
MOVED_ASIDE=""      # swap step 2 done: the installed app is at .tester-previous
MOVED_IN=""         # swap step 3 done: this run's copy is at the target
INSTALLED=""        # this run's copy is verified in place
ROLLBACK_NOTE=""
DROP_VERSION=""
DROP_BUILD=""
CHECK_SCRATCH=""

usage() {
    echo "usage: install_tester.sh --verify-only <app-bundle> | --check-drop <zip> <tag> | --check-tag <tag> | --print-pin | <tag>" >&2
}

die() {
    echo "error: $*" >&2
    exit 1
}

# Signal policy. Outside the swap, HUP, INT and TERM end the run through `exit`, which bash runs
# only after the foreground child (curl, ditto, codesign, mv) has exited, so cleanup and the
# lock release never race it. Without these traps Bash 3.2 runs the EXIT trap at once and leaves
# the child running. During the swap the signals are ignored, by the shell and by the mv and
# codesign children it starts, so the swap always completes or rolls back.
catch_signals() {
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

block_signals() {
    trap '' HUP INT TERM
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

# The inode of a path itself (a symlink is not followed). `ls -di` reads the same on macOS and
# Linux, where `stat` does not.
inode_of() {
    local listing ino rest
    listing="$(ls -did -- "$1" 2>/dev/null)" || return 1
    read -r ino rest <<EOF
$listing
EOF
    echo "$ino"
}

exists() {
    [ -e "$1" ] || [ -L "$1" ]
}

# The only way an app copy is moved. `mv src dst` puts src *inside* dst when dst is a directory,
# and another program (`just app-install`, Finder) can create the target at any moment. So:
#   0  dst is now the very object src was (same inode), and src is gone;
#   1  nothing moved (src missing, or mv failed);
#   2  nothing moved: dst was already taken;
#   3  mv nested src inside a dst that appeared meanwhile; it was moved back to src;
#   4  as 3, but it could not be moved back: it is at dst/<name>, and a note says so.
safe_move() {
    local src="$1" dst="$2" ino nested
    ino="$(inode_of "$src")" || return 1
    [ -n "$ino" ] || return 1
    if exists "$dst"; then
        return 2
    fi
    mv "$src" "$dst" || return 1
    if [ "$(inode_of "$dst")" = "$ino" ] && ! exists "$src"; then
        return 0
    fi
    nested="$dst/${src##*/}"
    if [ "$(inode_of "$nested")" = "$ino" ] && ! exists "$src" && mv "$nested" "$src" &&
        [ "$(inode_of "$src")" = "$ino" ]; then
        return 3
    fi
    echo "note: $src was moved inside $dst, which appeared meanwhile, and could not be moved back; it is at $nested" >&2
    return 4
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
    local zip="$1" totals count bytes names entry listing missing
    # Bound what extraction may create and write: the download cap bounds compressed bytes only,
    # and empty entries add no bytes. The total line reads
    # "<n> files, <bytes> bytes uncompressed, <bytes> bytes compressed: <ratio>".
    totals="$(unzip -Zt "$zip")" || die "cannot read the archive"
    count="${totals%% *}"
    case "$count" in
        '' | *[!0-9]*) die "cannot read the archive's entry count" ;;
    esac
    [ "$count" -le "$MAX_ENTRIES" ] ||
        die "the archive holds $count entries, more than the $MAX_ENTRIES allowed"
    bytes="${totals#*, }"
    bytes="${bytes%% bytes uncompressed*}"
    case "$bytes" in
        '' | *[!0-9]*) die "cannot read the archive's unpacked size" ;;
    esac
    [ "$bytes" -le "$MAX_UNZIPPED_BYTES" ] ||
        die "the archive unpacks to $bytes bytes, more than the $MAX_UNZIPPED_BYTES allowed"
    names="$(unzip -Z1 "$zip")" || die "cannot read the archive"
    [ -n "$names" ] || die "the archive is empty"
    while IFS= read -r entry; do
        [ "${#entry}" -le "$MAX_PATH_BYTES" ] ||
            die "the archive holds a path longer than $MAX_PATH_BYTES bytes"
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
    # Every folder a path implies is itself an entry (ditto -c -k lists them all), so extraction
    # creates no more paths than the entries counted above.
    missing="$(awk '{ seen[$0] = 1; name[NR] = $0 }
        END {
            for (i = 1; i <= NR; i++) {
                p = name[i]; sub(/\/$/, "", p)
                if (p !~ /\//) continue
                sub(/\/[^\/]*$/, "/", p)
                if (!(p in seen)) { print p; exit }
            }
        }' <<<"$names")" || die "cannot read the archive"
    [ -z "$missing" ] || die "the archive does not list the folder $missing"
}

# What ditto wrote into $1: exactly the app bundle at the top, and inside it only directories
# and regular files, no more of them than the entry limit. No symlink, device, FIFO or socket,
# whatever the central directory said.
check_extracted_tree() {
    local extract="$1" odd count
    odd="$(find "$extract" -mindepth 1 \( -type l -o \( ! -type f ! -type d \) \) -print)" ||
        die "cannot read the unpacked archive"
    [ -z "$odd" ] || die "the archive unpacks to something other than files and folders: ${odd%%$'\n'*}"
    [ -d "$extract/$BUNDLE_NAME" ] || die "the archive does not unpack to $BUNDLE_NAME"
    [ "$(ls -A "$extract" | wc -l | tr -d ' ')" = "1" ] || die "the archive holds more than the app"
    count="$(find "$extract" -mindepth 1 -print | wc -l | tr -d ' ')" ||
        die "cannot read the unpacked archive"
    [ "$count" -le "$MAX_ENTRIES" ] || die "the archive unpacks to $count entries, more than the $MAX_ENTRIES allowed"
}

# Modes and access lists come from the ZIP (ditto applies an ACL carried in an AppleDouble entry,
# even with --noacl), and codesign does not seal them: a repackaged genuine drop could install
# code that other users may change. So the unpacked tree gets owner read/write (and search on
# folders), no group or other write, no setuid or setgid, and no ACL. $1 is the scratch folder
# this run made, never a path from the archive, so chmod follows no symlink from the ZIP.
normalise_modes() {
    local tree="$1" loose
    chmod -R u+rwX,go-w,ug-s "$tree" || die "could not reset the permissions of the unpacked app"
    chmod -R -N "$tree" || die "could not remove access lists from the unpacked app"
    loose="$(find "$tree" \( -perm -020 -o -perm -002 -o -perm -4000 -o -perm -2000 \) -print)" ||
        die "cannot read the unpacked archive"
    [ -z "$loose" ] || die "the unpacked app is writable by others: ${loose%%$'\n'*}"
}

# The one offline acceptance of a downloaded drop, used by the install and by --check-drop (and
# so by the packager). $1 the ZIP, $2 the tag, $3 an empty scratch folder. Dies unless every check
# passes: the tag, the download cap, the central directory (paths, no symlink, entry count,
# unpacked size), extraction, the extracted tree, mode and ACL normalisation, the pinned signer,
# and the version and build the tag names. Then the verified app is at $3/$BUNDLE_NAME and
# DROP_VERSION and DROP_BUILD hold its version and build.
accept_drop() {
    local zip="$1" tag="$2" scratch="$3" version build size app
    if ! [[ "$tag" =~ $TAG_PATTERN ]]; then
        die "not a tester tag: $tag"
    fi
    version="${BASH_REMATCH[1]}"
    build="${BASH_REMATCH[2]}"
    size="$(wc -c <"$zip" | tr -d ' ')" || die "cannot read the archive"
    case "$size" in
        '' | *[!0-9]*) die "cannot read the archive's size" ;;
    esac
    [ "$size" -le "$MAX_ZIP_BYTES" ] ||
        die "the archive is $size bytes, more than the $MAX_ZIP_BYTES allowed"
    check_archive_shape "$zip"
    ditto -x -k "$zip" "$scratch" || die "could not unpack the archive"
    check_extracted_tree "$scratch"
    normalise_modes "$scratch"
    app="$scratch/$BUNDLE_NAME"
    verify_signer "$app" >/dev/null ||
        die "refusing a build that is not signed by the pinned tester certificate"
    DROP_VERSION="$(plist_value "$app" CFBundleShortVersionString)" || DROP_VERSION=""
    DROP_BUILD="$(plist_value "$app" CFBundleVersion)" || DROP_BUILD=""
    if [ "$DROP_VERSION" != "$version" ] || [ "$DROP_BUILD" != "$build" ]; then
        die "the app inside is version $DROP_VERSION, build $DROP_BUILD, not the version $version, build $build the tag names"
    fi
}

remove_scratch() {
    set +e
    if [ -n "$CHECK_SCRATCH" ] && [ -d "$CHECK_SCRATCH" ]; then
        chmod -R u+rwX "$CHECK_SCRATCH" 2>/dev/null
        rm -rf "$CHECK_SCRATCH"
        ! exists "$CHECK_SCRATCH" || echo "note: could not remove $CHECK_SCRATCH" >&2
    fi
}

# --check-drop: accept_drop on a local ZIP, in a scratch folder of its own (removed on exit).
# No network, no install directory.
check_drop() {
    local zip="$1" tag="$2"
    if [ ! -f "$zip" ]; then
        echo "error: not a file: $zip" >&2
        return 2
    fi
    trap remove_scratch EXIT
    catch_signals
    CHECK_SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/tester-check-drop.XXXXXX")" ||
        die "could not create a scratch folder"
    accept_drop "$zip" "$tag" "$CHECK_SCRATCH"
    echo "ok: $zip is a drop the installer accepts for $tag (version $DROP_VERSION, build $DROP_BUILD)"
}

# Folders an earlier run left beside the app: staging (.tester-install.*, other than the lock)
# and kept copies (.tester-leftover.*). Read only; nothing here removes them.
report_strays() {
    local dest="$1" lead="$2" path
    for path in "$dest"/.tester-install.* "$dest"/.tester-leftover.*; do
        exists "$path" || continue
        [ "$path" != "$dest/.tester-install.lock" ] || continue
        echo "note: $lead $path (it may hold a copy of the app; it is not removed automatically)" >&2
    done
}

# Only one installer at a time per install directory: a second run would share $PREV with the
# first. mkdir is atomic. A lock left by a run that was killed is never removed here: the
# message names it, and the person removes it once no install is running.
acquire_lock() {
    local lock="$1" dest
    dest="$(dirname "$lock")"
    if ! mkdir "$lock" 2>/dev/null; then
        [ -d "$lock" ] || die "could not create $lock"
        echo "error: another tester install is running in $dest, or one was interrupted; if none is running, remove the folder $lock and run this again" >&2
        report_strays "$dest" "also beside the app:"
        exit 1
    fi
    LOCK="$lock"
}

# Undo the swap's completed steps in reverse order, each only as far as its destination is free
# and the copy is the one this run moved. What cannot be undone stays where it is, and
# ROLLBACK_NOTE (the end of the caller's error message) says where.
rollback() {
    ROLLBACK_NOTE=""
    if [ -z "$MOVED_SUPERSEDED$MOVED_ASIDE$MOVED_IN" ]; then
        ROLLBACK_NOTE="; nothing was changed"
        [ -z "$AT_MOVE_IN" ] || [ -n "$HAD_APP" ] ||
            ROLLBACK_NOTE="$ROLLBACK_NOTE; there was no previous copy to restore"
        return 0
    fi
    if [ -n "$MOVED_IN" ]; then
        if [ "$(inode_of "$TARGET")" != "$OWN_INODE" ]; then
            ROLLBACK_NOTE="; $TARGET is not the copy this install put there and was left as it is"
        elif safe_move "$TARGET" "$STAGE/rejected"; then
            MOVED_IN=""
        else
            ROLLBACK_NOTE="; the installed copy could not be moved aside: $TARGET is an unverified build, do not open it"
        fi
    fi
    if [ -n "$MOVED_ASIDE" ]; then
        if safe_move "$PREV" "$TARGET"; then
            MOVED_ASIDE=""
            ROLLBACK_NOTE="$ROLLBACK_NOTE; the previous copy was restored"
        else
            ROLLBACK_NOTE="$ROLLBACK_NOTE; could not restore the previous copy, it is at $PREV"
        fi
    elif [ -z "$HAD_APP" ]; then
        ROLLBACK_NOTE="$ROLLBACK_NOTE; there was no previous copy to restore"
    fi
    if [ -n "$MOVED_SUPERSEDED" ] && safe_move "$STAGE/superseded" "$PREV"; then
        MOVED_SUPERSEDED="" # otherwise cleanup keeps it and names it
    fi
}

swap_fail() {
    rollback
    die "$1$ROLLBACK_NOTE"
}

# The swap, with HUP, INT and TERM ignored. Each move goes through safe_move; any failure rolls
# back. The copies it replaced are deleted (by cleanup) only once this run's own copy is verified
# at the target.
swap() {
    local rc=0
    block_signals
    # 1. A stale .tester-previous (start-up found the app verified) makes room for the old app.
    if exists "$PREV"; then
        [ -n "$STALE_PREV" ] || swap_fail "$PREV appeared during the install"
        safe_move "$PREV" "$STAGE/superseded" || swap_fail "could not clear $PREV"
        MOVED_SUPERSEDED=1
    fi
    # 2. The installed app moves aside.
    if exists "$TARGET"; then
        HAD_APP=1
        safe_move "$TARGET" "$PREV" ||
            swap_fail "could not move the installed app aside; if macOS asked about App Management, allow Terminal in System Settings > Privacy & Security > App Management, or remove the app by hand, then run this again"
        MOVED_ASIDE=1
    fi
    # 3. This run's copy moves in, never into something another program put at the target.
    AT_MOVE_IN=1
    safe_move "$STAGE/extract/$BUNDLE_NAME" "$TARGET" || rc=$?
    case "$rc" in
        0) MOVED_IN=1 ;;
        2 | 3 | 4) swap_fail "another program put something at $TARGET during the install; it was left as it is" ;;
        *) swap_fail "could not install" ;;
    esac
    # 4. The installed copy verifies, and is still this run's own.
    verify_signer "$TARGET" >/dev/null || swap_fail "the installed copy failed verification"
    [ "$(inode_of "$TARGET")" = "$OWN_INODE" ] ||
        swap_fail "another program replaced $TARGET during the install"
    INSTALLED=1
    # 5. The replaced copy goes to staging, deleted with it.
    if [ -n "$MOVED_ASIDE" ]; then
        safe_move "$PREV" "$STAGE/replaced" || echo "note: the previous copy is left at $PREV" >&2
    fi
    catch_signals
}

# EXIT trap. Runs after the last child has exited (see catch_signals). Deletes only what this run
# created, plus, once its own copy is verified in place, the copies it replaced. A copy it moved
# in from the install directory on a run that did not finish (superseded, or a rejected copy that
# is not its own) is kept: the staging folder becomes .tester-leftover.<same suffix> and each copy
# is named. Then releases the lock, if this run took it.
cleanup() {
    local copy kept="" leftover
    set +e
    if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
        if [ -n "$INSTALLED" ]; then
            chmod -R u+rwX "$STAGE" 2>/dev/null
            rm -rf "$STAGE"
        else
            rm -f "$STAGE/probe.err" "$STAGE/drop.zip" "$STAGE/drop.zip.sha256"
            if [ -d "$STAGE/extract" ]; then
                chmod -R u+rwX "$STAGE/extract" 2>/dev/null
                rm -rf "$STAGE/extract"
            fi
            if exists "$STAGE/rejected" && [ -n "$OWN_INODE" ] &&
                [ "$(inode_of "$STAGE/rejected")" = "$OWN_INODE" ]; then
                chmod -R u+rwX "$STAGE/rejected" 2>/dev/null
                rm -rf "$STAGE/rejected"
            fi
            # Only these names are copies moved in from the install directory; anything else
            # left is this run's own and could not be removed.
            for copy in superseded replaced rejected; do
                ! exists "$STAGE/$copy" || kept=1
            done
            if [ -n "$kept" ]; then
                leftover="$(dirname "$STAGE")/.tester-leftover.${STAGE##*.}"
                safe_move "$STAGE" "$leftover" || leftover="$STAGE"
                for copy in "$leftover"/* "$leftover"/.[!.]*; do
                    exists "$copy" || continue
                    case "${copy##*/}" in
                        rejected) echo "note: a copy that failed the certificate check is kept at $copy; do not open it" >&2 ;;
                        superseded | replaced) echo "note: an earlier copy of the app that this install moved is kept at $copy" >&2 ;;
                        *) echo "note: could not remove $copy" >&2 ;;
                    esac
                done
            else
                rmdir "$STAGE" 2>/dev/null
            fi
        fi
        if [ -z "$kept" ] && exists "$STAGE"; then
            echo "note: could not remove this install's staging folder $STAGE; remove it once no install is running" >&2
        fi
    fi
    if [ -n "$LOCK" ]; then
        rmdir "$LOCK" 2>/dev/null || echo "note: could not remove $LOCK" >&2
    fi
}

# Start-up: an earlier run that stopped mid-swap can leave the old copy at $PREV. With no app,
# it is put back. With an app, the app decides: if it verifies against the pin, $PREV is stale and
# the swap clears it (so a run that fails before the swap leaves it where it is); if it does not,
# $PREV may be the last good copy, so nothing is touched.
recover_previous() {
    local rc=0
    exists "$PREV" || return 0
    if ! exists "$TARGET"; then
        block_signals
        safe_move "$PREV" "$TARGET" || rc=$?
        catch_signals
        case "$rc" in
            0) echo "restored the previous copy left by an interrupted install" ;;
            2 | 3) die "another program put something at $TARGET while the previous copy was being restored; nothing was changed, the previous copy is at $PREV" ;;
            4) die "could not restore the previous copy from $PREV" ;;
            *) die "could not restore the previous copy from $PREV; nothing was changed" ;;
        esac
    elif verify_signer "$TARGET" >/dev/null 2>&1; then
        STALE_PREV=1
    else
        die "the installed app at $TARGET does not pass the certificate check, and $PREV holds the copy from before it; nothing was changed. To go back to that copy, move $TARGET to the Trash and rename $PREV to \"$BUNDLE_NAME\"; or tell the owner what this printed"
    fi
}

install_drop() {
    local tag="$1" dest zip sum expected actual old_version="" old_build=""

    if ! [[ "$tag" =~ $TAG_PATTERN ]]; then
        die "not a tester tag: $tag"
    fi

    check_platform

    dest="${APP_INSTALL_DIR:-$HOME/Applications}"
    TARGET="$dest/$BUNDLE_NAME"
    PREV="$dest/.tester-previous"
    mkdir -p "$dest"
    trap cleanup EXIT
    catch_signals
    acquire_lock "$dest/.tester-install.lock"
    report_strays "$dest" "an earlier install that failed or was interrupted left"
    STAGE="$(mktemp -d "$dest/.tester-install.XXXXXX")"
    check_not_running
    recover_previous

    zip="$STAGE/drop.zip"
    sum="$STAGE/drop.zip.sha256"
    echo "downloading $tag"
    fetch "$REPO_URL/releases/download/$tag/$tag.zip" "$zip" "$MAX_ZIP_BYTES" 600 ||
        die "could not download the ZIP for $tag"
    fetch "$REPO_URL/releases/download/$tag/$tag.zip.sha256" "$sum" "$MAX_SUM_BYTES" 60 ||
        die "could not download the checksum for $tag"

    # The checksum only catches corruption; the certificate pin is the authenticity check.
    expected="$(cut -d' ' -f1 <"$sum")"
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "the checksum file is malformed"
    actual="$(sha256_of "$zip")"
    [ "$expected" = "$actual" ] || die "the download is corrupt (SHA-256 mismatch)"

    mkdir "$STAGE/extract"
    accept_drop "$zip" "$tag" "$STAGE/extract"
    OWN_INODE="$(inode_of "$STAGE/extract/$BUNDLE_NAME")" || OWN_INODE=""
    [ -n "$OWN_INODE" ] || die "cannot read the unpacked app"

    if [ -d "$TARGET" ]; then
        old_version="$(plist_value "$TARGET" CFBundleShortVersionString)" || old_version="?"
        old_build="$(plist_value "$TARGET" CFBundleVersion)" || old_build="?"
    fi

    check_not_running # it may have been started during the download

    swap

    echo "installed $tag: version $DROP_VERSION, build $DROP_BUILD"
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
        --check-drop)
            if [ "$#" -ne 3 ]; then
                usage
                return 2
            fi
            check_drop "$2" "$3"
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
