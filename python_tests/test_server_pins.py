"""The server pins move together, or a test fails.

A TTS release is described by several files that nothing else keeps in step:
``artifacts/tts.json`` (what the app ships), ``pyproject.toml``/``uv.lock`` (the copy the
compatibility tests import), ``constraints/tts.txt`` (what the wheel installs with) and the
recorded fixtures (``python_tests/fixtures/provenance.json``). The STT server's pin is the Swift
catalog's ``sttServerRequirement``; its recorded fixtures are checked against it here too.

Moving a pin: see "Moving a server pin" in ``docs/migration.md``.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = json.loads((ROOT / "artifacts/tts.json").read_text())
PROVENANCE = json.loads((ROOT / "python_tests/fixtures/provenance.json").read_text())
SERVICE_SWIFT = ROOT / "Sources/PipecatToolbarCore/Service.swift"
PACKAGE = "pipecat-local-tts-server"


def sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def stt_version() -> str:
    match = re.search(
        r'static let sttServerRequirement = "pipecat-local-stt-server==([^"]+)"',
        SERVICE_SWIFT.read_text(),
    )
    assert match, "the parser sees ServiceCatalog.sttServerRequirement"
    return match.group(1)


def pins(text: str) -> dict[str, Version]:
    return {
        canonicalize_name(name): Version(version)
        for name, version in re.findall(r"^([A-Za-z0-9_.\-]+)==(\S+)", text, re.MULTILINE)
    }


def test_the_artifact_describes_one_release():
    version = ARTIFACT["version"]
    assert ARTIFACT["filename"] == f"pipecat_local_tts_server-{version}-py3-none-any.whl"
    assert ARTIFACT["filename"] in ARTIFACT["url"]
    assert re.fullmatch(r"[0-9a-f]{64}", ARTIFACT["sha256"])
    assert re.fullmatch(r"[0-9a-f]{40}", ARTIFACT["source_commit"])


def test_the_compatibility_tests_import_the_release_the_app_ships():
    version = ARTIFACT["version"]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert f"{PACKAGE}=={version}" in pyproject["dependencies"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = [p["version"] for p in lock["package"] if p["name"] == PACKAGE]
    assert locked == [version], "uv.lock: run `uv lock` after changing the pin"
    # The same file, not just the same version: the lock's wheel is the artifact the app bundles.
    (entry,) = [p for p in lock["package"] if p["name"] == PACKAGE]
    assert [(w["url"], w["hash"]) for w in entry["wheels"]] == [
        (ARTIFACT["url"], f"sha256:{ARTIFACT['sha256']}")
    ], "artifacts/tts.json and uv.lock name different wheels"
    # What the drift tests actually import (a stale .venv fails here, not silently later).
    assert importlib.metadata.version(PACKAGE) == version, "run `uv sync --locked`"


def test_the_recorded_constraints_belong_to_the_artifact():
    head = (ROOT / "constraints/tts.txt").read_text().splitlines()[:3]
    assert head[1] == f"# server: {PACKAGE}=={ARTIFACT['version']}"
    assert head[2] == f"# source-commit: {ARTIFACT['source_commit']}", (
        "regenerate with scripts/compile_tts_constraints.py"
    )


def test_the_recorded_constraints_satisfy_the_wheels_requirements():
    """Every requirement the wheel declares, for every extra, on the app's target (arm64 macOS,
    CPython 3.12), has a pin in ``constraints/tts.txt`` inside its version range."""
    recorded = pins((ROOT / "constraints/tts.txt").read_text())
    requirements = [Requirement(r) for r in importlib.metadata.requires(PACKAGE) or []]
    extras = sorted({e for r in requirements for e in _extras(r)})
    environment = {
        "sys_platform": "darwin",
        "platform_system": "Darwin",
        "platform_machine": "arm64",
        "os_name": "posix",
        "python_version": "3.12",
        "python_full_version": "3.12.0",
        "implementation_name": "cpython",
    }
    missing = []
    for requirement in requirements:
        if not any(
            requirement.marker is None or requirement.marker.evaluate({**environment, "extra": e})
            for e in ["", *extras]
        ):
            continue
        pinned = recorded.get(canonicalize_name(requirement.name))
        if pinned is None or not requirement.specifier.contains(pinned, prereleases=True):
            missing.append(f"{requirement} (pinned: {pinned})")
    assert missing == []
    assert extras, "the parser sees the wheel's extras"


def _extras(requirement: Requirement) -> set[str]:
    return set(re.findall(r'extra\s*==\s*"([^"]+)"', str(requirement.marker or "")))


@pytest.mark.parametrize("server", ["tts", "stt"])
def test_recorded_fixtures_are_for_the_pinned_server_release(server):
    entry = PROVENANCE[server]
    if server == "tts":
        assert entry["version"] == ARTIFACT["version"], "re-record the TTS fixtures"
        assert entry["source_commit"] == ARTIFACT["source_commit"]
    else:
        assert entry["version"] == stt_version(), "re-record the STT fixtures"
        assert (ROOT / f"python_tests/fixtures/stt_server_{entry['version']}_source.txt").is_file()


def test_every_recorded_fixture_is_listed_and_unchanged():
    listed = {path for server in ("tts", "stt") for path in PROVENANCE[server]["fixtures"]}
    on_disk = {
        str(p.relative_to(ROOT))
        for pattern in (
            "python_tests/fixtures/upstream/*",
            "python_tests/fixtures/stt_*",
            "docs/supervised-contract.json",
        )
        for p in ROOT.glob(pattern)
        if p.is_file()
    }
    assert on_disk == listed, "list every recorded fixture in python_tests/fixtures/provenance.json"
    stale = {
        path: sha256(path)
        for server in ("tts", "stt")
        for path, digest in PROVENANCE[server]["fixtures"].items()
        if sha256(path) != digest
    }
    assert stale == {}, "a recorded fixture changed: re-record it and update its digest"
