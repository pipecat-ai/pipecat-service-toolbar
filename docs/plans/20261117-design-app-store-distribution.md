# Design note: Mac App Store distribution

**Status**: Not planned (decision record)
**Component**: distribution
**Assigned to**: Varun Singh / Claude
**Priority**: Low
**Branch**: none
**Created**: 2026-11-17
**Completed**: n/a
**Review Gates**: none

## Decision

Do not pursue the Mac App Store for the current app. Ship through Homebrew ([app distribution plan](20261117-feature-app-distribution.md)) and a notarized GitHub release. Revisit only if a reduced edition is wanted (see Options).

## Why it does not fit

The first four rows are facts about this repository. The last two are my reading of Apple's rules and have not been checked against the current App Review Guidelines or sandbox documentation; verify before relying on them.

| Area | This app | App Store requirement |
|------|----------|-----------------------|
| Sandbox | Deliberately unsandboxed (`scripts/PipecatServiceToolbar.entitlements`): it launches `uv` and Python interpreters it installs | The App Sandbox is required |
| Files | `AppPaths` layout under `~/Library/Application Support/Pipecat Service Toolbar/`; a shared Hugging Face cache outside the app (`HF_HOME`, `XDG_CACHE_HOME`, `~/.cache`) | Data lives in the app container; files outside it need user-granted access |
| Process inspection | `PortProbe.owner` and external discovery use `lsof` and `launchctl print` to recognise servers started by other tools | Sandboxed apps cannot inspect other processes this way |
| Identity | One bundle id, one signing identity per install; installed environments and preferences carry over (`AGENTS.md` compatibility invariants) | A store build is signed with an Apple Distribution identity and gets a container, so it does not see an existing install's state, and two copies of one bundle id conflict |
| Code | Downloads Python packages and model weights at runtime and runs them | Guidelines restrict apps that download and run code that changes features (to verify) |
| Network | Listens on loopback ports for the STT server it hosts | Needs the network client and server entitlements (to verify) |

Two invariants break outright: the AppPaths layout and cache precedence, and "an installed copy keeps its environments". Fixing either means a different app, not a packaging change.

## Options if the store is ever wanted

1. **Do nothing** (current decision).
2. **A reduced edition**: Apple Speech only, plus connecting to servers the user started elsewhere. No managed Python environments, no model downloads, no process discovery. Separate target, separate bundle id, separate listing; it shares the Swift core where possible. Real work, and a smaller product.
3. **Move the runtime out of the app**: a signed helper installed outside the store (for example by Homebrew) that the sandboxed app talks to over loopback. The store app then only connects. This is option 2 with a helper; it needs the same new bundle id.

## What would reopen this

A concrete user need for store distribution (managed devices, discoverability) that Homebrew and the GitHub release do not meet, and an owner for a separate edition. Then: check the current sandbox entitlements and review guidelines first, and write a proper plan.

## Distribution plan update — 2026-11-17

The Homebrew plan is now [App distribution](20261117-feature-app-distribution.md).
It covers one signed, notarized release artifact consumed by a Homebrew cask and
a standalone command-line installer. The App Store decision above is unchanged.
