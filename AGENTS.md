# Pipecat Service Toolbar

Native SwiftUI menu bar app for local TTS/STT. Human docs: README.md; architecture:
docs/architecture.md; extraction provenance and compatibility: docs/migration.md.

- Work on feature branches. Keep focused commits, stage explicit paths, and use regular merges.
- `uv sync --locked` creates the lean Python environment. Never add model extras to CI's base environment.
- Run `uv run ruff format .`, `uv run ruff check .`, `uv run pytest`, `swift test`, and `just app-build` before a PR.
- Python tests are in `python_tests/`; Swift tests are in `Tests/`. Do not merge these directories.
- Keep bundle identifier `ai.pipecat.ServiceToolbar`, signing identity continuity,
  preference keys, AppPaths layout, cache precedence, and environment fingerprints compatible.
- App version/build: app-version.json (only there; notes in CHANGELOG.md, which is the app's, not the
  servers'). Server artifact: artifacts/tts.json. Unrelated numbers. Build numbers only increase.
  App releases must not implicitly upgrade server packages or force model downloads.
- Moving a server pin: follow "Moving a server pin" in docs/migration.md; test_server_pins.py checks it.
- Source overrides are explicit; normal builds must work with no sibling repository.
- Check catalog and supervised-protocol compatibility when changing server pins.
- Tests use temporary app state and preferences. Never reset a real installation,
  remove shared model caches, or replace the installed app as a validation shortcut.
- Retain historical plans and inherited changelog entries as decision/release records.
- Scan staged changes for credentials and private data before committing.
