# Build build/Pipecat Service Toolbar.app (wheel + pinned uv + uv.lock pins, ad-hoc signed).
# Set SIGN_IDENTITY="Developer ID Application: …" to sign for distribution.
app-build:
    scripts/build_app.sh

# Signs with SIGN_IDENTITY, else (scripts/resolve_sign_identity.sh) the self-signed
# "Code Signing" identity when the keychain has it, so Microphone / Speech grants carry over to
# and from app-install, else ad hoc. Then scripts/quit_app.sh quits a running copy, found
# by the id the build wrote (BUNDLE_ID overrides it), and waits up to QUIT_WAIT_SECS for it.
# Build the app bundle, then launch it (quits a running copy first).
app-open:
    #!/usr/bin/env bash
    set -euo pipefail
    # Two steps: `export X="$(…)"` would hide the helper's failure from set -e.
    SIGN_IDENTITY="$(scripts/resolve_sign_identity.sh)"
    export SIGN_IDENTITY
    scripts/build_app.sh
    app="build/Pipecat Service Toolbar.app"
    id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist")"
    scripts/quit_app.sh "$id"
    open "$app"

# Signs with SIGN_IDENTITY, default the self-signed "Code Signing" identity: its designated
# requirement (bundle id + cert) is stable across rebuilds, so Microphone / Speech Recognition
# grants survive an update; the ad-hoc default of app-build is cdhash-bound and resets them on
# every rebuild. scripts/resolve_sign_identity.sh --require fails before the build when
# the keychain lacks it. Quits a running copy first (scripts/quit_app.sh, by the id the
# build wrote) and waits up to QUIT_WAIT_SECS for it.
# Build, then install into ~/Applications (APP_INSTALL_DIR overrides), replacing any installed copy, and launch it.
app-install:
    #!/usr/bin/env bash
    set -euo pipefail
    # Signing is the last build step: check the identity first, not after the whole build.
    # Two steps: `export X="$(…)"` would hide the helper's failure from set -e.
    SIGN_IDENTITY="$(scripts/resolve_sign_identity.sh --require)"
    export SIGN_IDENTITY
    scripts/build_app.sh
    name="Pipecat Service Toolbar.app"
    src="build/$name"
    dest="${APP_INSTALL_DIR:-$HOME/Applications}"
    id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$src/Contents/Info.plist")"
    # Quit is asynchronous and can be refused: wait for the process to go before touching the
    # bundle, or a surviving copy keeps running the old build (aborts, bundle untouched, if it stays up).
    scripts/quit_app.sh "$id"
    mkdir -p "$dest"
    # Stage beside the target and verify the copy before removing the installed app, so a failed
    # copy leaves the old install in place; rm before mv because ditto/mv would merge or nest.
    stage="$(mktemp -d "$dest/.install.XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    ditto "$src" "$stage/$name"
    codesign --verify --deep --strict "$stage/$name"
    rm -rf "${dest:?}/$name"
    mv "$stage/$name" "$dest/$name"
    echo "installed $dest/$name"
    codesign -dr - "$dest/$name" 2>&1 | tail -1
    open "$dest/$name"

# Run the app unbundled from the checkout (prepares the pinned server wheel; uses your uv).
app-run *args:
    swift run --package-path . PipecatServiceToolbar {{args}}

# Swift unit tests (+ a live tone-server test when .venv exists: `uv sync --locked`).
app-test:
    swift test --package-path .

# Unattended UI smoke (no Accessibility grant): --snapshot render, bundle launch, quit + SIGTERM leave no children.
app-smoke *args:
    scripts/ui_smoke.sh {{args}}

# Exit 1 unless constraints/tts.txt is exactly scripts/compile_tts_constraints.py's output for the
# source_commit in artifacts/tts.json (CHECKOUT: a pipecat-local-tts-server clone with that commit).
tts-constraints-check checkout:
    uv run --no-project python scripts/compile_tts_constraints.py --server-checkout "{{checkout}}" --check

# Exit 1 unless the files recorded from the TTS server (python_tests/fixtures/upstream/*,
# docs/supervised-contract.json) are the pinned source_commit's own (CHECKOUT: as above).
tts-fixtures-check checkout:
    uv run --no-project python scripts/check_tts_fixtures.py --server-checkout "{{checkout}}"
