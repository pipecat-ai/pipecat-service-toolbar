# Pipecat Service Toolbar

Native SwiftUI menu bar app for local TTS/STT. Quickstart: README.md; documentation index: docs/README.md; usage: docs/usage.md;
build and tests: docs/development.md; architecture: docs/architecture.md;
extraction provenance and compatibility: docs/migration.md.

- Work on feature branches. Keep focused commits, stage explicit paths, and use regular merges.
- `uv sync --locked` creates the lean Python environment. Never add model extras to CI's base environment.
- Run `uv run ruff format .`, `uv run ruff check .`, `uv run pytest`, `swift test`, and `just app-build` before a PR.
- Python tests are in `python_tests/`; Swift tests are in `Tests/`. Do not merge these directories.
- Tester drops (self-signed interim channel): `just app-tester-package`; guide `docs/tester-install.md`,
  checklist in `docs/development.md` "Tester drops". A tester drop never touches app-version.json or CHANGELOG.md.
- Notarized DMG: `just app-dmg` (setup check `just app-dmg-doctor`); guide `docs/development.md` "Notarized DMG".
  It never touches app-version.json or CHANGELOG.md. This repo is public: team IDs, key IDs and vault names
  live only in the git-ignored `.env` and `.private/`; commit placeholders (`.env.example`) only.
- Keep bundle identifier `ai.pipecat.ServiceToolbar`, signing identity continuity,
  preference keys, AppPaths layout, cache precedence, and environment fingerprints compatible.
- App version/build: app-version.json (only there; notes in CHANGELOG.md, which is the app's, not the
  servers'). Server artifact: server-pins/tts/wheel.json. Unrelated numbers. Build numbers only increase.
  App releases must not implicitly upgrade server packages or force model downloads.
- Moving a server pin: follow "Moving a server pin" in docs/migration.md; test_server_pins.py checks it,
  and CI also runs `just tts-constraints-check <checkout>` and `just tts-fixtures-check <checkout>`
  against the pinned server commit (run both locally after a pin move or a CI uv bump).
- Source overrides are explicit; normal builds must work with no sibling repository.
- Check catalog and supervised-protocol compatibility when changing server pins.
- Tests use temporary app state and preferences. Never reset a real installation,
  remove shared model caches, or replace the installed app as a validation shortcut.
- Retain historical plans and inherited changelog entries as decision/release records.
- Scan staged changes for credentials and private data before committing.
