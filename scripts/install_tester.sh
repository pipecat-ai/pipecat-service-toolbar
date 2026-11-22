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
# What it touches: the app bundle in the install directory, a `.tester-previous` sibling (during
# the swap, and after a successful install as the rollback copy: the build that install
# replaced), its own `.tester-install.*` staging directory, the `.tester-install.lock` directory that
# keeps a second run out, and a `.tester-leftover.*` directory when a failed run has to keep an
# app copy it moved. Nothing else: no state, caches, models or preferences, and never sudo.
#
# What it deletes: only what the run itself created (its download, checksum, probe output and
# the unpacked copy it never moved), and the older rollback copy once its own copy is verified in
# place and the new rollback copy has itself verified. A successful install never deletes the
# build it replaced. A copy it moved out of the way on a run that did not finish is put back where
# possible, otherwise kept in a `.tester-leftover.*` directory and named; never deleted.
#
# Contract (app copies): a copy is restored to, kept at, or left at the app path only after it
# passes app_ok there (the pinned signer, a runnable main executable, and still the copy expected).
# A copy that fails is never left at the app path and never deleted: it is moved into a named
# `.tester-leftover.*` directory (or left where it is if it cannot be moved) and the message says
# "do not open it". The same holds for the build an update saves as `.tester-previous`.
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
MAX_PATH_BYTES=1024           # macOS PATH_MAX; bounds the listing the checks read (stock unzip
                              # already fails on a name of 1024 bytes or more; this is the backstop)
MAX_SUM_BYTES=4096

# State of one install run, read by rollback and cleanup.
STAGE=""
LOCK=""
TARGET=""
PREV=""
OWN_INODE=""        # inode of the app this run unpacked: proves a copy is this run's own
STAGE_INODE=""
OLD_INODE=""        # inode at the target when the install began (or recovered there)
STALE_INODE=""      # inode at .tester-previous beside a verified target at start-up
AT_MOVE_IN=""       # the swap reached step 3
MOVED_SUPERSEDED="" # swap step 1 done: .tester-previous is in staging as superseded
MOVED_ASIDE=""      # swap step 2 done: the installed app is at .tester-previous
REJECTED=""         # step 3 put a copy at the target that failed app_ok, and it was moved out
STUCK=""            # ... and it could not be moved out: it is still at the target
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

# Signal policy. Outside the swap and cleanup, these signals end the run through `exit`, which bash runs
# only after the foreground child (curl, ditto, codesign, the osascript rename) has exited, so
# cleanup and the lock release never race it. Without these traps Bash 3.2 runs the EXIT trap at
# once and leaves the child running. During the swap the signals are ignored, by the shell and by
# the osascript and codesign children it starts, so the swap always completes or rolls back. They are ignored as
# well during cleanup (so a second signal cannot cut it short with the lock held) and while the
# lock, the staging folder or the scratch folder is created together with the variable that
# names it (so cleanup always knows what this run created).
catch_signals() {
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 131' QUIT
    trap 'exit 141' PIPE
    trap 'exit 143' TERM
}

