# Group server pins by service

**Status**: Complete
**Component**: macos
**Assigned to**: repository maintainers
**Priority**: Low
**Branch**: `refactor/server-pins-layout`
**Created**: 2026-11-23

## Context and objective

The toolbar bundles one TTS wheel for all TTS extras. It verifies the wheel against a download manifest and checks the matching dependency pins against the upstream lockfile. STT installs wheels from PyPI using hash-pinned dependencies per engine. Put these inputs under one root and keep the two install mechanisms.

## Requirements

- Move the TTS manifest and constraints to `server-pins/tts/` as `wheel.json` and `constraints.txt`. Move the five engine files into `server-pins/stt/constraints/`, retaining their names for the existing bundle and override consumers.
- Preserve all seven files byte for byte, along with dependency versions, resource paths, checkout override precedence, environment fingerprints and the existing missing-pin behavior.

## Implementation checklist

### Repository layout and consumers

- Update the pin generators, wheel preparation, build script, CI provenance check, Swift checkout discovery, tests and documentation to the new paths.
- Apply the same checkout classification to root discovery, STT directory selection and TTS wheel preparation. An older toolbar checkout uses its own `constraints/` directory and runs its own `scripts/prepare_tools.py`. Keep the existing STT fallback for an external TTS checkout.

## Technical specifications

- `scripts/compile_tts_constraints.py` reads the TTS manifest and writes the lockfile export; `scripts/prepare_tools.py` verifies both before creating the wheel staging directory. `.github/workflows/test.yml` reads the same manifest to select the upstream commit. `scripts/compile_stt_constraints.py` writes the engine files and `scripts/build_app.sh` copies them into the existing resource directory.
- `ToolLocator.swift` distinguishes toolbar checkouts from upstream TTS checkouts using the manifest. Select STT pins from the selected toolbar checkout, the discovered toolbar checkout, or the external checkout fallback in that order. Bundle and explicit tool overrides continue to use `stt-<engine>.txt`.
- Update the Python provenance paths and Swift test fixtures with the moves. Update the current pin references in the maintenance docs and append a path note to the distribution plan that uses the old location.

## Testing notes

- Compare the seven moved files against the base Git blobs. Run Python formatting, lint and tests, Swift tests and an app build. Compare the bundled pins against the source files.
- Use a checkout containing the manifest's `source_commit` for `just tts-constraints-check` and `just tts-fixtures-check`. CI pins uv `0.13.8` for reproducible lockfile exports.
- Test selecting an older checkout alongside a current checkout. Exercise wheel preparation dispatch and the lookup of an actual STT pins file from the older checkout. Exercise the STT generator's default output path with a stub resolver in a temporary tree and check that it writes all five files.

## Acceptance criteria

- Each source reader and writer reaches the moved file. Legacy checkout detection and bundled and override resource paths still work.
- Pin bytes, hash checks and environment fingerprints match the base.

## Issues and solutions

The sandbox blocked test loopback sockets, some macOS test subprocesses and the default Swift module cache. The native runs passed with the normal tooling and loopback listeners.

## Final results

- All seven files match the base Git blobs. The STT generator wrote all five pins to the new directory in a temporary checkout with a stub resolver.
- Both upstream TTS checks passed with local uv `0.13.9` against the pinned commit. The generated TTS constraints matched the checked-in bytes.
- Swift reported 520 tests across 54 suites. The older-checkout override selected its own STT pins, and the existing-environment test confirmed that the app would reuse installed environments.
- Python reported 806 passed and 3 skipped. Ruff formatting and lint passed.
- The ad-hoc bundle passed strict signature verification. Its wheel matched the manifest hash, and all six bundled constraints files matched their checked-in sources.

## 2026-11-24 override compatibility correction

An explicit override could still point at the checkout's `constraints/tts.txt` or `constraints/` after the move. `ToolLocator` now redirects only those exact missing paths when their checkout has the current wheel manifest and the replacement exists. [Development overrides](../architecture.md#development-overrides)

The regression test exercises `ToolLocator.locate` through both environment variables. It verifies the pins resolved for Tone and Whisper, and checks that existing paths, unrelated checkout paths and missing replacements keep their original behavior.

The corrected branch passed the full Swift suite (521 tests) and 39 focused Python tests. The rebuilt bundle passed strict signature verification. Its wheel hash and all six constraints files matched their source pins.

## 2026-11-24 per-engine override correction

A legacy STT directory can survive the move if it contains local files. The override now checks for each engine's pin in that directory before trying the relocated pin in the same checkout. Legacy matching follows filesystem traversal and casing rules. [Development overrides](../architecture.md#development-overrides)

## 2026-11-24 install-time lookup decision

Resolve the pin at each package install. Give an existing explicit pin its original Foundation path semantics. Try the relocated pin only when the original pin is missing at a recognized legacy path in that checkout. With no STT override, read the selected checkout's current layout. Keep the original override URL or default checkout in `Tools` so file changes and symlink retargets can take effect between installs.

Exercise checkout changes in both directions with the same `Tools` instance. Use a fake `uv` to verify the constraint filename, working directory and STT hash flag. Cover relative overrides whose original path and raw symlink traversal would reach different checkouts.

The expanded fixture reported 17 failed assertions on the previous resolver. All 11 override tests passed with the install-time lookup, including both repository-built and explicit wheel runs.

The fresh reviewers found two ways a previously valid spelling can lose its pin after the move. TTS must use the lexical working directory used by its installer. STT can traverse past a removed legacy `constraints/..` pair after checking the checkout marker and confirming the directory entry is absent.

The follow-up fixtures reported 11 failed assertions on the earlier resolver. All 11 override tests passed after the correction, including traversal of repeated removed legacy components and an existing directory symlink.

Both spellings with parent traversal use the same STT resolver. If it recovers a directory after skipping a removed legacy component, probe that directory for the explicit engine pin before checking for a relocated pin. Split POSIX paths on literal slashes, including when the next component starts with a combining mark.

The final filesystem review found that a symlink followed by `..` can send the STT file probe and `uv` to different checkouts. Keep an explicit pin in the directory `uv` uses when either checkout moves its pins. When a missing legacy directory also removes the descendant in a terminal `..` spelling, check that its surviving parent resolves to the same checkout before relocating the pin. The regression fixture exercises the package install in both checkout orders and after removal of the terminal traversal directory.

When both checkout manifests exist, default STT lookup prefers the current engine pin and then checks the legacy pin. The same install fixture verifies both cases and checks the hash flag. It also verifies that an explicit pin in `uv`'s working directory wins even when the physical path cannot qualify for relocation.

When both linked checkouts move their pins, the physical fallback can still pick a different checkout from `uv`. Use the working directory that Foundation gives `uv` for all relocation decisions. This supersedes physical traversal for STT relocation. The install fixture covers both checkouts moving, including an override ending in a symlink followed by `..`.
