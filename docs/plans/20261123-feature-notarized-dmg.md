# Task: Build a notarized DMG that can be shared

**Status**: In Progress (Phases 1-3 implemented; Phase 4 validation open)
**Component**: distribution
**Assigned to**: Filipi Fuchter
**Priority**: Medium
**Branch**: `feature/notarized-dmg` (PR #5); planned on `docs/notarized-dmg-plan`
**Created**: 2026-11-23
**Review Gates**: full

## Objective

Produce `Pipecat-Service-Toolbar-<version>-b<build>.dmg`: a signed, notarized and stapled disk image holding a signed,
notarized and stapled app, laid out for drag-to-Applications. A recipient downloads it with any browser, opens
it, drags the app to Applications and launches it with no Gatekeeper prompt beyond the normal
"downloaded from the internet" confirmation. Nobody needs Xcode, uv, Python, just or a terminal.

Any team member can build it on their own Mac: after a one-time setup from the team's password manager (the
Developer ID `.p12` and the notarization API key), one command builds, signs and notarizes it: `just app-dmg`. There is no
macOS CI machine, so builds are local by design. The DMG is a manual sharing channel (Slack, Drive, a GitHub
release asset). It does not install, update or remove anything on the recipient's Mac.

## Context

Verified against `main` at `1cfe741` on 2026-11-23:

- `scripts/build_app.sh` already signs inside-out with the hardened runtime (`bin/uv`, then the bundle with
  `scripts/PipecatServiceToolbar.entitlements`, whose only entitlement is `com.apple.security.device.audio-input`).
  It passes `--timestamp=none`, which notarization rejects. Its header already names
  `SIGN_IDENTITY="Developer ID Application: …"` as the distribution path.
- No DMG, notarization or `notarytool` tooling exists. Signing identities on the owner's Mac today: none
  (`security find-identity -v -p codesigning` lists no `Code Signing` and no `Developer ID Application`).
- `docs/plans/20261117-feature-app-distribution.md` (accepted, not implemented) plans a notarized **ZIP** built in a
  protected GitHub Actions workflow, a `curl` installer and a later Homebrew cask. It proposes
  `scripts/lib/release_identity.sh` for the Team ID and a secure-timestamp release mode in `build_app.sh`
  (its Phase 2). Its CI parts are out of scope here: the team has no macOS CI machine.
- `docs/plans/20261121-feature-selfsigned-tester-channel.md` ships a self-signed ZIP and an installer that pins the
  self-signed certificate. It retires once an Apple-signed channel exists (its Phase 6).
- The app spawns `uv` and uv-managed CPython as separate processes. Python and models live under
  `~/Library/Application Support/Pipecat Service Toolbar` and are never part of the bundle, so they are not
  notarized and are not loaded into the app's process (no library-validation exception needed). This is the
  assumption Phase 4 proves on a clean guest.
- Notarization reference used for the certificate steps: daily-demos/daily-launcher `notarize.md` (Developer ID
  Application certificate from a CSR, export with private key, `security find-identity`, `codesign -dvvv`,
  `spctl` checks). That guide targets electron-builder; this plan uses `codesign`, `hdiutil`, `xcrun notarytool`
  and `xcrun stapler` directly.

## Decisions

- **Format: DMG, alongside (not instead of) the planned ZIP.** The DMG is for people who share a download link.
  The app-distribution plan's ZIP stays the input to its installer and the Homebrew cask. Both carry the same
  kind of Developer-ID-signed, stapled app, so this work is a building block for that plan, not a fork of it.
- **Shared pieces land once.** The release signing mode in `build_app.sh`, the identity resolver and the
  signature check are written here so the app-distribution plan's Phase 2 reuses them. One deviation from that
  plan: the Team ID is not a committed constant (its proposed `scripts/lib/release_identity.sh`); it comes from
  the git-ignored `.env` (see Credentials). The release owner named in that plan reviews this plan before implementation.
- **Local, any maintainer.** Signing and notarization run on the maintainer's own Mac. The shared secrets live in
  the team's password manager and reach the build only through the login keychain (the imported identity and a `notarytool`
  keychain profile); the scripts never read a `.p12`, `.p8` or password from disk or the environment. Nothing in
  the packager is specific to one person's Mac: the identity is found by Team ID, the profile by name.