block_signals() {
    trap '' HUP INT QUIT PIPE TERM
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
        shasum -a 256 <"$1" | cut -d' ' -f1
    else
        sha256sum <"$1" | cut -d' ' -f1
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

# The kernel enforces the vacant destination even if Finder or another installer creates it
# after the shell's check. A plain mv can overwrite a path or move the source inside it.
safe_move() {
    local src="$1" dst="$2" expected="$3"
    [ -n "$expected" ] && [ "$(inode_of "$src")" = "$expected" ] || return 1
    if exists "$dst"; then
        return 2
    fi
    # RENAME_EXCL is 0x4 in Darwin's <sys/stdio.h>. Both paths are on the install volume.
    if ! osascript -l JavaScript -e '
        ObjC.bindFunction("renamex_np", ["int", ["char *", "char *", "unsigned int"]]);
        function run(paths) {
            if ($.renamex_np(paths[0], paths[1], 4) !== 0) throw Error("exclusive rename failed");
        }' "$src" "$dst" >/dev/null; then
        if exists "$dst"; then
            return 2
        fi
        return 1
    fi
    if [ "$(inode_of "$dst")" != "$expected" ]; then
        echo "note: $src or $dst changed during the move; check both locations" >&2
        return 4
    fi
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
    local zip="$1" totals count names entry listing missing archive_status
    # C locale: ${#entry} counts bytes, not the characters of the UTF-8 locale `curl | bash`
    # inherits, and ZipInfo renders names one byte at a time.
    local LC_ALL=C ZIPINFO="" ZIPINFOOPT=""
    export LC_ALL ZIPINFO ZIPINFOOPT
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
    # ZipInfo's totals can wrap. Check every size against the remaining budget before adding it.
    listing="$(unzip -Zl "$zip")" || die "cannot read the archive"
    archive_status="$(awk -v expected="$count" -v limit="$MAX_UNZIPPED_BYTES" '
        NR <= 2 { next }
        /^[0-9]+ files?, / { footer++; next }
        {
            if (footer || $4 !~ /^[0-9]+$/) invalid = 1
            else if ($4 + 0 > limit - total) oversized = 1
            else total += $4 + 0
            entries++
        }
        END {
            if (invalid || entries != expected || footer != 1) print "invalid"
            else if (oversized) print "oversized"
        }' <<<"$listing")" || die "cannot read the archive"
    case "$archive_status" in
        invalid) die "cannot read the archive's unpacked size" ;;
        oversized) die "the archive unpacks to more than the $MAX_UNZIPPED_BYTES bytes allowed" ;;
    esac
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
        # In the C locale ZipInfo substitutes ? for non-ASCII bytes and caret escapes for
        # control characters. Neither representation is a reliable folder key.
        case "$entry" in
            *'?'* | *'^'[@-_]* | *[[:cntrl:]]*) die "the archive holds an ambiguous path: $entry" ;;
        esac
    done <<EOF
$names
EOF
    # An early refusal of a symlink entry the listing shows (a Unix-host entry marked `l`). It is
    # not what keeps extraction inside the staging directory: an entry from a FAT (host 0) or NTFS
    # (host 11) host with symlink mode bits lists with no `l`, and ditto still makes a link of it.
    # What protects is that ditto creates symlinks last and keeps every path inside its
    # destination, and that check_extracted_tree refuses any symlink after extraction, before
    # anything is verified or moved. The listing is
    # read in full and searched through a here-string: `unzip -Z | grep -q` under pipefail
    # reports the pipeline as failed (unzip dies of SIGPIPE once grep exits on the first match)
    # whenever the listing outgrows the pipe buffer, and the symlink would go unseen.
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
    count="$(find "$extract" -mindepth 1 -print0 | tr -cd '\000' | wc -c | tr -d ' ')" ||
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

