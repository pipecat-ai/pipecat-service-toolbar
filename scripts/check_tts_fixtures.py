#!/usr/bin/env python3
"""Exit 1 unless the files recorded from the TTS server are that server commit's own files.

    uv run --no-project python scripts/check_tts_fixtures.py --server-checkout <path>

Each file ``python_tests/fixtures/provenance.json`` records from the TTS repository (its ``tts``
and ``contract`` sections; each entry's ``source`` is its path in that repository) is compared
with its source at the ``source_commit`` in ``server-pins/tts/wheel.json`` (``git cat-file``, so the
checkout's working tree and current branch are irrelevant). The digests in ``provenance.json``
only catch an edit after recording; this catches a version bump without re-recording. CI runs it
against a clone of ``source_repository`` (``just tts-fixtures-check <path>`` locally). Reading
the pin and running git are ``scripts/compile_tts_constraints.py``'s (``load_artifact``,
``run``), so both checks validate the commit and report git errors the same way.

``docs/supervised-contract.json`` is not a byte copy: the toolbar's ``_comment`` describes this
repository instead of the server's, so every other field is compared, as canonical JSON (sorted
keys, values as written), so a change of type alone (``16000`` to ``16000.0``, ``1`` to ``true``)
is drift too.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__:  # imported as scripts.check_tts_fixtures (the tests)
    from .compile_tts_constraints import ROOT, load_artifact, require_full_commit, run
else:  # run as a script: scripts/ is sys.path[0]
    from compile_tts_constraints import ROOT, load_artifact, require_full_commit, run

PROVENANCE = "python_tests/fixtures/provenance.json"
#: The ``provenance.json`` sections whose files are recorded from the TTS repository.
SECTIONS = ("tts", "contract")
#: recorded JSON file -> the top-level key that is the toolbar's own (left out of the comparison).
OWN_KEY = {"docs/supervised-contract.json": "_comment"}


def recorded(root: Path = ROOT) -> dict[str, str]:
    """Recorded file -> its path in the TTS repository, from ``provenance.json``."""
    provenance = json.loads((root / PROVENANCE).read_text())
    sources = {}
    for section in SECTIONS:
        for path, entry in provenance[section]["fixtures"].items():
            if not isinstance(entry.get("source"), str):
                raise SystemExit(f"{PROVENANCE}: {section} fixture {path} names no source")
            sources[path] = entry["source"]
    return sources


def _canonical(data: bytes, own_key: str) -> str:
    parsed = json.loads(data)
    parsed.pop(own_key, None)
    return json.dumps(parsed, sort_keys=True)


def drift(checkout: Path, commit: str, root: Path = ROOT) -> list[str]:
    """The recorded files that differ from their source at ``commit`` (empty: none)."""
    require_full_commit(commit)
    differ = []
    for path, source in recorded(root).items():
        ours = (root / path).read_bytes()
        theirs = run(["git", "-C", str(checkout), "cat-file", "blob", f"{commit}:{source}"])
        if path in OWN_KEY:
            same = _canonical(ours, OWN_KEY[path]) == _canonical(theirs, OWN_KEY[path])
        else:
            same = ours == theirs
        if not same:
            differ.append(f"{path} is not {source} at {commit}")
    return differ


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-checkout", type=Path, required=True)
    args = parser.parse_args(argv)
    differ = drift(args.server_checkout, load_artifact()["source_commit"])
    for line in differ:
        print(f"{line}: re-record it (docs/migration.md, Moving a server pin)", file=sys.stderr)
    return 1 if differ else 0


if __name__ == "__main__":
    raise SystemExit(main())
