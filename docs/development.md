# Development

[Documentation](README.md) · [Architecture](architecture.md) · [Migration and server pins](migration.md)

## Build and test

From the repository root, with Xcode 26+, uv and just installed:

```bash
uv sync --locked
uv run ruff format .
uv run ruff check .
uv run pytest
swift test
just app-build
```

The Python environment is lean: it installs the pinned TTS package for compatibility
and live tone tests, without model extras. Python tests live in `python_tests/`;
Swift tests live in `Tests/` so both work on case-insensitive filesystems.
`just app-build` creates the bundle without installing or launching it.

For an installed copy, `just app-install` builds, installs into `~/Applications`
and launches the app. It uses the self-signed `Code Signing` identity by default
and fails before building if that identity is missing. `just app-open` uses the same
identity when available, falling back to ad hoc. A stable signing identity preserves
Microphone and Speech Recognition grants across rebuilds; ad-hoc signing does not.
Both recipes quit a running copy first and stop if it refuses to quit.
See [Build settings](#build-settings) for overrides.

Without just, run `scripts/build_app.sh`, then open
`build/Pipecat Service Toolbar.app`.

Other development commands:

```bash
just app-run     # run unbundled from the checkout (swift run)
just app-test    # Swift tests
PIPECAT_TOOLBAR_E2E=1 swift test --package-path . --filter EndToEnd   # real installs
swift run --package-path . PipecatServiceToolbar --snapshot /tmp/shots  # render the UI
just app-smoke   # unattended UI smoke: render, launch, quit and SIGTERM with no leftovers
```

## End-to-end tests

`AppleSpeechEndToEndTests` (in the `EndToEnd` filter) runs the debug app binary's
`--serve-apple-speech <port>` mode, the same `STTWebSocketServer` + SpeechAnalyzer the
app serves, and transcribes `say` clips through `STTClient`. It needs no permission
prompt, but it skips unless the debug binary's own speech asset is installed (assets
are reserved per client, so the bundled app's install does not count). Once:
`swift build --package-path .`, then
`.build/debug/PipecatServiceToolbar --apple-speech-selftest <16 kHz mono .wav>`
(downloads the asset if needed); `--apple-speech-status` prints `installed` after.

## UI smoke test

`just app-smoke` (`scripts/ui_smoke.sh`) needs no Accessibility or Screen Recording
grant, so it cannot open the panel or click. It launches the app with your real
support and log folders (installing nothing) and refuses while another copy runs.
Step 5 starts a tone server outside the app on 8665, checks the bootstrap
log reports it as External, and checks it is still running after Quit. Step 6 relaunches
with tone in the wanted set and restore on. It checks the log reports tone as External again.
It checks the log has no `restore: starting` and does have `restore: skipping tone: external`.
It checks no `tts_server`/`stt_server` `serve` process was started. The bootstrap's own probe
children are normal, so it does not assert zero children. Its Quit must exit clean and leave
the external server running. Steps 5
and 6 are skipped when 8665 is held, a `pipecat.tts-server.tone` LaunchAgent is loaded, or
the repo `.venv` cannot import `tts_server`. `LAUNCH_WAIT` (seconds, default 30) bounds the
wait for the bootstrap to log its end and `QUIT_WAIT` (seconds, default 20) the wait for each
quit to finish.
The smoke runs under your real bundle id, so it must not read or write your wanted set:
every launch gets `-restoreOnLaunch` and `-restore.wanted` as launch arguments and
`PIPECAT_TOOLBAR_DEFAULTS_SUITE=<throwaway suite>` (`PreferencesDomain`, which scopes only
`restore.wanted`, `restoreOnLaunch`, `lastUsed.tts` and `lastUsed.stt`). Launch arguments live in the argument domain, which
heads every `UserDefaults` search list including the suite's: the app's reads of `restore.wanted` and
`restoreOnLaunch` return the arguments (that is how step 6 seeds its wanted set) while its writes go to the
suite, so a smoke run's own wanted-set writes are not read back. The script seeds and deletes
the suite, and fails if a hash of `defaults read <bundle id> restore.wanted` differs before and
after (a read-only isolation check; the value is never printed).

### Manual UI checklist

These checks need a person (or a terminal granted Accessibility):

1. `just app-open`; the Pipecat icon appears in the menu bar; click it, the panel opens.
2. Tone > Install: progress, then Installed. Start: the row turns running and
   `lsof -nP -iTCP:8665 -sTCP:LISTEN` shows the server; Try it plays a tone.
3. STT > Apple Speech > Install (if needed) > Start: `lsof -nP -iTCP:9465 -sTCP:LISTEN`
   shows the app; Try it > Test Clip shows "Matches the clip" (no microphone needed).
   Right-click a running Tone or Apple Speech row: Test Voice plays a line, Test
   Transcription shows the same match.
4. Stop each: the port closes. Quit from the panel with Tone running and a client
   connected (a Pipecat bot, say): Quit asks first, and Cancel keeps everything running.
   Quit again and choose Quit anyway: within ~10 s `pgrep -fl "Pipecat Service Toolbar"`
   and `lsof -nP -iTCP:8665` print nothing. (With no client connected, Quit does not ask.)
5. Tone > Delete: the row returns to Install.

## Server contract checks

To check the server side of the app's contract, run `just smoke-supervised [backend]`
(or `just smoke-supervised-all`) from the **pipecat-local-tts-server checkout**.
These recipes run the same `prefetch --json --check` and
`serve --supervised` commands the app builds, with its environment, on an ephemeral
port: `ready`, a TCP synthesis, busy-port `EADDRINUSE`, and the stdin-EOF stop. Pass
`--python <service env>/bin/python` to test an environment the app installed. See
[tests/smoke/README.md](https://github.com/vr000m/pipecat-local-tts-server/blob/78823eeabf1f36c464243af1bd41b879d058c028/tests/smoke/README.md#app-path-supervised_smokepy).

## Lifecycle and catalog changes

The service lifecycle (install, start, stop, delete, and each service's part of quit)
lives in `PipecatToolbarCore` as a reducer, `ServiceLifecycle.reduce(_ state: inout
LifecycleState, _ event:) -> [LifecycleEffect]` (no I/O), plus `ServiceDriver`, which runs
its effects through injectable seams; the app's `ServiceModel` only publishes it. The
app-wide lifecycle (launch check, Try Again, Reset, quit) is `AppLifecycle.reduce` in
`AppLifecycle.swift`, with `AppModel` as its adapter. The
Swift tests depend on Core alone, so a lifecycle change is tested without the app
target (`ServiceLifecycleTests`, `ServiceDriverTests`, `AppLifecycleTests`). Download sizes come from
`ServiceCatalog.snapshots(for:)` in `Service.swift`; when a backend's
`prefetch_specs(model)` changes (a pin, a codec, voice files), update that table:
`uv run pytest python_tests/test_macos_catalog_drift.py` fails until they match.

Unbundled runs find their tools through `PIPECAT_TOOLBAR_UV`, `PIPECAT_TOOLBAR_WHEEL`,
`PIPECAT_TOOLBAR_CONSTRAINTS`, `PIPECAT_TOOLBAR_STT_CONSTRAINTS`
and `PIPECAT_TOOLBAR_REPO` (see
[Architecture](architecture.md#development-overrides)); a built app uses its bundle.

## Updating STT constraints and fixtures

Follow the complete [server-pin checklist](migration.md#moving-a-server-pin) when
changing a server release. The commands below cover STT regeneration and verification.

After changing an STT requirement in `Service.swift`, regenerate its hash-pinned
constraints (network needed; `python_tests/test_supervised_contract.py` fails until you do):

```bash
uv run python scripts/compile_stt_constraints.py   # writes constraints/stt-<engine>.txt
```

CI cannot rerun that resolver, so the files' digests are recorded in
`python_tests/fixtures/provenance.json` and `uv run pytest` fails on any edit until you update
them (`shasum -a 256 constraints/stt-*.txt`).

When `ServiceCatalog.sttServerRequirement` moves, also re-record
`python_tests/fixtures/stt_server_<version>_source.txt` from the new tag (the lean tests read the
excerpt named by the pin, so they fail until it exists and its release line names that version)
and remove the old release's excerpt and its `provenance.json` entry (the tests fail while either
is left), then check it against that release's real source;
no CI job installs the STT server, so this check is opt-in:

```bash
# a directory holding stt_server/ and pipecat_local_stt_server-<version>.dist-info
# (a site-packages, or the app's uv cache entry)
PIPECAT_STT_SERVER_SITE=<dir> uv run pytest python_tests/test_supervised_contract.py -k verbatim
```

STT model weights are pinned by upstream, not by the app (see [Pins](architecture.md#pins)): the
default models' commits live in `pipecat-local-stt-server`'s `PINNED_REVISIONS`, so moving
one means a new STT release, then bumping `ServiceCatalog.sttServerRequirement` and
regenerating the constraints. Other STT models follow `main`.

## Tester drops

Interim self-signed builds for known testers, until the Apple-signed alpha exists. Plan:
[20261121-feature-selfsigned-tester-channel.md](plans/20261121-feature-selfsigned-tester-channel.md).
`just app-tester-package` needs the self-signed `Code Signing` identity, a clean tree and a HEAD pushed
to `origin`. It refuses a tag that exists locally or on `origin`, and refuses as well when it cannot
ask `origin` (a `git ls-remote` failure other than "no such tag"). It builds with that identity,
checks the bundle against the certificate pinned in `scripts/install_tester.sh`
(`install_tester.sh --verify-only <app>`, a `codesign -R` verification), writes the ZIP in a scratch
folder and runs the installer's whole offline acceptance on it,
`install_tester.sh --check-drop <zip> <tag>`: at most 250 MiB downloaded, entries only inside the app,
unambiguous ASCII paths with explicit parent folders, no symbolic links, at most 10,000 entries and
1 GiB unpacked, then extraction, a check of what was
extracted (only folders and regular files, only the app at the top), group/other write, setuid and
access lists removed, the pinned signer, an executable main binary, and the version and build the tag
names. The install runs the same function on every download. The packager creates the checksum and
manifest in scratch before moving `<tag>.zip`, `<tag>.zip.sha256` and `<tag>.manifest.json` into
`dist/tester/`. The tag rule, the pin and the limits come from the
installer itself (`install_tester.sh --check-tag <tag>`, `install_tester.sh --print-pin`,
`--check-drop`), never from a second copy.
The tag is `tester-<version>-b<build>-<shortsha>`; the recipe refuses a tag that exists, so a second
drop needs a new commit. Tester drops never change `app-version.json` or `CHANGELOG.md`.
`TESTER_APP_BUNDLE=<app>` packages a prebuilt bundle and is for tests only (see Build settings).

The installer allows one run at a time per install directory: it holds the
`.tester-install.lock` folder there while it runs, and a run that finds one refuses and names it
(a hard-killed run leaves it for manual removal). HUP, INT, QUIT, PIPE and TERM end a run only
after its current command has finished, and are ignored during the swap and cleanup. The swap uses
macOS exclusive rename so it requires an install volume that supports it. A successful update keeps
the previous copy at `.tester-previous`. Cleanup removes the older backup after verifying the update
and identifying both the latest backup and the superseded copy. A copy that cannot be restored goes in
a named `.tester-leftover.*` folder. Every run names the `.tester-install.*` and `.tester-leftover.*`
folders it finds beside the app and never
removes them.

Release checklist:

1. Pull `main`, then run `just app-tester-package`. To recheck a ZIP by hand (no network, no install),
   run `scripts/install_tester.sh --check-drop dist/tester/<tag>.zip <tag>`. Check `on_main` in the manifest before publishing:
   send testers only a drop with `on_main: true`. The packager records `on_main` and prints a rehearsal
   note when it is `false`, but does not refuse; such a drop is a rehearsal for a guest only.
2. Publish: `gh release create <tag> --prerelease --target <manifest commit> dist/tester/<tag>*`.
   Never edit the assets afterwards; delete a bad drop and publish a new one from a new commit.
3. Verify: `gh release view <tag> --json targetCommitish` names the manifest commit, and the downloaded
   ZIP's SHA-256 equals the manifest's `zip_sha256`.
4. Fill `<INSTALLER_COMMIT_SHA>` (the manifest commit) and `<TAG>` in the one-liner from
   `docs/tester-install.md` and send it to testers directly. Never post it publicly.
5. Validate on a clean guest before the first invitation (plan, Phase 5). Never replace your own
   installed app or reset real state as a shortcut.

## Build settings

Environment variables read by `scripts/`:

| Variable | Read by | Effect |
|---|---|---|
| `SIGN_IDENTITY` | `build_app.sh`, `resolve_sign_identity.sh` | Code-signing identity (default `-`, ad hoc). Unset, `just app-open` and `just app-install` take the self-signed `Code Signing` identity from `resolve_sign_identity.sh` (which asks the keychain through `has_sign_identity.sh`): `app-open` falls back to ad hoc when it is missing, `app-install` fails before the build. An explicit value (a name, or the certificate's full SHA-1 hash) always wins. The keychain check is advisory, as codesign reads the value: a full SHA-1 in any case, or a case-insensitive substring of a name; two identities sharing a name are left to codesign at signing. Sign for distribution with `SIGN_IDENTITY="Developer ID Application: …" just app-build`. |
| `CONFIG` | `build_app.sh` | Swift build configuration, `release` (default) or `debug`. |
| `APP_INSTALL_DIR` | `just app-install`, `install_tester.sh` | Install destination (default `~/Applications`), for both the developer install and a tester drop. For the identity `just app-install` signs with, see `SIGN_IDENTITY`. |
| `TESTER_APP_BUNDLE` | `package_tester.sh` | Test only: package this prebuilt bundle instead of building one with the self-signed identity. Never for a real drop. |
| `BUNDLE_ID` | `build_app.sh` | Overrides `CFBundleIdentifier` (default `ai.pipecat.ServiceToolbar`); `just app-open` quits the id the build wrote. |
| `QUIT_WAIT_SECS` | `quit_app.sh` | Whole seconds (default `30`) that `just app-open` and `just app-install` wait for a running copy to quit before launching or replacing the bundle: one deadline for the whole step, osascript calls included. Either recipe stops, without launching or replacing, on any non-zero exit of `quit_app.sh`; its header is the contract for the timing bound and the exit codes (a missing bundle id or a value that is not whole seconds is a usage error). Not the smoke's `QUIT_WAIT` below. |
| `LAUNCH_WAIT` | `ui_smoke.sh` | Seconds (default `30`) `just app-smoke` waits for each launch's bootstrap to log its end. |
| `QUIT_WAIT` | `ui_smoke.sh` | Seconds (default `20`) `just app-smoke` waits for each quit it sends to finish. A separate knob from `QUIT_WAIT_SECS` (`quit_app.sh`, default `30`), which the smoke does not read. |
| `PIPECAT_TTS_BUILD_CACHE` | `fetch_uv.sh` | Root of the build cache for the pinned `uv` download (default `~/Library/Caches/pipecat-tts-build`). |
| `PIPECAT_UV` | `compile_stt_constraints.py` | The `uv` binary used to compile the STT constraints (default: the app's pinned uv). |

## Server artifacts and app versions

`artifacts/tts.json` pins the TTS wheel URL, filename, version and SHA-256.
`scripts/prepare_tools.py` checks its checksum and package metadata before use;
the verified download is reused from `.build/artifacts/`. A corrupt cached wheel
fails the build: remove that wheel from `.build/artifacts/` and retry.
`constraints/tts.txt` records the original TTS dependency pins, while each
`constraints/stt-*.txt` retains the existing hash-pinned STT environment.
`constraints/tts.txt` is generated by `scripts/compile_tts_constraints.py` from the pinned
server commit's `uv.lock`; do not edit it by hand. Its `content-sha256` header line lets
`uv run pytest` catch an accidental edit, `scripts/prepare_tools.py` refuses to bundle a file
whose header names another release than `artifacts/tts.json` or fails its digest, and `just tts-constraints-check <checkout>` (also run
in CI) verifies it is the script's output for `source_commit` in `artifacts/tts.json`. That
output depends on the uv version, so CI pins uv (`.github/workflows/test.yml`); bumping it needs
the check to pass, regenerating the file if it does not. `just tts-fixtures-check <checkout>`
(also run in CI) verifies the files recorded from the TTS server are that commit's own.

App releases use `app-version.json` and [CHANGELOG.md](../CHANGELOG.md), independently of the
server package versions: increment the build integer for every distributed build, and bump the
version only together with a new changelog entry. The first build from this repository is
0.4.1 (474); builds before it were numbered after the TTS server (the installed 0.9.1 builds,
468 and earlier, are 0.4.0 and earlier in the changelog). Moving a server pin is a separate
change with its own checklist: see [Moving a server pin](migration.md#moving-a-server-pin). Unbundled `swift run` uses the same pinned artifact
preparation. Explicit `PIPECAT_TOOLBAR_WHEEL` and `PIPECAT_TOOLBAR_CONSTRAINTS` overrides support
local server development; `PIPECAT_TOOLBAR_REPO` can explicitly build a TTS checkout (a path that
is no checkout fails the tool lookup rather than falling back), and its STT environments still
install against this repository's `constraints/` (override the folder with
`PIPECAT_TOOLBAR_STT_CONSTRAINTS`). Bundled apps always use their bundled tools.
