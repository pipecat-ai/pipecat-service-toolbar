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
as ready. This guarantee covers the shipped 0.9.1 baseline; environments from a
different server release retain the existing package-refresh behaviour. The app
continues to use cached models during such normal upgrades.

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
`python_tests/fixtures/migration-baseline.json` protects runtime, identity and
STT constraint bytes for this extraction. Future intentional changes can retire
individual pins through review. Swift pre-extraction environment fixtures cover
all 21 Python-backed catalog entries, including old and current variant layouts.

## Cutover

Validate and merge the destination import PR first. Then merge the linked source
removal PR; that PR removes app-owned code/CI and retains the server contracts.
Neither step publishes a release or replaces an installed application. Keep the
source branch available until both PRs are validated. Before cutover, rollback is
closing the PRs; after cutover, a source removal can be reverted without touching
user installations or model caches.
