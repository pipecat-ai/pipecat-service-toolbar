"""Identity guard: bundle identity, signing, ``AppPaths`` layout and preference keys must keep
matching the app that is already installed.

The baseline lists only those bytes (see its ``_comment``). Everything else, including server
pins, the STT constraints and ordinary Swift changes, is free to move; behavioural tests cover
what must not change. Changing a listed file is an identity change for every installed copy:
review it as one, then update its digest.
"""

import hashlib
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The first build number taken from this repository (installed 0.9.1 builds were 468).
FIRST_BUILD_HERE = 474


def test_identity_and_layout_files_match_the_installed_app():
    baseline = json.loads((ROOT / "python_tests/fixtures/migration-baseline.json").read_text())
    changed = [
        p
        for p, digest in baseline["files"].items()
        if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != digest
    ]
    assert changed == []


def test_build_version_and_server_fingerprint_are_independent():
    app = json.loads((ROOT / "app-version.json").read_text())
    server = json.loads((ROOT / "server-pins/tts/wheel.json").read_text())
    assert re.fullmatch(r"\d+\.\d+\.\d+", app["version"])
    assert isinstance(app["build"], int) and app["build"] >= FIRST_BUILD_HERE
    # The fingerprint of installed TTS environments is the wheel name, not anything of the app's.
    assert server["filename"] == f"pipecat_local_tts_server-{server['version']}-py3-none-any.whl"
    build = (ROOT / "scripts/build_app.sh").read_text()
    assert "app-version.json" in build
    assert "rev-list --count" not in build
    assert "prepare_tools.py" in build


def test_the_app_version_is_the_latest_released_changelog_entry():
    """One source (``app-version.json``); the changelog's newest numbered entry follows it, and
    nothing else carries a second app version."""
    app = json.loads((ROOT / "app-version.json").read_text())
    headings = re.findall(r"^## \[(\d[^]]*)\]", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert headings[0] == app["version"], "release: add the changelog entry, then bump the version"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == "0.0.0"
