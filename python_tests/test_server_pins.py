"""The server pins move together, or a test fails.

A TTS release is described by several files that nothing else keeps in step:
``artifacts/tts.json`` (what the app ships), ``pyproject.toml``/``uv.lock`` (the copy the
compatibility tests import), ``constraints/tts.txt`` (what the wheel installs with) and the
recorded fixtures (``python_tests/fixtures/provenance.json``; CI compares the TTS ones with the
pinned commit, ``scripts/check_tts_fixtures.py``). ``uv.lock`` and
``constraints/tts.txt`` also agree on every package they share, so the compatibility tests run
the dependency versions the app installs. The STT server's pin is the Swift
catalog's ``sttServerRequirement``; its recorded fixtures and hash-pinned
``constraints/stt-*.txt`` are digested in ``provenance.json`` and checked against it here too.

Moving a pin: see "Moving a server pin" in ``docs/migration.md``.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from scripts import check_tts_fixtures, compile_tts_constraints

from ._stt_upstream import FIXTURE as STT_EXCERPT
from ._stt_upstream import excerpt_for, stt_server_version, upstream_stt

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = json.loads((ROOT / "artifacts/tts.json").read_text())
PROVENANCE = json.loads((ROOT / "python_tests/fixtures/provenance.json").read_text())
SERVICE_SWIFT = ROOT / "Sources/PipecatToolbarCore/Service.swift"
CONSTRAINTS = ROOT / "constraints/tts.txt"
PACKAGE = "pipecat-local-tts-server"
# The app's target: the bundled uv installs the TTS server on arm64 macOS, CPython 3.12.
TARGET = {
    "sys_platform": "darwin",
    "platform_system": "Darwin",
    "platform_machine": "arm64",
    "os_name": "posix",
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "implementation_name": "cpython",
}


def sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def stt_version() -> str:
    return stt_server_version(SERVICE_SWIFT)


def pins(text: str) -> dict[str, Version]:
    """The ``name==version`` pins whose environment marker holds on ``TARGET``.

    A package may be pinned once per environment (``numpy`` has one pin below CPython 3.13 and
    one from it); two pins that both apply would leave the installed version to chance."""
    found: dict[str, Version] = {}
    clashes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate(TARGET):
            continue
        (specifier,) = requirement.specifier
        assert specifier.operator == "==", f"not an exact pin: {line}"
        name = canonicalize_name(requirement.name)
        if name in found:
            clashes.append(name)
        found[name] = Version(specifier.version)
    assert clashes == [], f"more than one pin applies on the app's target: {clashes}"
    return found


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


def test_the_compatibility_tests_lock_the_dependency_versions_the_app_installs():
    """Every package ``uv.lock`` shares with an applicable pin in ``constraints/tts.txt`` is
    locked at that pin, so the tests (and the Swift tone-server tests that launch ``.venv``)
    exercise what the bundled server installs with."""
    recorded = pins(CONSTRAINTS.read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    drift = {
        package["name"]: f"locked {package['version']}, shipped {recorded[name]}"
        for package in lock["package"]
        if (name := canonicalize_name(package["name"])) in recorded
        and Version(package["version"]) != recorded[name]
    }
    assert drift == {}, (
        "set these to the shipped version in [tool.uv] constraint-dependencies in pyproject.toml,"
        " then run `uv lock` and `uv sync --locked`"
    )


def test_the_recorded_constraints_belong_to_the_artifact():
    lines = CONSTRAINTS.read_bytes().splitlines(keepends=True)
    head = [line.decode().rstrip("\n") for line in lines[:4]]
    assert head[1] == f"# server: {PACKAGE}=={ARTIFACT['version']}"
    assert head[2] == f"# source-commit: {ARTIFACT['source_commit']}", (
        "regenerate with scripts/compile_tts_constraints.py"
    )
    # scripts/prepare_tools.py copies the file unchanged, so a hand edit to any line (the
    # provenance lines above included) must not pass: the fourth line is the SHA-256 of every
    # other line, in file order. It is an unkeyed accidental-edit check, not proof of origin;
    # `just tts-constraints-check <checkout>` (run in CI) proves the pins are the release's lock.
    covered = b"".join(lines[:3] + lines[4:])
    assert head[3] == f"# content-sha256: {hashlib.sha256(covered).hexdigest()}", (
        "constraints/tts.txt was edited after scripts/compile_tts_constraints.py wrote it"
        " (content-sha256 does not match the other lines): regenerate it, do not edit it by hand"
    )


def test_the_lean_lock_constrains_only_what_the_server_pins():
    """Every ``[tool.uv] constraint-dependencies`` entry in ``pyproject.toml`` is an applicable
    pin of ``constraints/tts.txt`` at the same version, so a pin move cannot leave a stale entry
    holding the lean environment at a version the bundled server no longer installs."""
    recorded = pins(CONSTRAINTS.read_text())
    entries = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"].get(
        "constraint-dependencies", []
    )
    stale = []
    for entry in entries:
        requirement = Requirement(entry)
        specifiers = list(requirement.specifier)
        pinned = recorded.get(canonicalize_name(requirement.name))
        if (
            requirement.marker is not None
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
            or pinned is None
            or Version(specifiers[0].version) != pinned
        ):
            stale.append(f"{entry} (constraints/tts.txt: {pinned})")
    assert stale == [], (
        "remove these from [tool.uv] constraint-dependencies in pyproject.toml (or set them to the"
        " constraints/tts.txt version), then run `uv lock` and `uv sync --locked`"
    )


def test_the_recorded_constraints_satisfy_the_wheels_requirements():
    """Every requirement the wheel declares, for every extra, on the app's target (arm64 macOS,
    CPython 3.12), has a pin in ``constraints/tts.txt`` inside its version range."""
    recorded = pins(CONSTRAINTS.read_text())
    requirements = [Requirement(r) for r in importlib.metadata.requires(PACKAGE) or []]
    extras = sorted({e for r in requirements for e in _extras(r)})
    missing = []
    for requirement in requirements:
        if not any(
            requirement.marker is None or requirement.marker.evaluate({**TARGET, "extra": e})
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


SECTIONS = ("tts", "contract", "stt")


@pytest.mark.parametrize("server", ["tts", "stt"])
def test_recorded_fixtures_are_for_the_pinned_server_release(server):
    entry = PROVENANCE[server]
    if server == "tts":
        assert entry["version"] == ARTIFACT["version"], "re-record the TTS fixtures"
        assert entry["source_commit"] == ARTIFACT["source_commit"]
    else:
        assert entry["version"] == stt_version(), "re-record the STT fixtures"
        assert (ROOT / f"python_tests/fixtures/stt_server_{entry['version']}_source.txt").is_file()
        # Every reader of the excerpt follows the pin (not a hard-coded release).
        assert STT_EXCERPT.name == f"stt_server_{entry['version']}_source.txt"


def test_every_recorded_fixture_is_listed_and_unchanged():
    listed = {path for server in SECTIONS for path in PROVENANCE[server]["fixtures"]}
    on_disk = {
        str(p.relative_to(ROOT))
        for pattern in (
            "python_tests/fixtures/upstream/*",
            "python_tests/fixtures/stt_*",
            "docs/supervised-contract.json",
            # Hash-pinned by a network resolver, so CI cannot regenerate them: a digest here makes
            # any edit (a dropped transitive pin included) a deliberate provenance update.
            "constraints/stt-*.txt",
        )
        for p in ROOT.glob(pattern)
        if p.is_file()
    }
    assert on_disk == listed, "list every recorded fixture in python_tests/fixtures/provenance.json"
    stale = {
        path: sha256(path)
        for server in SECTIONS
        for path, digest in PROVENANCE[server]["fixtures"].items()
        if sha256(path) != digest
    }
    assert stale == {}, "a recorded fixture changed: re-record it and update its digest"


def test_ci_compares_every_file_recorded_from_the_tts_repository():
    """``scripts/check_tts_fixtures.py`` (run in CI against the pinned commit) knows the source of
    every file ``provenance.json`` records from the TTS repository, so bumping the version without
    re-recording fails CI."""
    recorded = {*PROVENANCE["tts"]["fixtures"], *PROVENANCE["contract"]["fixtures"]}
    assert set(check_tts_fixtures.UPSTREAM) == recorded


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, bytes]) -> str:
    for path, data in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(data)
    _git(repo, "add", *files)
    _git(repo, "commit", "-q", "-m", "record")
    return _git(repo, "rev-parse", "HEAD")


def test_the_tts_fixture_check_fails_on_a_fixture_that_was_not_re_recorded(tmp_path):
    repo = tmp_path / "server"
    repo.mkdir()
    _git(repo, "init", "-q")
    contract = json.loads((ROOT / "docs/supervised-contract.json").read_text())
    upstream = {
        source: (ROOT / recorded).read_bytes()
        for recorded, source in check_tts_fixtures.UPSTREAM.items()
    }
    # The server's own _comment differs from the toolbar's copy; that alone is not drift.
    upstream["docs/supervised-contract.json"] = json.dumps(
        {**contract, "_comment": ["the server's own wording"]}, indent=2
    ).encode()
    same = _commit(repo, upstream)
    assert check_tts_fixtures.drift(repo, same) == []
    readme = _commit(repo, {"README.md": upstream["README.md"] + b"\n"})
    assert check_tts_fixtures.drift(repo, readme) == [
        f"python_tests/fixtures/upstream/tts-README.md is not README.md at {readme}"
    ]
    _commit(repo, {"README.md": upstream["README.md"]})
    changed = {**contract, "stt_wire_rate": contract["stt_wire_rate"] + 1}
    field = _commit(repo, {"docs/supervised-contract.json": json.dumps(changed).encode()})
    assert check_tts_fixtures.drift(repo, field) == [
        f"docs/supervised-contract.json is not docs/supervised-contract.json at {field}"
    ]


@pytest.mark.parametrize(
    "call",
    [
        lambda d: compile_tts_constraints.run(["git", "-C", str(d), "archive", "HEAD"]),
        lambda d: check_tts_fixtures.drift(d, "0" * 40),
    ],
    ids=["compile", "fixtures"],
)
def test_a_failing_git_step_shows_gits_own_error(tmp_path, capsys, call):
    with pytest.raises(SystemExit) as failed:
        call(tmp_path)
    assert "failed (exit" in str(failed.value)
    assert "not a git repository" in capsys.readouterr().err


def test_constraints_check_compares_bytes(tmp_path, monkeypatch):
    """``--check`` passes only on the exact bytes the script writes: a CRLF copy is not them."""
    text = CONSTRAINTS.read_text()
    monkeypatch.setattr(compile_tts_constraints, "compile_constraints", lambda *_: text)
    monkeypatch.setattr(compile_tts_constraints.shutil, "which", lambda _: "uv")
    exact, crlf = tmp_path / "exact.txt", tmp_path / "crlf.txt"
    exact.write_bytes(text.encode())
    crlf.write_bytes(text.replace("\n", "\r\n").encode())
    check = ["--server-checkout", str(tmp_path), "--check", "--output"]
    assert compile_tts_constraints.main([*check, str(exact)]) == 0
    assert compile_tts_constraints.main([*check, str(crlf)]) == 1


def test_the_stt_excerpt_follows_the_pin(tmp_path):
    swift = tmp_path / "Service.swift"
    swift.write_text(
        '    public static let sttServerRequirement = "pipecat-local-stt-server==9.8.7"\n'
    )
    assert excerpt_for(swift).name == "stt_server_9.8.7_source.txt"
    assert STT_EXCERPT == excerpt_for(SERVICE_SWIFT)
    with pytest.raises(AssertionError, match="record stt_server_9.8.7_source.txt"):
        upstream_stt(excerpt_for(swift))
