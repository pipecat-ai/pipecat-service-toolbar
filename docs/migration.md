# Repository migration

The toolbar is extracted from
[pipecat-local-tts-server at 78823ee](https://github.com/vr000m/pipecat-local-tts-server/tree/78823eeabf1f36c464243af1bd41b879d058c028/macos)
(the original directory is `macos/`). The filtered `macos/` history is imported
at this repository's root and merged with its initial commit. Original source
copyright is retained in LICENSE; no published source history was rewritten.

## Existing installations

Install from this checkout with the same signing identity. The app remains
`ai.pipecat.ServiceToolbar` with the same application name. Layout version 1,
Application Support/Caches/Logs paths, managed Python, per-family environments,
variant markers, preferences, model ports and external-server ownership stay unchanged.
Hugging Face cache overrides (`HF_HUB_CACHE`, `HF_HOME`, `XDG_CACHE_HOME`), the
Phonon derived cache and `MOONSHINE_VOICE_CACHE` keep their existing semantics.
Do not reset, move or delete caches for this migration.

The TTS wheel remains `pipecat_local_tts_server-0.9.1-py3-none-any.whl` and STT
requirements remain 0.6.0. Their fingerprints still identify existing environments
as ready. This guarantee covers the shipped baseline: app version 0.4.0, build 468, which
shipped as 0.9.1 under the old numbering (its bundle still reports 0.9.1), bundling TTS 0.9.1 and
STT 0.6.0. An environment built by an earlier release, such
as a Nemotron environment installed under STT 0.5.0, retains the existing package-refresh
behaviour: it refreshes when next started. That is the app's refresh working as designed, not a
server fault. The app continues to use cached models during such normal upgrades.

## Versions

The app's version and build live only in `app-version.json`, and its release notes in
`CHANGELOG.md`; server versions live in `artifacts/tts.json` and `Service.swift`. They are
unrelated numbers. Builds made before this repository existed were numbered after the TTS server
release; the changelog renumbers them 0.1.0-0.4.0 (mapping table there), while the installed
bundles keep reporting the old number. The build number only increases (installed builds reached
468; the first build here is 474), so an installed copy never sees a lower `CFBundleVersion`.
An app release never changes a server pin unless its changelog entry says so.

## Build and test ownership

| Original source coverage | Owner after extraction |
|---|---|
| Swift package and tests | This repository, including live tone-server tests against the pinned package |
| Catalog drift and Swift registry/model assertions | `python_tests/test_macos_catalog_drift.py` against the installed pinned package |
| App recipes, signing, quit and UI-smoke shell tests | `python_tests/test_app_recipes.py` |
| Swift supervised decoder, STT constraints, wire rate and recorded STT fixtures | `python_tests/test_supervised_contract.py` and Swift tests |
| TTS emitter, registry, launchd and request lifecycle assertions | Remain in the TTS repository |
| Smoke environment parity with ToolLocator | `python_tests/test_supervised_smoke_helpers.py`; server smoke validation remains upstream |

The [test ownership inventory](test-ownership.json) maps every original test in
the split modules to its retained owner.

Port and README fixtures in `python_tests/fixtures/upstream/` record the source
baseline above. The supervised schema and STT excerpts also come from that
baseline. Update provenance and compatibility checks deliberately with server
pins; there is no implicit dependency on a sibling checkout or upstream main.
`python_tests/fixtures/migration-baseline.json` pins only the bytes that define an installed
app's identity and layout: `Info.plist`, entitlements, the signing-identity scripts, `AppPaths`,
`PreferencesDomain` and the two preference stores. Changing one is an identity change for every
installed copy; review it as one, then update its digest. Everything else (server pins, STT
constraints, ordinary Swift changes) moves freely: environment fingerprints are protected
behaviourally by `RepositoryMigrationTests`. Swift pre-extraction environment fixtures cover
all 21 Python-backed catalog entries, including old and current variant layouts.

## Cutover

Validate and merge the destination import PR first. Then merge the linked source
removal PR; that PR removes app-owned code/CI and retains the server contracts.
Neither step publishes a release or replaces an installed application. Keep the
source branch available until both PRs are validated. Before cutover, rollback is
closing the PRs; after cutover, a source removal can be reverted without touching
user installations or model caches.

## Moving a server pin

A pin is not one file. Change them together; `python_tests/test_server_pins.py` fails when they
disagree. A recorded file is guarded where its source is reachable: CI compares the TTS ones with
the pinned commit. The STT ones, whose source no CI job has, are guarded only by their digests,
the release the excerpt's name and header record, and the opt-in `PIPECAT_STT_SERVER_SITE` check
(see the README).

**TTS** (`pipecat-local-tts-server`):

1. Update `artifacts/tts.json`: `version`, `filename`, `url`, `sha256` (from PyPI) and the release
   tag's full `source_commit`.
2. `uv run --no-project python scripts/compile_tts_constraints.py --server-checkout <path>`
   regenerates `constraints/tts.txt` from that commit's `uv.lock`; never edit the file by hand.
   Its `content-sha256` header line is the SHA-256 of every other line (the `# server:` and
   `# source-commit:` lines and the pins), so `test_server_pins.py` fails on an accidental edit
   to any of them, and `scripts/prepare_tools.py` (so `just app-build` and `swift run`) refuses
   a file whose header does not match `artifacts/tts.json` or its digest. That digest is not
   proof of origin: `just tts-constraints-check <path>` exits non-zero when the file is not the
   script's output for the pinned commit, and CI runs the same check against a clone of
   `source_repository` (the `compatibility` job, Python 3.12). The
   output depends on the uv version: generate it with the uv CI pins (`version` in
   `.github/workflows/test.yml`), and when bumping that pin run the check and regenerate the
   file if it fails.
3. Update the dependency in `pyproject.toml`, and set `[tool.uv] constraint-dependencies` to the
   new `constraints/tts.txt` versions of the packages the lean environment shares with it, and
   remove any entry `constraints/tts.txt` no longer pins (the tests list both). Then `uv lock`
   and `uv sync --locked`.
4. Re-record the files copied from the server at the new `source_commit`:
   `python_tests/fixtures/upstream/tts-README.md` (its `README.md`) and `tts-justfile` (its
   `justfile`) byte for byte, and `docs/supervised-contract.json` (its
   `docs/supervised-contract.json`; keep this repository's `_comment`, every other field must
   match). Update their `sha256` and the `tts` version/commit in
   `python_tests/fixtures/provenance.json` (the contract is under `contract`; each of these
   entries names its path in the server repository as `source`, which is what the check reads).
   `just tts-fixtures-check <path>` (run in CI, as in step 2) exits non-zero until every file
   matches (the contract as canonical JSON, so a change of a value's type counts).
5. Run `just app-test`, `uv run pytest` and `just app-build`. `RepositoryMigrationTests` then
   fails (see below).

**STT** (`pipecat-local-stt-server`):

1. Change `ServiceCatalog.sttServerRequirement` and every engine's requirement in
   `Service.swift`.
2. Regenerate `constraints/stt-*.txt` with `scripts/compile_stt_constraints.py` (network
   needed). CI cannot rerun that resolver, so their digests in `provenance.json` are what makes
   any edit deliberate (a dropped transitive pin would otherwise pass CI and fail
   `--require-hashes` at install).
3. Record `python_tests/fixtures/stt_server_<new version>_source.txt` from the release's tag (the
   tests read the excerpt named by `sttServerRequirement`, so they fail until it exists; verify it
   with `PIPECAT_STT_SERVER_SITE`, see the README) and remove the old one (`test_server_pins.py`
   fails while an excerpt for any other release is left); re-record the `stt_*` line fixtures.
4. `docs/supervised-contract.json` is shared: its STT half (`error_codes.stt`, `stt_wire_rate`)
   must match the new excerpt and line fixtures (`test_supervised_contract.py`). The contract is
   recorded from the TTS repository (step 4 above), so a change to it lands in
   `pipecat-local-tts-server` first and arrives here with a TTS pin move.
5. In `provenance.json`, replace the old excerpt's entry with the new one (an entry without its
   file fails, as does a file without its entry), update every changed `sha256` (`stt` and
   `contract`) and the `stt` version.
6. Run `just app-test`, `uv run pytest` and `just app-build`; `RepositoryMigrationTests` then
   fails (see below).

**Installed environments.** An intentional pin change changes the environment fingerprint (TTS:
the wheel filename in `artifacts/tts.json`; STT: each engine's requirements, which carry
`sttServerRequirement`), so installed environments refresh their packages on next start (models
stay cached). `RepositoryMigrationTests` fails with "Existing environment would refresh" for
every affected service: that is the signal. Its fixture
(`Tests/PipecatServiceToolbarTests/Fixtures/pre-extraction-environments.json`) is a record of
what is installed today (the shipped 0.9.1/0.6.0 baseline), and every row must be ready under
the current pins. Change it only when that refresh is intended: replace the affected rows with
the fingerprints the new build writes (never add rows beside the old ones, which would then
fail) and say in the changelog that existing environments will refresh.
