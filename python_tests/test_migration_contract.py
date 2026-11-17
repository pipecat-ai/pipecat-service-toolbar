"""Extraction-only guard: runtime and identity bytes must match the old app.

Future intentional runtime changes can retire individual pins with review; this
baseline records the repository move rather than a permanent freeze.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_extraction_preserves_all_runtime_and_identity_files():
    baseline = json.loads((ROOT / "python_tests/fixtures/migration-baseline.json").read_text())
    changed = [
        p
        for p, digest in baseline["files"].items()
        if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != digest
    ]
    assert changed == []


def test_build_version_and_server_fingerprint_are_independent():
    app = json.loads((ROOT / "app-version.json").read_text())
    server = json.loads((ROOT / "artifacts/tts.json").read_text())
    assert app["version"] == "0.9.1" and app["build"] >= 474
    assert server["filename"] == "pipecat_local_tts_server-0.9.1-py3-none-any.whl"
    build = (ROOT / "scripts/build_app.sh").read_text()
    assert "app-version.json" in build
    assert "rev-list --count" not in build
    assert "prepare_tools.py" in build