check_main_executable() {
    local app="$1" executable
    executable="$(plist_value "$app" CFBundleExecutable)" || executable=""
    case "$executable" in
        '' | . | .. | */*) echo "error: the app has an invalid main executable name" >&2; return 1 ;;
    esac
    if [ ! -f "$app/Contents/MacOS/$executable" ] || [ ! -x "$app/Contents/MacOS/$executable" ]; then
        echo "error: the app's main executable cannot be run: $executable" >&2
        return 1
    fi
}

# The one acceptance of an app copy at a place, used for every keep, restore and delete decision:
# $1 is the copy expected there (inode $2), it is signed by the pinned certificate, its main
# executable can run, and it is still that copy afterwards. With $3 = drop it must also carry the
# version and build of this run's drop: inode numbers can be reused once a copy is deleted (Linux
# does at once; APFS does not), so the inode alone cannot tell this run's copy from a replacement.
app_ok() {
    local app="$1" expected="$2" what="${3:-}" version build
    [ -n "$expected" ] && [ "$(inode_of "$app")" = "$expected" ] || return 1
    verify_signer "$app" >/dev/null 2>&1 || return 1
    check_main_executable "$app" 2>/dev/null || return 1
    if [ "$what" = drop ]; then
        version="$(plist_value "$app" CFBundleShortVersionString)" || return 1
        build="$(plist_value "$app" CFBundleVersion)" || return 1
        [ "$version" = "$DROP_VERSION" ] && [ "$build" = "$DROP_BUILD" ] || return 1
    fi
    [ "$(inode_of "$app")" = "$expected" ]
}

# Every move to the app path. $1 (inode $2) moves there with the exclusive rename, and stays only if
# app_ok (with $4) then passes on what the rename put there. Whatever fails, including a copy that
# replaced $1 during the rename (safe_move 4), is moved out to $STAGE/$3, which cleanup keeps in a
# named .tester-leftover.* folder. Returns 0 placed and verified; 1 nothing moved; 2 the app path
# was occupied, nothing moved; 3 moved but failed, and nothing is left at the app path; 5 moved and
# failed, and it could not be moved out: an unverified copy is at the app path.
place_at_target() {
    local src="$1" expected="$2" reject="$3" what="${4:-}" rc=0 actual
    safe_move "$src" "$TARGET" "$expected" || rc=$?
    case "$rc" in
        0) ! app_ok "$TARGET" "$expected" "$what" || return 0 ;;
        4) ;;
        *) return "$rc" ;;
    esac
    actual="$(inode_of "$TARGET")" || return 3
    safe_move "$TARGET" "$STAGE/$reject" "$actual" && return 3
    exists "$TARGET" || return 3
    return 5
}

# The one offline acceptance of a downloaded drop, used by the install and by --check-drop (and
# so by the packager). $1 the ZIP, $2 the tag, $3 an empty scratch folder. Dies unless every check
# passes: the tag, the download cap, the central directory (paths, no symlink, entry count,
# unpacked size), extraction, the extracted tree, mode and ACL normalisation, the pinned signer,
# an executable main binary, and the version and build the tag names. The verified app is at $3/$BUNDLE_NAME and
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
    check_main_executable "$app" || exit 1
    DROP_VERSION="$(plist_value "$app" CFBundleShortVersionString)" || DROP_VERSION=""
    DROP_BUILD="$(plist_value "$app" CFBundleVersion)" || DROP_BUILD=""
    if [ "$DROP_VERSION" != "$version" ] || [ "$DROP_BUILD" != "$build" ]; then
        die "the app inside is version $DROP_VERSION, build $DROP_BUILD, not the version $version, build $build the tag names"
    fi
}

remove_scratch() {
    block_signals
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
    local zip="$1" tag="$2" scratch_base="${TMPDIR:-/tmp}"
    case "$zip" in /*) ;; *) zip="$PWD/$zip" ;; esac
    case "$scratch_base" in /*) ;; *) scratch_base="$PWD/$scratch_base" ;; esac
    if [ ! -f "$zip" ]; then
        echo "error: not a file: $zip" >&2
        return 2
    fi
    trap remove_scratch EXIT
    # The scratch folder and CHECK_SCRATCH come into being with no trap able to run in between.
    block_signals
    CHECK_SCRATCH="$(mktemp -d "$scratch_base/tester-check-drop.XXXXXX")" ||
        die "could not create a scratch folder"
    catch_signals
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
# Contract: whenever a trap can run, LOCK is set exactly when this run created the lock, so
# cleanup releases this run's lock and never another's. A signal trap runs between commands, so
# mkdir and the assignment run with signals ignored: a signal then is discarded, never acted on
# between them (which left the lock behind with LOCK still empty).
acquire_lock() {
    local lock="$1" dest rc=0
    dest="$(dirname "$lock")"
    block_signals
    mkdir "$lock" 2>/dev/null || rc=$?
    [ "$rc" -ne 0 ] || LOCK="$lock"
    catch_signals
    if [ "$rc" -ne 0 ]; then
        [ -d "$lock" ] || die "could not create $lock"
        echo "error: another tester install is running in $dest, or one was interrupted; if none is running, remove the folder $lock and run this again" >&2
        report_strays "$dest" "also beside the app:"
        exit 1
    fi
}

# Undo the swap's completed steps in reverse order, each only as far as its destination is free
# and the copy is the one this run moved. What cannot be undone stays where it is, and
# ROLLBACK_NOTE (the end of the caller's error message) says where.
rollback() {
    local rc=0
    ROLLBACK_NOTE=""
    if [ -z "$MOVED_SUPERSEDED$MOVED_ASIDE$REJECTED$STUCK" ]; then
        ROLLBACK_NOTE="; nothing was changed"
        [ -z "$AT_MOVE_IN" ] || [ -n "$OLD_INODE" ] ||
            ROLLBACK_NOTE="$ROLLBACK_NOTE; there was no previous copy to restore"
        return 0
    fi
    if [ -n "$MOVED_ASIDE" ] && [ -n "$STUCK" ]; then
        ROLLBACK_NOTE="; the previous copy is at $PREV"
    elif [ -n "$MOVED_ASIDE" ]; then
        # The previous copy goes back only if it verifies at the app path (it may have changed
        # in place since start-up, or never have verified).
        place_at_target "$PREV" "$OLD_INODE" unverified-previous || rc=$?
        case "$rc" in
            0)
                MOVED_ASIDE=""
                ROLLBACK_NOTE="; the previous copy was restored"
                ;;
            3)
                MOVED_ASIDE=""
                ROLLBACK_NOTE="; the previous copy did not pass verification, so it was not restored and is kept in a folder named below; do not open it"
                ;;
            5)
                MOVED_ASIDE=""
                ROLLBACK_NOTE="; the previous copy did not pass verification and could not be moved away again: $TARGET is an unverified build, do not open it"
                ;;
            *) ROLLBACK_NOTE="; could not restore the previous copy, check $PREV" ;;
        esac
    elif [ -z "$OLD_INODE" ]; then
        ROLLBACK_NOTE="$ROLLBACK_NOTE; there was no previous copy to restore"
    fi
    if [ -n "$MOVED_SUPERSEDED" ] && safe_move "$STAGE/superseded" "$PREV" "$STALE_INODE"; then
        MOVED_SUPERSEDED="" # otherwise cleanup keeps it and names it
    fi
}

swap_fail() {
    rollback
    die "$1$ROLLBACK_NOTE"
}

# The swap runs with signals blocked. A verified update retains the previous copy as a backup.
swap() {
    local rc=0
    block_signals
    # 1. A stale .tester-previous (start-up found the app verified) makes room for the old app.
    if [ -n "$STALE_INODE" ]; then
        safe_move "$PREV" "$STAGE/superseded" "$STALE_INODE" || swap_fail "could not clear $PREV"
        MOVED_SUPERSEDED=1
    elif exists "$PREV"; then
        swap_fail "$PREV appeared during the install"
    fi
    # 2. The installed app moves aside.
    if [ -n "$OLD_INODE" ]; then
        safe_move "$TARGET" "$PREV" "$OLD_INODE" || rc=$?
        case "$rc" in
            0) MOVED_ASIDE=1 ;;
            4) swap_fail "another program changed $TARGET while it was being moved aside; check $PREV" ;;
            *) swap_fail "could not move the installed app aside; if macOS asked about App Management, allow Terminal in System Settings > Privacy & Security > App Management, or remove the app by hand, then run this again" ;;
        esac
    fi
    # 3-4. This run's copy moves in, never into something another program put at the target, and
    # stays only if it verifies there as this run's drop.
    AT_MOVE_IN=1
    rc=0
    place_at_target "$STAGE/extract/$BUNDLE_NAME" "$OWN_INODE" rejected drop || rc=$?
    case "$rc" in
        0) INSTALLED=1 ;;
        2) swap_fail "another program changed $TARGET during the install" ;;
        3)
            REJECTED=1
            swap_fail "the copy at $TARGET failed verification after the move and was not left there"
            ;;
        5)
            STUCK=1
            swap_fail "the copy at $TARGET failed verification after the move and could not be moved aside: $TARGET is an unverified build, do not open it"
            ;;
        *) swap_fail "could not install" ;;
    esac
    catch_signals
}

# Delete the older backup only when the verified install is in place and the new backup at $PREV
# has itself verified. A replaced build that does not verify is not kept as the rollback copy: it
# moves into staging. Every copy still in staging is kept in a named .tester-leftover.* folder.
clean_stage() {
    local copy kept="" leftover backup_ok=""
    if [ -n "$STAGE" ] && [ -n "$STAGE_INODE" ] &&
        [ "$(inode_of "$STAGE")" = "$STAGE_INODE" ]; then
        rm -f "$STAGE/probe.err" "$STAGE/drop.zip" "$STAGE/drop.zip.sha256"
        if [ -d "$STAGE/extract" ]; then
            chmod -R u+rwX "$STAGE/extract" 2>/dev/null
            rm -rf "$STAGE/extract"
        fi
        if [ -n "$INSTALLED" ] && [ -n "$MOVED_ASIDE" ]; then
            if app_ok "$PREV" "$OLD_INODE"; then
                backup_ok=1
            elif [ "$(inode_of "$PREV")" = "$OLD_INODE" ]; then
                safe_move "$PREV" "$STAGE/unverified-previous" "$OLD_INODE" ||
                    echo "note: the build this install replaced, at $PREV, did not pass verification and could not be moved; do not open it" >&2
            else
                echo "note: $PREV is not the copy this install saved there; it was left as it is" >&2
            fi
        fi
        if [ -n "$backup_ok" ] && [ -n "$MOVED_SUPERSEDED" ] &&
            [ "$(inode_of "$TARGET")" = "$OWN_INODE" ] &&
            [ "$(inode_of "$STAGE/superseded")" = "$STALE_INODE" ]; then
            chmod -R u+rwX "$STAGE/superseded" 2>/dev/null
            rm -rf "$STAGE/superseded"
        fi
        for copy in superseded rejected unverified-previous; do
            ! exists "$STAGE/$copy" || kept=1
        done
        if [ -n "$kept" ]; then
            leftover="$(dirname "$STAGE")/.tester-leftover.${STAGE##*.}"
            safe_move "$STAGE" "$leftover" "$STAGE_INODE" || leftover="$STAGE"
            for copy in "$leftover"/* "$leftover"/.[!.]*; do
                exists "$copy" || continue
                case "${copy##*/}" in
                    rejected) echo "note: a copy that failed verification at $TARGET is kept at $copy; do not open it" >&2 ;;
                    unverified-previous) echo "note: a previous build that did not pass verification is kept at $copy, not as the rollback copy; do not open it" >&2 ;;
                    superseded) echo "note: an earlier copy of the app that this install moved is kept at $copy" >&2 ;;
                    *) echo "note: could not remove $copy" >&2 ;;
                esac
            done
        else
            rmdir "$STAGE" 2>/dev/null
        fi
        if [ -z "$kept" ] && exists "$STAGE"; then
            echo "note: could not remove this install's staging folder $STAGE; remove it once no install is running" >&2
        fi
    elif [ -n "$STAGE" ] && exists "$STAGE"; then
        echo "note: the staging path could not be identified and was kept at $STAGE" >&2
    fi
}

