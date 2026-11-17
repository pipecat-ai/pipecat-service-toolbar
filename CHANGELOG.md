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
| 0.4.1 | New numbering; first build from this repository (build 474) | 2026-11-17 |
| 0.4.0 | 0.9.1 (build 468) | 2026-11-16 |
| 0.3.0 | 0.9.0 | 2026-10-31 |
| 0.2.3 | 0.8.3 | 2026-10-28 |
| 0.2.2 | 0.8.2 | 2026-10-27 |
| 0.2.1 | 0.8.1 | 2026-10-27 |
| 0.2.0 | 0.8.0 | 2026-10-27 |
| 0.1.0 | 0.7.0 (first release) | 2026-10-25 |

## [Unreleased]

## [0.4.1] - 2026-11-17

### Changed

- The app moved to `pipecat-ai/pipecat-service-toolbar`, retaining its Git history, build tools,
  tests, and documentation. Upgrading from 0.9.1 (build 468, now numbered 0.4.0) with the same
  signing identity preserves preferences, permissions, environments, ports, and model caches;
  no environment rebuild or model download is needed. See [migration and compatibility](docs/migration.md).
- App versions are now independent of server versions. This first standalone build is 0.4.1
  (build 474); the server pins remain TTS 0.9.1 and STT 0.6.0.

### Fixed

- A TTS source override through `PIPECAT_TOOLBAR_REPO` now retains the hash-pinned STT
  dependencies. Invalid checkout paths report an error at startup; an empty value counts as unset.
- A corrupt cached TTS wheel now reports which file to delete.

### Added