- **Notarize twice, staple both.** Notarize the app (as a `ditto` ZIP) and staple it, then build the DMG
  around the stapled app, sign the DMG, notarize it and staple it. A stapled app keeps passing Gatekeeper
  offline after the user copies it out of the DMG; a stapled DMG passes offline when opened.
- **Version is read, never written.** `app-version.json` and `CHANGELOG.md` are untouched by `app-dmg`, as for
  tester drops. A DMG for a real release is built from the commit that bumped the version. File name:
  `Pipecat-Service-Toolbar-<version>-b<build>.dmg` (no spaces, so links and shells need no quoting); a build from a commit other than the version-bump commit
  appends `-<shortsha>`.
- **Clean tree required.** `app-dmg` refuses a dirty tree, so every DMG maps to one commit, recorded in
  `<dmg>.manifest.json` with the SHA-256, version, build, Team ID and notarization submission IDs.
- **Output** goes to `dist/dmg/` (already ignored through `dist/`).

## Requirements

### Credentials

**Public/private split.** This repository is public. Vault names, key IDs, the Issuer ID, certificate
fingerprints, submission IDs and any other team-specific instructions live in a git-ignored maintainer note,
`.private/signing-setup.md` (`.private/` and `.local_certificates/` are in `.gitignore`), shared with
maintainers out of band. Committed docs and scripts use placeholders (`<TEAMID>`, `<KEYID>`, `<ISSUER_ID>`)
and refer to that note. The values the scripts need (`APPLE_TEAM_ID`, optional `SIGN_IDENTITY`,
`NOTARY_PROFILE`, and `NOTARY_KEY_ID`/`NOTARY_ISSUER_ID` for the one-time notary setup) go in a git-ignored
`.env` copied from the committed `.env.example`. `scripts/lib/release_env.sh` parses it without executing it,
takes only those keys, and lets the environment win. Only release builds read it.

Two shared secrets, both kept in the team's password manager, with access limited to maintainers who ship builds:

| Secret | Contents | Used for |
|---|---|---|
| Developer ID signing | Password-protected `.p12` (Developer ID Application certificate **and** private key) and its password | Signing the app and DMG |
| Notarization API key | App Store Connect team API key: `AuthKey_<KEYID>.p8`, Key ID, Issuer ID | Uploading to Apple's notary service |

The `.cer` alone cannot sign. The API key is a team key (role Developer is enough), created by the Account
Holder or an Admin in App Store Connect → Users and Access → Integrations; Apple allows the `.p8` to be
downloaded once, so it goes straight into the password manager. Reuse an existing team key rather than creating
another. The Apple team to sign with is to be confirmed with the app-distribution plan's release owner before
Phase 1 merges.

Each maintainer who builds a DMG, once per Mac (exact item names and IDs: `.private/signing-setup.md`):

1. Download the `.p12` and its password into the git-ignored `.local_certificates/`.
2. Import it: `security import <file>.p12 -k ~/Library/Keychains/login.keychain-db -T /usr/bin/codesign`
   (prompts for the `.p12` password). Answer **Always Allow** if a later build asks for key access.
3. Verify: `security find-identity -v -p codesigning` lists `"Developer ID Application: <Team> (<TEAMID>)"`.
   If it is missing, the `.p12` has no private key or the Developer ID G2 intermediate is missing
   (apple.com/certificateauthority).
4. Download the `.p8` and copy the Key ID and Issuer ID, then store them in the keychain:
   `xcrun notarytool store-credentials pipecat-notary --key AuthKey_<KEYID>.p8 --key-id <KEYID> --issuer <ISSUER_ID>`.
   The command validates the key against Apple before saving. The profile name defaults to `pipecat-notary`;
   `NOTARY_PROFILE` overrides it.
5. Delete the downloaded `.p12` and `.p8`; the keychain now holds both.
6. Check the setup: `just app-dmg-doctor` reports the identity, its expiry and the notary profile
   (`xcrun notarytool history --keychain-profile pipecat-notary`) without building anything.

A personal Apple ID with an app-specific password (`--apple-id <id> --team-id <TEAMID>`) also works for
`store-credentials`, but it is tied to one person; keep it out of the shared vault and prefer the team key.