# The EXIT trap waits for cleanup's children before releasing the lock.
cleanup() {
    block_signals
    set +e
    clean_stage
    if [ -n "$LOCK" ]; then
        rmdir "$LOCK" 2>/dev/null || echo "note: could not remove $LOCK" >&2
    fi
}

# Start-up, when $PREV exists. With an app that passes app_ok, $PREV is stale and the swap clears
# it (so a run that fails before the swap leaves it where it is). Otherwise $PREV may be the last
# good copy, and it counts only if it passes app_ok itself.
# With no app: most often the person removed the app but not the hidden rollback copy, rarely a
# run stopped mid-swap. A verified $PREV is put back, so the run starts from a verified app (and if
# the run then fails, that older verified build is what stays installed); one that does not verify
# is refused and named, and nothing moves. With an app that does not verify, nothing moves.
recover_previous() {
    local rc=0 previous_inode
    if exists "$TARGET"; then
        OLD_INODE="$(inode_of "$TARGET")" || die "cannot identify the installed app"
    fi
    exists "$PREV" || return 0
    previous_inode="$(inode_of "$PREV")" || die "cannot identify the previous copy at $PREV"
    if [ -n "$OLD_INODE" ] && app_ok "$TARGET" "$OLD_INODE"; then
        STALE_INODE="$previous_inode"
        return 0
    fi
    if ! app_ok "$PREV" "$previous_inode"; then
        [ -z "$OLD_INODE" ] ||
            die "neither the installed app at $TARGET nor the previous copy at $PREV passes verification; nothing was changed. Do not open either; tell the owner what this printed"
        die "the previous copy at $PREV does not pass verification, so it was not restored; nothing was changed. Do not open it; move it to the Trash (or tell the owner what this printed), then run this again"
    fi
    [ -z "$OLD_INODE" ] ||
        die "the installed app at $TARGET could not be verified, and $PREV holds the copy from before it; nothing was changed. To go back to that copy, move $TARGET to the Trash and rename $PREV to \"$BUNDLE_NAME\"; or tell the owner what this printed"
    block_signals
    place_at_target "$PREV" "$previous_inode" unverified-previous || rc=$?
    catch_signals
    case "$rc" in
        0) OLD_INODE="$previous_inode"; echo "restored the previous copy" ;;
        2) die "another program put something at $TARGET while the previous copy was being restored; check $PREV" ;;
        3 | 5) die "the copy restored from $PREV changed and did not pass verification at $TARGET; do not open it" ;;
        *) die "could not restore the previous copy from $PREV; nothing was changed" ;;
    esac
}