- Build and CI checks verify the pinned TTS wheel, dependency constraints, and recorded server
  fixtures, and keep shared test dependencies aligned with the app's pins. See
  [Moving a server pin](docs/migration.md#moving-a-server-pin) for the checks and regeneration steps.

## [0.4.0] - 2026-11-16

Shipped as 0.9.1 alongside [TTS server 0.9.1](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.9.1).

### Added

- Moonshine STT on port 9965, using the English-only `medium-streaming@en` model. Deleting it
  removes its Python environment but keeps its weights; `MOONSHINE_VOICE_CACHE` is respected
  and never deleted. See [Model source](docs/architecture.md#model-source).

### Changed

- All STT engines now install `pipecat-local-stt-server` 0.6.0 instead of 0.5.0, with refreshed
  dependency pins for Whisper, Parakeet, and Nemotron.
- `HF_TOKEN` is passed only to services that use Hugging Face model weights.
- Delete Everything is offered only for Hugging Face models. Tone, Apple Speech, and Moonshine
  show a single Delete action.

### Fixed

- Suggested models remain visible with progress and Cancel while Start refreshes their packages.
  Cancellation stops the pending start, and a sibling variant's environment update no longer
  causes the start to be dropped.

## [0.3.0] - 2026-10-31

Shipped as 0.9.0 alongside [TTS server 0.9.0](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.9.0).

### Added

- Running services are restored on the next launch (`restoreOnLaunch`), enabled by default with
  a toggle in the panel's gear menu. A stale or busy port reports Failed without retrying;
  Reset clears the saved services, while Quit preserves them.
- Quit asks for confirmation when clients are connected to an app-started server or their count
  cannot be determined. Quit scripts warn when confirmation may be waiting in the app.
- System-initiated quits bypass confirmation, using provisional reason codes that had not yet
  been manually verified for Log Out, Restart, or Shut Down. Disconnected clients may still
  count until keepalive expires (about 140 seconds for TTS; STT timing was unverified).
  See [Restore and quit guard](docs/architecture.md#restore-and-quit-guard).
- The Running panel suggests a model for the idle category when the other category has an active
  service, preferring the last-used model, then the default or an installed model. Tone is never
  suggested, and Reset clears the last-used choice.
- Smoke checks cover service restoration and adoption of External servers using isolated
  preferences, while checking that the real restore preferences remain unchanged.

## [0.2.3] - 2026-10-28

Shipped as 0.8.3 alongside [TTS server 0.8.3](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.3).

### Fixed

- `just app-open` waits for the previous app to quit before launching the new build. Both
  `app-open` and `app-install` stop if quitting times out or the app's running state is unknown.
- `app-open` uses the self-signed `Code Signing` identity when available, preserving Microphone
  and Speech Recognition permissions across rebuilds and installs; otherwise it uses ad hoc signing.
- Signing checks in `has_sign_identity.sh` no longer mistake the keychain listing's header for
  an available identity. A keychain query failure stops the build instead of being treated as a
  missing identity.

## [0.2.2] - 2026-10-27

Shipped as 0.8.2 alongside [TTS server 0.8.2](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.2).

### Added

- `just app-install`: build Pipecat Service Toolbar signed with the self-signed `Code Signing`
  identity (so Microphone / Speech Recognition grants survive rebuilds) and install it into
  `~/Applications`, replacing any installed copy.

## [0.2.1] - 2026-10-27

Shipped as 0.8.1 alongside [TTS server 0.8.1](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.1).

### Changed

- External-server detection waits up to 2 seconds for a response, down from 5 seconds.

### Fixed

- External-server LaunchAgent labels truncate in the middle so engine names remain distinguishable.
  Hovering shows the full label.

## [0.2.0] - 2026-10-27

Shipped as 0.8.0 alongside [TTS server 0.8.0](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.8.0).

### Added

- Adopts running servers that match catalog services on their ports as External, with Try it and Copy
  actions (STT testing uses a clip, not the microphone). The app leaves these servers running
  on Quit, never stops or deletes them, and limits Reset to its own environments and layout.
- External discovery supports local TCP ports only; Unix sockets, LAN endpoints, and
  token-protected servers are not discovered. An unrecognised port holder makes Start report
  the port busy. See [External servers](docs/architecture.md#external-servers).
- Parakeet STT on port 9765 and Nemotron STT on port 9865, with hash-pinned dependencies.
  Parakeet requires `ffmpeg` installed through Homebrew in `/opt/homebrew/bin` or `/usr/local/bin`.
- Smoke checks verify that an External tone server remains running after the app quits.

### Changed

- App-started STT services now use `pipecat-local-stt-server` 0.5.0, replacing the bundled helper
  and removing the `PIPECAT_TOOLBAR_HELPER` override. Compatible older servers can still be
  adopted as External.
- The STT server now owns model-revision pins for the four default models: Whisper large-v3-turbo,
  Kyutai 1B, Parakeet, and Nemotron. Other Whisper sizes and Kyutai 2.6B follow their model
  repository's `main` revision.
- The supervised protocol contract moves to version 2, separating TTS and STT error codes;
  `ModelInvalidError` is STT-only.

## [0.1.0] - 2026-10-25

Shipped as 0.7.0 alongside [TTS server 0.7.0](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.7.0).

### Added

- Native SwiftUI menu bar app for local TTS and STT on fixed loopback ports, with a Running panel,
  Add Models catalog, model deletion, and copyable URLs, Pipecat snippets, and environment settings.
  Supports the TTS server's backends plus Apple Speech, Whisper, and Kyutai STT.
- Managed Python environments with pinned dependencies and STT model revisions. Adding these
  pins does not mark existing environments outdated; `HF_TOKEN` reaches model download and
  serving processes, never the package installer.
- Try it for speech synthesis and transcription, plus Test Voice and Test Transcription shortcuts.
  Transcription tests use a bundled spoken clip without requiring the microphone.
- Build and run commands: `just app-build`, `app-open`, `app-run`, and `app-test`.

### Changed

- TTS Try it clears previous text before each request and cancels interrupted synthesis, preventing
  rejected text from leaking into the next request. Silent servers time out and reconnect.
- Service lifecycle handling moved into a separately tested core without changing behaviour,
  ports, or storage layout. Model download sizes now include all snapshots an install fetches.

### Fixed

- Late refreshes and obsolete server events no longer hide running services, undo deletion, or
  overwrite a newer service's state. Startup errors remain visible after a server exits.
- Cancel and Quit stop pending installs, downloads, probes, and their child processes without
  reporting cancelled work as installed. Quitting waits for cleanup and prevents further work;
  stopping a finished process cannot signal an unrelated process that reused its identifier.
- Shared model variants retain their environment and weights when a sibling still needs them,
  including when its cache check cannot complete. Failed variant-record updates report failure.
- Delete Everything removes partial downloads and reports anything it cannot delete, including
  for outdated environments. Deletion stays within model-cache locations and refuses symlinks
  that lead outside the cache.
- Reset is refused while services are running, installing, or being removed. Setup and Reset
  cannot run twice concurrently, and model actions wait for them to finish.
- STT installs refuse to proceed without their bundled dependency hash pins. Existing Apple Speech
  downloads can be reattached with progress and Cancel support; Quit cancels them.
- Try it remembers Default voice and avoids stale playback after an output-device change.
  Apple Speech handles cancellation during decoding and disconnects clients that stop reading;
  abandoned Kyutai transcriptions release the backend.
- `just app-open` quits the bundle identifier used by the build.

See [architecture and lifecycle details](docs/architecture.md) for the implementation and
[the original release](https://github.com/vr000m/pipecat-local-tts-server/releases/tag/v0.7.0)
for the inherited release record.
