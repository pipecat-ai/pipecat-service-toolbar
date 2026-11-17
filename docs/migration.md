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
as ready. This guarantee covers the shipped baseline (app 0.9.1, build 468, bundling TTS 0.9.1
and STT 0.6.0; the changelog numbers it 0.4.0). An environment built by an earlier release, such
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
disagree, and the recorded server fixtures cannot go stale silently.

**TTS** (`pipecat-local-tts-server`):

1. Update `artifacts/tts.json`: `version`, `filename`, `url`, `sha256` (from PyPI) and the release
   tag's full `source_commit`.
2. Update the dependency in `pyproject.toml`, then `uv lock` and `uv sync --locked`.
3. `uv run --no-project python scripts/compile_tts_constraints.py --server-checkout <path>`
   regenerates `constraints/tts.txt` from that commit's `uv.lock`.
4. Re-record `python_tests/fixtures/upstream/*` and `docs/supervised-contract.json` against the new
   release (`tts_server/supervise.py`, justfile ports, README), then update their digests and the
   `tts` version/commit in `python_tests/fixtures/provenance.json`.
5. Run `just app-test`, `uv run pytest` and `just app-build`.

**STT** (`pipecat-local-stt-server`): change `ServiceCatalog.sttServerRequirement` and every
engine's requirement in `Service.swift`, run `scripts/compile_stt_constraints.py`, re-record
`python_tests/fixtures/stt_server_<version>_source.txt` and the `stt_*` line fixtures from the
release (verify with `PIPECAT_STT_SERVER_SITE`, see the README), then update their digests and
the `stt` version in `provenance.json`.

An intentional pin change changes the environment fingerprint, so installed environments refresh
their packages on next start (models stay cached). `RepositoryMigrationTests` records the shipped
0.9.1/0.6.0 baseline; when a new baseline ships, record its fingerprints beside the old ones and
say in the changelog that existing environments will refresh.
