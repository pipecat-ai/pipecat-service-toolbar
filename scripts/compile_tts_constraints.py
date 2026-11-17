#!/usr/bin/env python3
"""Regenerate ``constraints/tts.txt``: the pins the app installs the TTS server wheel with.

    uv run --no-project python scripts/compile_tts_constraints.py --server-checkout <path>

The pins are the server release's own ``uv.lock`` (``uv export --all-extras``), read from the
commit recorded as ``source_commit`` in ``artifacts/tts.json`` (``git archive``, so the
checkout's working tree and current branch are irrelevant). Update ``artifacts/tts.json``
first, then run this. The output starts with a provenance header that
``python_tests/test_server_pins.py`` checks against the artifact, so pins from one release
cannot sit next to another release's wheel. The header's ``content-sha256`` is the SHA-256 of
every other byte of the file in order (the title, ``# server:`` and ``# source-commit:`` lines
and the pins; only the ``content-sha256`` line itself is left out), so a hand edit to any of
them (``scripts/prepare_tools.py`` copies the file into the app unchanged) fails that test until
this script regenerates the file. The digest is unkeyed: it catches accidental edits, not a
deliberate forgery. Provenance, that the pins are the pinned commit's own lock, is this check
(CI runs it against ``source_repository``):

    just tts-constraints-check <path>   # exit 1 if the recorded file is not this script's output
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLE = (
    "# Recorded pins for the bundled TTS server wheel. Regenerate with"
    " scripts/compile_tts_constraints.py.\n"
)
PROVENANCE = "# server: pipecat-local-tts-server=={version}\n# source-commit: {commit}\n"
DIGEST_LINE = "# content-sha256: {digest}\n"


def render(version: str, commit: str, pins: str) -> str:
    """The file: title, provenance, the digest line, then the pins. The digest is the SHA-256 of
    every other line of the file, exactly as written and in file order."""
    covered = TITLE + PROVENANCE.format(version=version, commit=commit)
    digest = hashlib.sha256((covered + pins).encode()).hexdigest()
    return covered + DIGEST_LINE.format(digest=digest) + pins


def compile_constraints(checkout: Path, spec: dict, uv: str) -> str:
    commit = spec["source_commit"]
    archive = subprocess.run(
        ["git", "-C", str(checkout), "archive", commit, "pyproject.toml", "uv.lock"],
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="tts-lock-") as tmp:
        work = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(work, filter="data")
        project = tomllib.loads((work / "pyproject.toml").read_text())["project"]
        if project["name"] != "pipecat-local-tts-server" or project["version"] != spec["version"]:
            raise SystemExit(
                f"{commit} is {project['name']} {project['version']}, not the pinned"
                f" pipecat-local-tts-server {spec['version']}"
            )
        out = work / "constraints.txt"
        subprocess.run(
            [
                uv,
                "export",
                "--frozen",
                "--all-extras",
                "--no-dev",
                "--no-hashes",
                "--no-emit-project",
                "--format",
                "requirements.txt",
                "--output-file",
                str(out),
            ],
            cwd=work,
            check=True,
            capture_output=True,
        )
        body = out.read_text()
    # Drop uv's own header (it records the temporary output path); keep the pins and "# via".
    lines = body.splitlines(keepends=True)
    first = next(i for i, line in enumerate(lines) if not line.startswith("#"))
    return render(spec["version"], commit, "".join(lines[first:]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "constraints/tts.txt")
    parser.add_argument("--check", action="store_true", help="exit 1 if --output would change")
    args = parser.parse_args()
    spec = json.loads((ROOT / "artifacts/tts.json").read_text())
    if not re.fullmatch(r"[0-9a-f]{40}", spec["source_commit"]):
        raise SystemExit("artifacts/tts.json needs a full 40-character source_commit")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv not found on PATH")
    text = compile_constraints(args.server_checkout, spec, uv)
    if args.check:
        if args.output.read_text() == text:
            return 0
        print(
            f"{args.output} is not this script's output for {spec['source_commit']}",
            file=sys.stderr,
        )
        return 1
    args.output.write_text(text)
    print(args.output, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
