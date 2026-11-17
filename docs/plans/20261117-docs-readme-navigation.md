# README and documentation navigation

**Status:** Complete (local changes)
**Component:** Documentation
**Date:** 2026-11-17

## Decision

Keep the root README focused on what the toolbar does, getting it running, and
connecting Pipecat. Move detailed reference material into guides with a docs index.
The content invariant is that the model/port tables, operating caveats, signing
options and maintenance commands remain reachable from the README.

## Files to modify

- `README.md`: concise introduction, quickstart, screenshot, integration and guide links.
- `docs/README.md`: documentation index.
- `docs/usage.md`: menu controls, external servers, restore/quit, paths and settings image.
- `docs/services.md`: complete model tables and runtime requirements.
- `docs/development.md`: build/signing, tests, smoke procedures and artifact maintenance.
- `docs/architecture.md`, `docs/migration.md`, `AGENTS.md`: navigation to relocated content.

Detailed connection-counting rules already live in the architecture guide; the
usage guide links there rather than repeating the parser contract. Existing plans
were checked for references to these topics. Their original implementation decisions
remain valid, so their historical text is retained. Release history is unchanged.

## Verification

- [x] Checked 74 local links and heading fragments across 10 Markdown files.
- [x] Preserved all original table rows: 21 service/variant rows, 8 storage-path
  rows and 11 build-setting rows (including headers). Pipecat integration text is unchanged.
- [x] Full Python run: 368 passed, 3 skipped, with 3 failures and 5 setup errors
  under sandbox restrictions. Native rerun of the affected checks and supervised
  contract module: 38 passed, 3 skipped; every failed/errored case passed.
- [x] `git diff --check` passed; all nine changed/new files are documentation.

The README is 93 lines, down from 442. Swift tests and app builds were not run
for this documentation-only change; no application code or server pins changed.
