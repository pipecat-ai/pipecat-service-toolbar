"""Check active documentation links and the inherited changelog's release coverage."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def test_active_local_links_exist():
    files = [ROOT / "README.md", ROOT / "AGENTS.md", *(ROOT / "docs").glob("*.md")]
    broken = []
    for path in files:
        text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
        for link in re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", text):
            parts = urlsplit(link)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            if not (path.parent / unquote(parts.path)).exists():
                broken.append(f"{path.relative_to(ROOT)}: {link}")
    assert broken == []


def test_changelog_preserves_toolbar_release_history():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert re.findall(r"^## \[([^]]+)\]", text, re.M) == [
        "Unreleased",
        "0.9.1",
        "0.9.0",
        "0.8.3",
        "0.8.2",
        "0.8.1",
        "0.8.0",
        "0.7.0",
    ]
    assert "before this repository existed" in text
    for phrase in [
        "Moonshine STT",
        "restoreOnLaunch",
        "has_sign_identity.sh",
        "adopts running servers",
    ]:
        assert phrase in text
