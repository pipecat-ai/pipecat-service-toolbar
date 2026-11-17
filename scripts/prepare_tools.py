#!/usr/bin/env python3
"""Prepare the checksum-pinned TTS wheel and recorded constraints for the app."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlsplit

# Standard library only: build_app.sh and ToolLocator run this with plain `uv run --no-project`.
if __package__:  # imported as scripts.prepare_tools (the tests)
    from .compile_tts_constraints import verify_header
else:  # run as a script: scripts/ is sys.path[0]
    from compile_tts_constraints import verify_header

ROOT = Path(__file__).resolve().parents[1]


def verify(wheel: Path, spec: dict) -> None:
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != spec["sha256"]:
        raise ValueError("TTS wheel SHA-256 does not match artifacts/tts.json")
    with zipfile.ZipFile(wheel) as archive:
        metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Expected exactly one wheel METADATA record")
        fields = BytesParser().parsebytes(archive.read(metadata[0]))
        if fields["Name"] != "pipecat-local-tts-server" or fields["Version"] != spec["version"]:
            raise ValueError("TTS wheel package name/version does not match the pin")
        if "tts_server/__main__.py" not in archive.namelist():
            raise ValueError("TTS wheel does not contain the server CLI")


def download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)


def prepare(output: Path, *, root: Path = ROOT, fetch=download) -> Path:
    spec = json.loads((root / "artifacts/tts.json").read_text())
    filename = spec["filename"]
    if filename != f"pipecat_local_tts_server-{spec['version']}-py3-none-any.whl":
        raise ValueError("Unexpected TTS wheel filename")
    if not spec["url"].startswith("https://files.pythonhosted.org/"):
        raise ValueError("Expected a pinned PyPI artifact URL")
    if urlsplit(spec["url"]).path.rsplit("/", 1)[-1] != filename:
        raise ValueError("artifacts/tts.json url does not point at the pinned wheel filename")
    if not re.fullmatch(r"[0-9a-f]{40}", spec.get("source_commit", "")):
        raise ValueError("artifacts/tts.json needs the 40-character source_commit of the release")
    # Fail closed on pins from another release (a half-finished pin move): the header must name
    # this wheel's version and commit. The verified bytes are what the app ships.
    constraints = (root / "constraints/tts.txt").read_bytes()
    verify_header(constraints, spec)
    cache = root / ".build/artifacts"
    cache.mkdir(parents=True, exist_ok=True)
    wheel = cache / filename
    if wheel.exists():
        try:
            verify(wheel, spec)  # Fail closed on a corrupt cache; do not silently reuse it.
        except ValueError as error:
            raise ValueError(
                f"Cached wheel {wheel} failed verification ({error}). It is a build cache: "
                "delete that file to download the pinned wheel again."
            ) from error
    else:
        with tempfile.TemporaryDirectory(prefix="wheel-", dir=cache) as temporary:
            candidate = Path(temporary) / filename
            fetch(spec["url"], candidate)
            verify(candidate, spec)
            candidate.replace(wheel)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheel, output / filename)
    (output / "constraints.txt").write_bytes(constraints)
    (output / "MANIFEST").write_text(filename + "\n")
    return output / filename


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".build/tools/wheels")
    print(prepare(parser.parse_args().output))