Renewal or a new certificate (Account Holder only): create a CSR in Keychain Access, issue a *Developer ID
Application* (G2) certificate at developer.apple.com, export certificate and key together as a new `.p12` and
replace the password-manager item. Never revoke a certificate that signed shipped builds.

### Build behaviour

- `build_app.sh` gains `SIGN_MODE=release`: secure timestamps (`--timestamp`), hardened runtime, and a hard
  failure unless the identity is a valid `Developer ID Application` identity of `APPLE_TEAM_ID` (from `.env`).
  The default mode keeps `--timestamp=none` so local and ad-hoc builds stay fast and offline.
- Release mode refuses a bundle whose signature carries `com.apple.security.get-task-allow`, and checks
  `codesign --verify --deep --strict` and `codesign -dvvv` (Authority chain ends in Developer ID, TeamIdentifier
  matches).
- `BUNDLE_ID` overrides are refused in release mode: the bundle ID stays `ai.pipecat.ServiceToolbar`.

### Compatibility

- Bundle ID, AppPaths, preference keys, cache layout and environment fingerprints are unchanged.
- **Signer change:** installs signed with the self-signed `Code Signing` identity or ad hoc have a different
  designated requirement. Replacing one with the Developer ID build resets Microphone and Speech Recognition
  grants once, and the tester installer (which pins the self-signed certificate) will refuse to update over it.
  Document both in `docs/migration.md`; do not change the tester installer in this plan.
- Opening the DMG never touches existing app data, model caches or running services. A user replacing a running
  copy must quit it first (Finder will say the app is in use); the docs say so.

## Implementation Checklist

### Phase 1: Release signing mode

- [x] `.env.example` (committed, placeholders) and `.env` (git-ignored); `scripts/lib/release_env.sh` parses it
      without executing it.
- [x] `scripts/resolve_release_identity.sh <team>`: the single valid Developer ID Application identity of the
      team, printed as its SHA-1; `SIGN_IDENTITY` (full hash or exact name) narrows; zero or several refuse.
- [x] `scripts/verify_release_signature.sh <app> <team>`: bundle and nested `uv` checked for Developer ID chain,
      Team ID, hardened runtime, secure timestamp, no `get-task-allow`, bundle ID.
- [x] `SIGN_MODE=release` in `build_app.sh`, all refusals before the build; the default `dev` path passes the
      same `codesign` arguments as before.
