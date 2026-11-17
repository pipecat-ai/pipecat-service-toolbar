# Changelog

Changes to the Pipecat Service Toolbar, the native macOS menu bar app in this repository.
Server-only changes belong to
[pipecat-local-tts-server](https://github.com/vr000m/pipecat-local-tts-server/releases) and
[pipecat-local-stt-server](https://github.com/vr000m/pipecat-local-stt-server/releases).

The app has its own version (`app-version.json`), independent of the server packages it
installs (`artifacts/tts.json`, `Service.swift`). Releases from this repository never upgrade
a server package implicitly; a changed server pin is listed as its own entry.

## Version history

Releases before this repository existed were numbered after the TTS server release they shipped
with, and are renumbered here as the app's own history. Dates are unchanged. The installed
`CFBundleShortVersionString` of those builds is the old number.

| App version | Shipped as | Date |
|---|---|---|
| 0.4.0 | 0.9.1 (build 468; this repository's first build is 474) | 2026-11-16 |
| 0.3.0 | 0.9.0 | 2026-10-31 |
| 0.2.3 | 0.8.3 | 2026-10-28 |
| 0.2.2 | 0.8.2 | 2026-10-27 |
| 0.2.1 | 0.8.1 | 2026-10-27 |
| 0.2.0 | 0.8.0 | 2026-10-27 |
| 0.1.0 | 0.7.0 (first release) | 2026-10-25 |

## [Unreleased]

### Changed

- The app moved to its own repository (`pipecat-ai/pipecat-service-toolbar`) with its Git history,
  build tools, tests and documentation. Bundle identifier, signing identity, preferences, `AppPaths`
  layout, environment fingerprints, ports and model-cache locations are unchanged: an installed
  0.9.1 (build 468) copy, now numbered 0.4.0, needs no environment rebuild or model download.
- The app version is independent of the servers (`app-version.json`; see Version history).
  It builds from the checksum-pinned TTS 0.9.1 wheel (`artifacts/tts.json`) and recorded
  constraints; STT remains at 0.6.0. The first build from this repository is 0.4.0 (474).

### Fixed

- `PIPECAT_TOOLBAR_REPO` pointing at a TTS source checkout no longer drops the hash-pinned STT
  constraints (they come from this repository's `constraints/`).

### Added

- Server pin checks (`python_tests/test_server_pins.py`): the TTS artifact, `pyproject.toml`,
  `uv.lock`, `constraints/tts.txt` (now with a provenance header) and the recorded server fixtures
  (`python_tests/fixtures/provenance.json`) must describe the same release, and the wheel's
  requirements must be satisfied by the recorded pins. `scripts/compile_tts_constraints.py`
  regenerates `constraints/tts.txt` from the server release's `uv.lock`.

## [0.4.0] - 2026-11-16

Shipped as 0.9.1 alongside [TTS server 0.9.1](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.9.1).

### Added

- Moonshine STT (port 9965, English only: model spec `medium-streaming@en`). It is not a Hugging Face model, so the app passes the spec as `--model` and shows it as the model. Deleting it removes only its Python environment; its weights stay on disk (see "Model source" in `docs/architecture.md`). The app forwards `MOONSHINE_VOICE_CACHE` to its child processes and never deletes that directory.

### Changed

- STT installs now go through `pipecat-local-stt-server` 0.6.0 instead of 0.5.0, for all engines (constraints regenerated, source excerpt re-recorded). Regenerating also re-resolved the transitive pins of the Whisper, Parakeet and Nemotron environments.
- `HF_TOKEN` is now passed only to services whose weights come from the Hugging Face cache; a service whose weights live elsewhere gets no token.
- the detail page's Delete offers Delete Everything only for models in the Hugging Face cache. Tone, Apple Speech and Moonshine show a single Delete. Before, every model with a download offered it (see "Model source" in `docs/architecture.md`).

### Fixed

- a suggested model whose Start needs a package refresh now keeps its Running-panel row, showing the install progress and Cancel, until it is starting; Cancel in that window, including between the refresh and the read after it, stops the start. Before, the category disappeared for the length of the install. A sibling variant's environment change during the read after the refresh no longer drops that Start.

## [0.3.0] - 2026-10-31

Shipped as 0.9.0 alongside [TTS server 0.9.0](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.9.0).

### Added

- running services are restored on the next launch (preference `restoreOnLaunch`, on by default; the toggle is in the panel's gear menu).
  A stale or busy port ends Failed and is not retried.
- Quit asks for confirmation when a client is connected to a server the app started.
  It also asks when the count cannot be read.
  Connections are counted from one bounded `lsof` call, excluding the app's own sockets.
  An `lsof` still running at its 2 s deadline is sent SIGKILL, and the count is unknown (asks).
  The killed `lsof` is reaped within 0.2 s, else by a background reaper.
  A launch failure, signal death or any stderr also asks. So does an exit other than 0 (or 1 with no output).
  So does a pipe held open past the deadline.
  So does any malformed or unknown `lsof` line, or a process with no readable socket.
  So does a line not ended by `\n`, or an empty line. So does a pid or fd with a sign or leading zero.
  So does a socket name that is not two whole IPv4 or bracketed IPv6 endpoints joined by one `->`.
  IPv6 may use `::`, a dotted IPv4 tail or a `%scope`.
  Endpoints are checked without a C parser: ASCII only, no leading zero in an octet or port.
  Exit 1 with no output is zero.
  System-initiated quits are not asked. That exemption rests on a provisional quit-reason code table.
  The table holds the documented Apple Event codes, not yet observed.
  A manual Log Out / Restart / Shut Down check has not run.
  Half-open peers count until keepalive reaps them: about 140 s at the TTS server's keepalive defaults.
  The STT servers' keepalive is not verified here.
- `scripts/quit_app.sh` says a quit confirmation may be waiting in the app when it exits 1.
- Reset also clears the saved set of services to restore (`restore.wanted`), so the next
  launch restores none. Quitting never changes the set; a Stop during shutdown still clears that service.
- `just app-smoke` (`scripts/ui_smoke.sh`) relaunches with tone wanted and restore on.
  It checks an External tone server is adopted and no server of the app's own is started.
  Every launch uses a throwaway `PIPECAT_TOOLBAR_DEFAULTS_SUITE` plus `-restoreOnLaunch` and
  `-restore.wanted` launch arguments.
  The run fails if a read-only hash of the real `restore.wanted` changed.
- the Running panel suggests a model for the idle category.
  It appears when the other category has an active service: starting, running, stopping, external, or failed at runtime.
  It offers the model you last started there.
  Otherwise it offers the default, then the first installed model whose cache check does not refuse it.
  It never suggests `tone`, and Reset clears the last-used choice (`lastUsed.tts`, `lastUsed.stt`).

## [0.2.3] - 2026-10-28

Shipped as 0.8.3 alongside [TTS server 0.8.3](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.3).

### Fixed

- `just app-open` waits for a running Pipecat Service Toolbar to exit before launching (the quit is
  asynchronous, so `open` used to re-activate the old copy) and stops, without launching, when the old copy
  has not quit within `QUIT_WAIT_SECS`. It now signs with the self-signed `Code Signing` identity when the
  keychain has it (ad hoc otherwise; it used to always sign ad hoc), so Microphone / Speech Recognition
  grants carry across `app-open` and `app-install`.
- Both recipes share `scripts/quit_app.sh`: one `QUIT_WAIT_SECS` deadline for the whole step,
  osascript calls included (one that ignores SIGTERM is killed). Exit 1 when the app is still running, or
  osascript gave no answer, at the deadline; exit 3 when osascript fails or answers anything but true/false,
  so whether the app runs is unknown and the recipe stops instead of reading it as "not running". No
  osascript outlives the script on any exit path.
- Both recipes choose their default identity in `scripts/resolve_sign_identity.sh`, which asks
  `scripts/has_sign_identity.sh`. That check reads only the identity lines of the keychain listing
  and takes an identity as codesign does: a full 40-hex SHA-1 (any case) or a case-insensitive substring of
  a name. It is advisory: two identities sharing a name still count as present, and codesign decides at
  signing. A failing `security` query stops the recipe instead of reading as "no identity". The old
  `app-install` check matched the listing's own `Policy: Code Signing` header, so it passed for
  `Code Signing` on every machine and a missing identity failed only at the last build step.

## [0.2.2] - 2026-10-27

Shipped as 0.8.2 alongside [TTS server 0.8.2](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.2).

### Added

- `just app-install`: build Pipecat Service Toolbar signed with the self-signed `Code Signing`
  identity (so Microphone / Speech Recognition grants survive rebuilds) and install it into
  `~/Applications`, replacing any installed copy.

## [0.2.1] - 2026-10-27

Shipped as 0.8.1 alongside [TTS server 0.8.1](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.1).

### Changed

- the External probe waits 2 s for a server's hello (was 5 s). Measured
  across every TTS backend mid-synthesis and every STT engine while its model loads, the slowest
  hello was 187 ms.

### Fixed

- an External row's LaunchAgent label is cut in the middle, not at the
  end, so `pipecat.stt-server.kyutai` / `…parakeet` / `…nemotron` stay distinguishable in the
  panel, and hovering the line shows the whole label.

## [0.2.0] - 2026-10-27

Shipped as 0.8.0 alongside [TTS server 0.8.0](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.0).

### Added

- **Adopts running servers (External).** At launch and while
  the panel is open (every 15 s) the app probes the catalog ports (loopback TCP) with
  a `server.hello` and shows a matching server it did not start as **External**;
  it starts its own only when none answers. External rows offer Try it (for STT, the
  test clip only, never the microphone) and Copy but no per-service Stop, Delete, Get
  or Install (the app-wide Reset deletes only the app's own environments and layout
  file, never signals an External server, and is not blocked by one), show the
  LaunchAgent label when `launchctl`
  confirms it holds the port (else `127.0.0.1:<port>`), and **Quit leaves them
  running** ("Stop All & Quit" appears only when the app started a server). The app
  only signals, stops or deletes processes it started. An app-started server and a
  `just tts-install` / `just stt-*` agent on one port: a matching agent is adopted;
  a holder that does not identify as that service makes Start report the port busy,
  naming it when `launchctl` confirms it. Unix sockets, LAN endpoints and
  token-protected servers are not discovered.
- **Parakeet (9765) and Nemotron (9865)** STT services, matching the
  `pipecat.stt-server.parakeet` / `nemotron` agent ports, installed against their own
  hash-pinned `constraints/stt-parakeet.txt` / `stt-nemotron.txt` (regenerate with
  `uv run python scripts/compile_stt_constraints.py`). Parakeet needs `ffmpeg` on
  the machine (Homebrew `/opt/homebrew/bin` or `/usr/local/bin`; the app adds those to
  Parakeet's server `PATH` only); without it the server exits with `BackendStartError:
  FFmpeg is not installed or not in your PATH`.
- `scripts/ui_smoke.sh` starts a tone server outside the app on 8665, checks the
  bootstrap log reports it as External and that it is still running after Quit
  (skipped when 8665 is held, a `pipecat.tts-server.tone` LaunchAgent is loaded, or the
  repo `.venv` cannot import `tts_server`).

### Changed

- STT now runs through the upstream
  `pipecat-local-stt-server==0.5.0` CLI (`python -m stt_server serve --supervised`,
  `prefetch --json`); the bundled `pipecat_app_helper.py`, `PIPECAT_TOOLBAR_HELPER`
  and the helper's cooperative cancel are removed, with the helper's `MODEL_REVISIONS`
  pins and `ServiceCatalog.sttModelRevisions` (0.1.0 listed both). An STT server on an
  older version is adopted by its hello, but an app-started STT server needs 0.5.0.
- STT model weights are pinned by upstream, not by the app: `pipecat-local-stt-server`
  0.5.0 pins the four default models (Whisper large-v3-turbo, Kyutai 1B, Parakeet,
  Nemotron) to a recorded Hub commit in code (`prefetch --json` reports it as
  `revision`). It has no revision flag, so the app passes none; any other `--model`
  (the other Whisper sizes, Kyutai 2.6B) follows the repo's `main`.
- `docs/supervised-contract.json` lists error codes per emitter (contract `version` 2;
  version 1 had one flat list); `ModelInvalidError` is emitted by the STT server only (`tts_server/supervise.py` is unchanged).

## [0.1.0] - 2026-10-25

Shipped as 0.7.0 alongside [TTS server 0.7.0](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.7.0).

### Added

- **Pipecat Service Toolbar**: native SwiftUI menu bar app that installs
  and serves local speech services on fixed loopback ports. TTS: every backend here
  (bundled pinned `uv` + this wheel + `uv.lock` constraints; a managed CPython 3.12 and
  one venv per service). STT: Apple Speech (SpeechAnalyzer behind a native WebSocket
  server), Whisper and Kyutai (`moshi_mlx`), all on the `pipecat-local-stt-server` wire
  protocol. Running-only main menu, an Add Models catalog, copy URL / Pipecat service /
  env from a right-click menu, model deletion, and "Try it" for both directions.
  Quitting stops the install or download step in flight (and the bootstrap's wheel
  build), lets no later step start, and waits up to 20 s for installs and removals to
  unwind; deleting a service with "Delete Everything" removes its model weights even
  when its environment is outdated, and reports any it could not delete. An abandoned
  Kyutai transcription releases the backend (cancelled per 80 ms frame). Cancel
  always ends an install (SIGKILL 5 s after SIGTERM) and never counts as a finished
  one; deleting a service mid-install cancels the install first; model deletion only
  touches `models--*` folders in the Hugging Face hub cache and the server's own
  derived-weights folder; a startup error is never lost to the server's exit; the
  TTS "Try it" request lifecycle is one pure state machine (`TTSRequestMachine`,
  tested over every state x event cell, random interleavings against a model server
  and a live tone server): requests go out one at a time on a reused connection,
  each starting with `input_text.clear`; Stop before the commit sends no commit and
  Stop after it sends `response.cancel` behind it; a frame acts only on the request
  its `response_id` / `previous_event_id` names; each request reports its outcome
  once; a request the server goes silent on times out and reconnects. It shows
  server error messages and ends cleanly when the output device changes. A cancelled queued install records
  nothing; an install step no longer waits on a grandchild holding its pipes.
  STT environments install against checked-in, hash-pinned constraints
  (`constraints/stt-<engine>.txt`, `--require-hashes`) and load model weights
  pinned to a commit, which `prefetch` downloads and `serve-stt` loads; neither pin
  marks an existing environment outdated. The supervised/prefetch JSON lines both
  emitters write (this server and the STT helper) are fixed in
  `docs/supervised-contract.json` and drift-tested from both sides.
  `HF_TOKEN` reaches only `prefetch` and `serve`, never `uv`. The Apple Speech
  server caps clients at 8 and decodes one at a time, and survives Stop with a
  decode in flight; microphone audio is resampled without per-buffer seams.
  Model deletion refuses a path whose backend directory is a symlink out of the
  cache; stopping a subprocess also ends its descendants (process group) and never
  hangs on a failed or refused launch; a server exit reported after a removal no
  longer marks the service installed; quit cancels an Apple Speech model install;
  the Apple Speech server closes a client that stops reading its replies.
  `just app-build` / `app-open` / `app-run` / `app-test`. Right-click a running
  service for **Test Voice** (speaks "Hello world, this is the Pipecat test server
  using the <model> model.") or **Test Transcription** (a bundled spoken clip, no
  microphone, checked against its sentence); the STT Try it "Test Clip" button uses
  the same clip instead of a live system-voice synthesis.

### Changed

- "Try it" clears the request buffer (`input_text.clear`) before every request and sends a
  cancel behind the commit a Stop interrupts, so a refused request's text can no longer prefix the next one and Stop between
  append and commit no longer leaves a synthesis holding the connection.
- Internals (no change to behaviour, ports or on-disk layout):
  the per-service lifecycle state machine moved from the app target into
  `PipecatToolbarCore` as a pure reducer (`ServiceLifecycle.reduce`) plus an effect
  runner (`ServiceDriver`) with injectable environment, process, port, Apple Speech
  and clock seams; `ServiceModel` is now a thin `@Observable` adapter and the Swift
  tests depend on Core only. Add Models' download size now counts every snapshot an
  install fetches at its pinned revision (Phonon's pin, dia's codec, pocket_tts's
  voice embeddings, the STT helper's file filter at its `MODEL_REVISIONS` pin, which
  `ServiceCatalog.sttModelRevisions` repeats) from `ServiceCatalog.snapshots(for:)`,
  which a lean drift test compares with each backend's `prefetch_specs(model)` and
  with the helper's pins.

### Fixed

- a late refresh (Reset, Try Again, a sibling's install, a
  slow cache probe) no longer hides a running server or undoes a Delete; Cancel during
  the busy-port check works and a late answer is dropped; an obsolete server's `error`
  line no longer judges the next server; an Apple Speech download already under way
  is attached to (progress and Cancel work); a fresh install cancelled during setup
  reads as not installed; Delete Everything also removes partially downloaded repos
  and reports any folder it refused; a bundled app missing an STT engine's hash pins
  refuses that install instead of resolving unpinned; choosing "Default voice" in
  Try it is remembered; stale audio no longer replays after an output-device change;
  `just app-open` quits the bundle id the build used.
- an environment from before variants keeps its builder
  installed when a sibling variant installs or is cancelled; a variant record that
  can't be written or removed now fails the install or removal instead of reporting
  success; Reset is refused while a service is running, installing or removing;
  Stop and quit stop a busy-port probe (`lsof`) in flight; quit always stops the
  environment manager's subprocesses (a cache probe included) and waits for a
  bootstrap or Reset in flight; Try Again and Reset no longer start a second
  bootstrap or Reset while one runs.
- Delete Everything on one variant keeps (and reports) the
  model folders when a sibling's cache check gives no answer, such as one quit stopped,
  so quitting while Phonon 8-bit is removed no longer deletes the voices Phonon still
  uses; a late bootstrap refresh after quit no longer attaches an Apple Speech install,
  and the download-size lookup no longer starts after quit; Stop cancels a busy-port
  probe at once instead of after its re-read; Get, Start and Delete wait while Reset
  or a bootstrap runs (a Start during Reset no longer fails with "missing the STT
  helper"); a stop after a process was reaped never signals a reused pid's group.
