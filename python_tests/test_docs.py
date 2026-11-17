"""Check active documentation links and the changelog's release coverage."""

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


HISTORY = ["0.4.0", "0.3.0", "0.2.3", "0.2.2", "0.2.1", "0.2.0", "0.1.0"]


def changelog_versions() -> list[str]:
    text = (ROOT / "CHANGELOG.md").read_text()
    return re.findall(r"^## \[([^]]+)\]", text, re.M)


def test_changelog_preserves_the_renumbered_release_history():
    """The app's first releases stay in the changelog; new releases are added above them."""
    versions = [v for v in changelog_versions() if v != "Unreleased"]
    assert versions[-len(HISTORY) :] == HISTORY
    keys = [tuple(int(part) for part in v.split(".")) for v in versions]
    assert keys == sorted(keys, reverse=True) and len(set(keys)) == len(keys)
    text = (ROOT / "CHANGELOG.md").read_text()
    for old in ["0.9.1", "0.9.0", "0.8.3", "0.8.2", "0.8.1", "0.8.0", "0.7.0"]:
        assert f"Shipped as {old} alongside" in text, old
    for phrase in [
        "Moonshine STT",
        "restoreOnLaunch",
        "has_sign_identity.sh",
        "Adopts running servers",
    ]:
        assert phrase in text


def test_changelog_is_the_apps_not_the_servers():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "macos/" not in text and "macOS app:" not in text
    assert (
        "alongside [TTS server" in text
    )  # provenance of the old numbers, not server release notes