- [x] `python_tests/test_release_signing.py` (stubs only; added to CI's macOS Bash 3.2 step).
- [x] `docs/development.md` "Release signing" and Build settings rows; `docs/architecture.md` Packaging.
- [x] A real `SIGN_MODE=release just app-build` on a maintainer's Mac passes `verify_release_signature.sh`
      (2026-11-23).

### Phase 2: DMG packager

- [x] Add `scripts/package_dmg.sh`, invoked by `just app-dmg`. Steps, each failing closed:
  1. Refuse a dirty tree; read version/build from `app-version.json`; resolve the Developer ID identity
     (through `resolve_release_identity.sh` with `APPLE_TEAM_ID` from `.env`; refuse zero or several, naming
     the setup step); check the notary profile
     exists before building, so a missing setup fails in seconds, not after the build.
  2. `SIGN_MODE=release scripts/build_app.sh`.
  3. `ditto -c -k --keepParent <app> <scratch>/app.zip`;
     `xcrun notarytool submit app.zip --keychain-profile $NOTARY_PROFILE --wait --output-format json`;
     on any status other than `Accepted`, fetch `notarytool log <id>` into `dist/dmg/` and stop.
  4. `xcrun stapler staple <app>` and `xcrun stapler validate <app>`.
  5. Stage a folder with the app and an `Applications` symlink; `hdiutil create -volname "Pipecat Service Toolbar"
     -srcfolder <stage> -fs APFS -format ULFO -ov <scratch>/<name>.dmg`.
  6. `codesign --sign <identity> --timestamp <dmg>`; notarize the DMG as in step 3; `stapler staple` and
     `stapler validate` it.
  7. Verify: `spctl -a -vvv -t exec <app>` reports `source=Notarized Developer ID`;
     `spctl -a -vvv -t open --context context:primary-signature <dmg>` accepts; mount the DMG read-only and
     re-run the app check on the mounted copy.
  8. Write `<name>.dmg.sha256` and `<name>.dmg.manifest.json`, then move all three from scratch into `dist/dmg/`
     (manifest last). Scratch is removed on every exit.
- [x] Tests stub the Apple tools on `PATH` (no override variables); `DMG_APP_BUNDLE` packages a prebuilt bundle, a test-mode marker in the manifest
      shows a DMG built with fakes, and the docs say never to share one.
- [x] `DMG_SKIP_NOTARIZE=1` builds a signed but un-notarized rehearsal DMG named `…-UNNOTARIZED.dmg`, for checking
      the layout without spending a notarization round trip. Never shared.
- [x] Add `just app-dmg-doctor`: read-only setup check (identity present and valid, Team ID, days to expiry, notary
      profile works). Each failure names the setup step that fixes it.
- [x] Tests with fake tools: happy path; notarization `Invalid` (log saved, no DMG published); `In Progress`
      timeout; stapler failure; dirty tree; missing, duplicate or wrong-team identity; missing notary profile;
      doctor output; output names and manifest fields.

### Phase 3: Documentation

- [x] `docs/development.md`: new section **"Notarized DMG"**: the per-Mac credential setup (Requirements above:
      download the `.p12` from the password manager, import, verify, `notarytool store-credentials`, delete the
      downloads), with placeholders only and a pointer to the private maintainer note for names and IDs,
      the Account Holder renewal note, `just app-dmg`, outputs, the verification
      commands (`codesign -dvvv --entitlements - <app>`, `spctl -a -vvv`, `stapler validate`), troubleshooting
      (reading `notarytool log`, the common rejections: missing timestamp, unsigned nested binary, `get-task-allow`)
      and the release checklist (Phase 4). Add `SIGN_MODE`, `NOTARY_PROFILE` and `DMG_SKIP_NOTARIZE` to
      **Build settings**.
- [x] `docs/install.md` (new, user-facing): requirements (Apple silicon, macOS 15+, Apple Speech macOS 26+),
      download, open the DMG, drag to Applications, first launch, quitting a running copy before replacing it,
      the one-time permission prompt after switching from a tester or developer build, and how to check the
      download's SHA-256.
- [x] `README.md` Quickstart: lead with "Download the DMG" linking `docs/install.md`; keep build-from-source below,
      and add one line under it: "Maintainers building a shareable DMG: follow
      [Notarized DMG](docs/development.md#notarized-dmg); the credentials are in the team's password manager".
- [x] `docs/architecture.md` **Packaging**: release signing mode and the DMG step.
- [x] `docs/migration.md`: the signer change and its effect on permissions and the tester installer.
- [x] `docs/README.md`: index rows for `install.md` and this plan. `AGENTS.md`: one line pointing at
      `just app-dmg` and the development.md section, stating that a DMG never touches `app-version.json` or
      `CHANGELOG.md`.
- [x] `.claude/skills/notarize-setup/SKILL.md`: an interactive skill that walks a new maintainer through the
      per-Mac setup (identity, `.env`, notary profile, cleanup, doctor, optional real run) and keeps
      team-specific values out of tracked files.
- [x] `test_docs.py` keeps every new local link valid.

### Phase 4: Validation (before the first share)

- [ ] **Second-maintainer test:** a team member other than the author, on their own Mac, follows only the docs
      the private maintainer note and the two password-manager items, runs `just app-dmg-doctor` and `just app-dmg` from `main`, and gets a
      notarized DMG. Every place they got stuck becomes a doc fix. Record the manifest in `## Findings`.
- [ ] On a clean macOS 15 and macOS 26 UTM guest snapshot, **download the DMG through a browser** (so it is
      quarantined; never strip `com.apple.quarantine`), open it, drag the app to `~/Applications` and
      `/Applications`, launch. Expect only the standard "downloaded from the internet" dialog.
- [ ] With the guest offline after download, the app still launches (stapled ticket).
- [ ] Install Kokoro and Whisper and run Try it, proving uv and uv-managed CPython run under the notarized,
      hardened app. Grant Microphone; relaunch; the grant persists.
- [ ] Replace an existing self-signed install: data and models are kept; permissions prompt once.
- [ ] Record `spctl`, `stapler validate` and `codesign -dvvv` output in `## Findings`.

### Phase 5: Follow-ups (separate plans)

- Optional DMG window styling (background image, icon positions); deliberately left out to keep the packager
  dependency-free.
- Publishing the DMG as a GitHub release asset next to the app-distribution plan's ZIP.

## Files

| File | Change |
|---|---|
| `scripts/build_app.sh` | `SIGN_MODE=release` |
| `.env.example`, `.gitignore` | New: release settings template; `.env`, `.private/`, `.local_certificates/` ignored |
| `scripts/lib/release_env.sh` | New: `.env` parser |
| `scripts/resolve_release_identity.sh`, `scripts/verify_release_signature.sh` | New: identity resolver, signature check |
| `scripts/package_dmg.sh` | New: packager |
| `justfile` | `app-dmg` recipe |
| `python_tests/test_release_signing.py`, `python_tests/test_package_dmg.py` | New: release signing and packager tests (CI macOS Bash 3.2 step) |
| `.claude/skills/notarize-setup/SKILL.md` | New: guided per-Mac setup for maintainers |
| `docs/install.md` | New: user install guide |
| `docs/development.md`, `docs/architecture.md`, `docs/migration.md`, `docs/README.md`, `README.md`, `AGENTS.md` | As listed in Phase 3 |

## Acceptance Criteria

- A second team member, starting from the docs, the private maintainer note and the two password-manager items only, builds a notarized DMG on
  their own Mac.
- `just app-dmg` on a clean `main` produces a DMG, checksum and manifest in `dist/dmg/`, and nothing else changes
  in the tree.
- On clean macOS 15 and 26 guests, a browser-downloaded DMG opens and the app launches with no Gatekeeper block,
  including offline; `spctl` reports `Notarized Developer ID` for both the app and the DMG.
- Speech services install and run from the notarized app.
- `uv run ruff format .`, `uv run ruff check .`, `uv run pytest`, `swift test` and `just app-build` pass; the
  default (non-release) build is unchanged.

## Progress

- 2026-11-23: plan drafted.
- 2026-11-23: Developer ID identity imported on the owner's Mac and test-signed (hardened runtime, secure
  timestamp, `codesign --verify --strict` OK).
- 2026-11-23: notary profile `pipecat-notary` (team API key, role Developer) stored and validated. A test-signed
  throwaway binary was notarized end to end: `Accepted`, "Ready for distribution", no issues; `spctl` reports
  `source=Notarized Developer ID`. IDs are in the private maintainer note.
- 2026-11-23: team-specific details moved out of this public plan into the git-ignored `.private/signing-setup.md`.
- 2026-11-23: Phase 1 implemented on `feature/notarized-dmg`; Team ID moved from a committed constant to `.env`.
- 2026-11-23: a real `SIGN_MODE=release` build of the app (bundle with nested `uv`, audio-input entitlement) was
  notarized: `Accepted`, "Ready for distribution", no issues.
- 2026-11-23: Phases 2 and 3 implemented: `scripts/package_dmg.sh` (`just app-dmg`, `just app-dmg-doctor`),
  `python_tests/test_package_dmg.py`, `docs/install.md`, development/migration/README/AGENTS updates.
- 2026-11-23: `notarize-setup` skill added. Used it to set up one Mac (identity, `.env`, notary profile),
  then ran `scripts/package_dmg.sh` for real (app 0.4.1, build 474). Both submissions (app and DMG) were
  accepted and stapled; a quarantined copy of the DMG passed `spctl` as `Notarized Developer ID`.

## Findings

- First-time setup on a Mac that had never held the identity hit two problems, now in the development.md
  Troubleshooting list and the skill: the imported identity showed as not valid until Apple's Developer ID G2
  intermediate certificate was installed, and `codesign` failed with `errSecInternalComponent` from a
  non-interactive shell until the key was approved from a normal terminal.
- `just app-dmg-doctor` first reported only "no valid Developer ID Application identity... import the .p12"
  for the G2 case above, which was misleading after the import. `resolve_release_identity.sh` now asks
  `security find-identity` again without `-v` and, when this team's identity is listed there but not valid,
  says the G2 intermediate is missing (or the certificate expired). The doctor and the release build both use it.
- The DMG's own `spctl` check (`-t open --context context:primary-signature`, on a copy marked as downloaded)
  reported `source=Notarized Developer ID`. `stapler validate` passed on the DMG's app. The
  `codesign -dvvv` output and the clean-guest checks in Phase 4 are still to do.

## Issues & Solutions

## Final Results
