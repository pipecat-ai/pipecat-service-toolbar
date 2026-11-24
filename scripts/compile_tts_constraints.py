#!/usr/bin/env python3
"""Regenerate ``server-pins/tts/constraints.txt``: the pins the app installs the TTS server wheel with.

    uv run --no-project python scripts/compile_tts_constraints.py --server-checkout <path>

The pins are the server release's own ``uv.lock`` (``uv export --all-extras``), read from the
commit recorded as ``source_commit`` in ``server-pins/tts/wheel.json`` (``git archive``, so the
checkout's working tree and current branch are irrelevant). Update ``server-pins/tts/wheel.json``
first, then run this. The output starts with a provenance header that
``python_tests/test_server_pins.py`` checks against the artifact, so pins from one release
cannot sit next to another release's wheel. The header's ``content-sha256`` is the SHA-256 of
every other byte of the file in order (the title, ``# server:`` and ``# source-commit:`` lines
and the pins; only the ``content-sha256`` line itself is left out), so a hand edit to any of
them fails that test until this script regenerates the file. ``scripts/prepare_tools.py``
copies the file into the app unchanged, and refuses to unless the header passes the same check
(``verify_header``), since app builds do not run the tests. The digest is unkeyed: it catches accidental edits, not a
deliberate forgery. Provenance, that the pins are the pinned commit's own lock, is this check
(CI runs it against ``source_repository``):

    just tts-constraints-check <path>   # exit 1 if the recorded file is not this script's output

``--check`` compares bytes. The output is ``uv export``'s format, so it depends on the uv version:
use the one ``.github/workflows/test.yml`` pins for CI.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shlex
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


def verify_header(data: bytes, spec: dict) -> None:
    """Raise ``ValueError`` unless ``data`` (``server-pins/tts/constraints.txt``'s bytes) carries ``render``'s
    header for ``spec``'s release: the ``# server:`` and ``# source-commit:`` lines name the pinned
    version and ``source_commit``, and ``content-sha256`` matches every other line. Bytes, as
    ``scripts/prepare_tools.py`` ships them (it calls this before copying the file into the app),
    so a CRLF copy fails too. An accidental-edit and half-finished-pin-move check, not provenance:
    that is ``--check``."""
    lines = data.splitlines(keepends=True)
    head = [line.decode(errors="replace").rstrip("\n") for line in lines[:4]]
    expected = PROVENANCE.format(version=spec["version"], commit=spec["source_commit"])
    if len(head) < 4 or head[1:3] != expected.splitlines():
        raise ValueError(
            f"server-pins/tts/constraints.txt header {head[1:3]} does not name the pinned"
            f" pipecat-local-tts-server {spec['version']} at {spec['source_commit']} from"
            " server-pins/tts/wheel.json: regenerate it with scripts/compile_tts_constraints.py"
        )
    digest = hashlib.sha256(b"".join(lines[:3] + lines[4:])).hexdigest()
    if head[3] != DIGEST_LINE.format(digest=digest).rstrip("\n"):
        raise ValueError(
            "server-pins/tts/constraints.txt content-sha256 does not match its other lines (edited after"
            " scripts/compile_tts_constraints.py wrote it): regenerate it, do not edit it by hand"
        )


def run(args: list[str], cwd: Path | None = None) -> bytes:
    """``args``' stdout. On failure, its stderr is passed through (CI shows why git or uv failed)
    before exiting non-zero. ``scripts/check_tts_fixtures.py`` reads the server's files through
    this too, so git errors are handled here once."""
    try:
        return subprocess.run(args, cwd=cwd, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as error:
        sys.stderr.write(error.stderr.decode(errors="replace"))
        raise SystemExit(f"{shlex.join(args)} failed (exit {error.returncode})") from None


def require_full_commit(commit: str) -> str:
    """``commit``, if it is a full commit id (it reaches git, so nothing option-like)."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit("server-pins/tts/wheel.json needs a full 40-character source_commit")
    return commit


def load_artifact(root: Path = ROOT) -> dict:
    """``server-pins/tts/wheel.json``, its ``source_commit`` checked (``require_full_commit``). Both TTS
    provenance checks (this script and ``scripts/check_tts_fixtures.py``) read the pin here."""
    spec = json.loads((root / "server-pins/tts/wheel.json").read_text())
    require_full_commit(spec["source_commit"])
    return spec


def compile_constraints(checkout: Path, spec: dict, uv: str) -> str:
    commit = require_full_commit(spec["source_commit"])
    archive = run(["git", "-C", str(checkout), "archive", commit, "pyproject.toml", "uv.lock"])
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
        run(
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
        )
        body = out.read_text()
    # Drop uv's own header (it records the temporary output path); keep the pins and "# via".
    lines = body.splitlines(keepends=True)
    first = next(i for i, line in enumerate(lines) if not line.startswith("#"))
    return render(spec["version"], commit, "".join(lines[first:]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "server-pins/tts/constraints.txt")
    parser.add_argument("--check", action="store_true", help="exit 1 if --output would change")
    args = parser.parse_args(argv)
    spec = load_artifact()
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv not found on PATH")
    text = compile_constraints(args.server_checkout, spec, uv)
    if args.check:
        # Bytes, not text: prepare_tools.py ships the file byte for byte, so a CRLF or re-encoded
        # copy is not the script's output either.
        if args.output.read_bytes() == text.encode():
            return 0
        print(
            f"{args.output} is not this script's output for {spec['source_commit']}",
            file=sys.stderr,
        )
        return 1
    args.output.write_bytes(text.encode())
    print(args.output, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
