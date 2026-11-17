#!/usr/bin/env python3
"""Exit 1 unless the files recorded from the TTS server are that server commit's own files.

    uv run --no-project python scripts/check_tts_fixtures.py --server-checkout <path>

Each recorded file is compared with its source at the ``source_commit`` in
``artifacts/tts.json`` (``git cat-file``, so the checkout's working tree and current branch are
irrelevant). The digests in ``python_tests/fixtures/provenance.json`` only catch an edit after
recording; this catches a version bump without re-recording. CI runs it against a clone of
``source_repository`` (``just tts-fixtures-check <path>`` locally).

``docs/supervised-contract.json`` is not a byte copy: the toolbar's ``_comment`` describes this
repository instead of the server's, so every other field is compared (as parsed JSON).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: recorded file -> its path in pipecat-local-tts-server. ``test_server_pins.py`` checks this
#: covers every file ``provenance.json`` records from the TTS repository.
UPSTREAM = {
    "python_tests/fixtures/upstream/tts-README.md": "README.md",
    "python_tests/fixtures/upstream/tts-justfile": "justfile",
    "docs/supervised-contract.json": "docs/supervised-contract.json",
}
#: recorded JSON file -> the top-level key that is the toolbar's own (left out of the comparison).
OWN_KEY = {"docs/supervised-contract.json": "_comment"}


def _blob(checkout: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(checkout), "cat-file", "blob", f"{commit}:{path}"], capture_output=True
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode(errors="replace"))
        raise SystemExit(f"git cat-file blob {commit}:{path} failed (exit {result.returncode})")
    return result.stdout


def drift(checkout: Path, commit: str, root: Path = ROOT) -> list[str]:
    """The recorded files that differ from their source at ``commit`` (empty: none)."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit("artifacts/tts.json needs a full 40-character source_commit")
    differ = []
    for recorded, source in UPSTREAM.items():
        ours, theirs = (root / recorded).read_bytes(), _blob(checkout, commit, source)
        if recorded in OWN_KEY:
            ours, theirs = json.loads(ours), json.loads(theirs)
            ours.pop(OWN_KEY[recorded], None)
            theirs.pop(OWN_KEY[recorded], None)
        if ours != theirs:
            differ.append(f"{recorded} is not {source} at {commit}")
    return differ


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-checkout", type=Path, required=True)
    args = parser.parse_args(argv)
    commit = json.loads((ROOT / "artifacts/tts.json").read_text())["source_commit"]
    differ = drift(args.server_checkout, commit)
    for line in differ:
        print(f"{line}: re-record it (docs/migration.md, Moving a server pin)", file=sys.stderr)
    return 1 if differ else 0


if __name__ == "__main__":
    raise SystemExit(main())