install_drop() {
    local tag="$1" dest zip sum expected actual old_version="" old_build=""

    if ! [[ "$tag" =~ $TAG_PATTERN ]]; then
        die "not a tester tag: $tag"
    fi

    check_platform

    dest="${APP_INSTALL_DIR:-$HOME/Applications}"
    case "$dest" in /*) ;; *) dest="$PWD/$dest" ;; esac
    TARGET="$dest/$BUNDLE_NAME"
    PREV="$dest/.tester-previous"
    mkdir -p "$dest"
    trap cleanup EXIT
    catch_signals
    acquire_lock "$dest/.tester-install.lock"
    report_strays "$dest" "an earlier install that failed or was interrupted left"
    # As for the lock: cleanup removes only a staging folder it can name and identify, so the
    # folder, STAGE and STAGE_INODE come into being with no trap able to run in between.
    block_signals
    STAGE="$(mktemp -d "$dest/.tester-install.XXXXXX")"
    STAGE_INODE="$(inode_of "$STAGE")" || die "cannot identify the staging folder"
    catch_signals
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

    block_signals
    (set +e; clean_stage) || true
    STAGE=""
    [ "$(inode_of "$TARGET")" = "$OWN_INODE" ] ||
        die "another program changed $TARGET during cleanup; check the app and backup paths ($TARGET and $PREV)"
    catch_signals

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
