# Task: Install the app with a Homebrew cask

**Status**: Not Started
**Component**: distribution
**Assigned to**: Varun Singh
**Priority**: Medium
**Branch**: planned on `docs/homebrew-cask-plan`
**Created**: 2026-11-23
**Review Gates**: none

## Objective

`brew install --cask pipecat-ai/tap/pipecat-service-toolbar` installs the notarized DMG that a maintainer
published as a GitHub release, and `brew upgrade` moves to the next release. This is the first cask: it
consumes the DMG from [the notarized DMG plan](20261123-feature-notarized-dmg.md) and leaves the lock,
quit-refusal and installer-compatibility design of Phase 8 in
[the app-distribution plan](20261117-feature-app-distribution.md) for later.

## Context

- v0.4.2 (build 475) is a stable GitHub release with two assets, `Pipecat-Service-Toolbar-0.4.2-b475.dmg` and
  its `.sha256`. The manifest is not published because it lists notary submission IDs. The cask takes its
  checksum from the `.sha256` file.
- The DMG name carries the build number (`-b475`), so the URL cannot be built from the version alone. The cask
  uses `version "0.4.2,475"` and builds the URL from both parts.
- The app is Apple silicon only, macOS 15 or later, bundle identifier `ai.pipecat.ServiceToolbar`, a menu bar
  app with no Dock icon. It does not update itself.
- No tap exists. Checked on 2026-11-23: `pipecat-ai/homebrew-tap` returns 404, and Homebrew's main cask
  repository has no Pipecat entry. The 404 can also mean a private repository, so the owner confirms.
- The app-distribution plan fixed the cask token as `pipecat-service-toolbar`, because its planned installer
  detects Homebrew ownership by that token. It also proposed a project-owned tap first and the main cask
  repository later, as separate work.
- Homebrew's default app directory, `/Applications`, needs admin rights. The app-distribution plan wants
  `~/Applications` and the `--appdir=~/Applications` flag (which `brew install --help` lists). Whether Homebrew
  keeps a custom `--appdir` across upgrade, reinstall and uninstall is unverified.

## Decisions

- **Minimal cask first.** Pinned URL and checksum, `depends_on arch: :arm64`, `depends_on macos: ">= :sequoia"`,
  the `app` stanza for the app inside the DMG, `name`, `desc`, `homepage`, a `livecheck` block, and
  `uninstall quit: "ai.pipecat.ServiceToolbar"`. `auto_updates` stays unset.
- **Not covered here, still open in Phase 8 of the app-distribution plan:** a shared install lock, refusing an
  upgrade while the app is running, interoperability with the curl installer, the ZIP artifact, the generated
  cask and `inputs.json`, the protected release workflow, and Gate C. Until a lock exists, the documentation
  tells users to quit the app before `brew upgrade`.
- **The cask lives in the tap repository**, not in this one. This repository gets the install instructions
  after a real install has worked.
- **Each release needs a manual tap PR** that bumps the version and checksum. Automating it belongs to the
  release workflow plan.
- **Cask hygiene:** `brew audit --cask --strict --online` and `brew style` must pass on the Homebrew version
  used for testing, and that version is recorded.
- **`zap`:** the app keeps environments and model caches that cost several GB to rebuild. Decide before the
  tap PR whether `zap` removes them (default: it removes preferences and logs only, not models or
  environments).

## Open questions for the owner

1. Who creates `pipecat-ai/homebrew-tap`? It needs organization admin access.
2. Is a project-owned tap enough, or does the owner want a submission to Homebrew's main cask repository later?
3. Does the cask require `--appdir=~/Applications`, or only recommend it? Default: recommend.

## Implementation Checklist

### Phase 1: Prerequisites

- [ ] The tap repository exists (question 1), with `Casks/` and a README that gives the full install command.
- [ ] Record the Homebrew version used for every test below.
- [ ] Probe `--appdir`: install with `--appdir=~/Applications`, then reinstall and uninstall with and without
      the flag, and record whether Homebrew keeps it. If not, document `HOMEBREW_CASK_OPTS`.

### Phase 2: Write and test the cask in a throwaway local tap

- [ ] Draft `Casks/pipecat-service-toolbar.rb` against the v0.4.2 release (version, URL, checksum from the
      published `.sha256`).
- [ ] `brew audit --cask --strict --online` and `brew style` pass.
- [ ] `brew livecheck` reports the current release as `0.4.2,475`.
- [ ] Install on a Mac that does not hold the build certificate: the app lands in the chosen directory, launches
      without a Gatekeeper dialog beyond the standard one, and `codesign` and `spctl` checks pass. Record
      pass or fail only.
- [ ] Reinstall and uninstall leave models, environments and settings in place; `zap` removes only what the
      decision above says.
- [ ] Upgrade: needs a second release. Until then, record that upgrade is untested. Test it at the next
      release before announcing Homebrew support.

### Phase 3: Publish the tap

- [ ] Open the tap PR from the tested cask. Get the owner's approval before merging it.
- [ ] After the merge, install from the public tap on a clean macOS 15 and macOS 26 guest.

### Phase 4: Documentation

- [ ] Add the Homebrew command to `docs/install.md` and the README only after Phase 3 passes. Mark it pending
      until then.
- [ ] Add the per-release tap bump to the release steps in `docs/development.md` ("Notarized DMG").
- [ ] Update the app-distribution plan's Phase 8 to say what this plan delivered and what is left.

### Phase 5: Follow-ups (separate plans)

- Automated tap bump from the release workflow.
- Lock, quit refusal and installer interoperability (Phase 8 of the app-distribution plan).
- Submission to Homebrew's main cask repository.

## Files

| File | Change |
|---|---|
| `docs/plans/20261123-feature-homebrew-cask.md` | New: this plan |
| `docs/plans/20261117-feature-app-distribution.md`, `docs/plans/20261123-feature-notarized-dmg.md` | Pointers to this plan |
| `docs/README.md` | Index entry |
| `docs/install.md`, `README.md`, `docs/development.md` | Phase 4, after the tap works |
| tap repository `Casks/pipecat-service-toolbar.rb` | New: the cask (outside this repository) |

## Acceptance Criteria

- A user on Apple silicon with macOS 15 or later runs the documented command and gets the notarized app.
- Uninstall leaves models, environments and settings alone.
- `brew audit --cask --strict --online` and `brew style` pass, with the Homebrew version recorded.
- The checksum in the cask equals the published `.sha256` of the release asset.
- No team ID, key ID, issuer ID, certificate hash or notary submission ID appears in the cask, the tap or this
  repository.

## Progress

- 2026-11-23: plan drafted after v0.4.2 was published. Scope chosen by the owner: a minimal DMG cask first.

## Findings

- 2026-11-23: no tap or Pipecat cask found (see Context).

## Issues & Solutions

## Final Results
