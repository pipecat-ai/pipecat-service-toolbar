import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts.compile_tts_constraints import render, verify_header
from scripts.prepare_tools import prepare

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "0123456789abcdef0123456789abcdef01234567"
#: A constraints file as scripts/compile_tts_constraints.py writes it for the fixture's pin.
CONSTRAINTS = render("0.9.1", COMMIT, "websockets==17.4\n")


def fixture(tmp_path, *, version="0.9.1"):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr(
            "pipecat_local_tts_server-0.9.1.dist-info/METADATA",
            f"Name: pipecat-local-tts-server\nVersion: {version}\n",
        )
        z.writestr("tts_server/__main__.py", "")
    content = raw.getvalue()
    pins = tmp_path / "server-pins/tts"
    pins.mkdir(parents=True)
    (pins / "constraints.txt").write_bytes(CONSTRAINTS.encode())
    (pins / "wheel.json").write_text(
        json.dumps(
            {
                "filename": "pipecat_local_tts_server-0.9.1-py3-none-any.whl",
                "version": "0.9.1",
                "url": "https://files.pythonhosted.org/packages/ab/cd/"
                "pipecat_local_tts_server-0.9.1-py3-none-any.whl",
                "source_commit": COMMIT,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    )
    return content


def test_verified_cache_is_reused_without_network(tmp_path):
    content = fixture(tmp_path)
    calls = []

    def fetch(url, path):
        calls.append(url)
        path.write_bytes(content)

    wheel = prepare(tmp_path / "first", root=tmp_path, fetch=fetch)
    assert wheel.read_bytes() == content
    assert (wheel.parent / "constraints.txt").read_bytes() == CONSTRAINTS.encode()
    prepare(tmp_path / "second", root=tmp_path, fetch=fetch)
    assert len(calls) == 1
    assert (tmp_path / "second/MANIFEST").read_text().strip() == wheel.name


def test_bad_download_never_creates_manifest_or_cached_wheel(tmp_path):
    fixture(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        prepare(tmp_path / "out", root=tmp_path, fetch=lambda _, p: p.write_bytes(b"corrupt"))
    assert not (tmp_path / "out/MANIFEST").exists()
    assert not list((tmp_path / ".build/artifacts").glob("*.whl"))


def test_corrupt_cache_is_rejected_even_without_network(tmp_path):
    content = fixture(tmp_path)
    wheel = prepare(tmp_path / "first", root=tmp_path, fetch=lambda _, p: p.write_bytes(content))
    (tmp_path / ".build/artifacts" / wheel.name).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256"):
        prepare(tmp_path / "second", root=tmp_path, fetch=lambda *_: pytest.fail("network"))
    assert not (tmp_path / "second/MANIFEST").exists()


def test_checksum_alone_cannot_substitute_another_version(tmp_path):
    content = fixture(tmp_path, version="0.9.2")
    with pytest.raises(ValueError, match="name/version"):
        prepare(tmp_path / "out", root=tmp_path, fetch=lambda _, p: p.write_bytes(content))


def respec(tmp_path, **changes):
    path = tmp_path / "server-pins/tts/wheel.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))


def test_url_must_name_the_pinned_wheel_and_the_release_commit_must_be_recorded(tmp_path):
    content = fixture(tmp_path)

    def fetch(_, p):
        p.write_bytes(content)

    respec(
        tmp_path, url="https://files.pythonhosted.org/packages/ab/cd/other-0.9.0-py3-none-any.whl"
    )
    with pytest.raises(ValueError, match="url does not point"):
        prepare(tmp_path / "out", root=tmp_path, fetch=fetch)
    respec(
        tmp_path,
        url="https://files.pythonhosted.org/packages/ab/cd/pipecat_local_tts_server-0.9.1-py3-none-any.whl",
        source_commit="main",
    )
    with pytest.raises(ValueError, match="source_commit"):
        prepare(tmp_path / "out", root=tmp_path, fetch=fetch)


def test_corrupt_cache_message_names_the_file_to_delete(tmp_path):
    content = fixture(tmp_path)
    wheel = prepare(tmp_path / "first", root=tmp_path, fetch=lambda _, p: p.write_bytes(content))
    (tmp_path / ".build/artifacts" / wheel.name).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="delete that file"):
        prepare(tmp_path / "second", root=tmp_path, fetch=lambda *_: pytest.fail("network"))


@pytest.mark.parametrize(
    ("constraints", "error"),
    [
        # A half-finished pin move: the previous release's recorded pins next to this wheel.
        (render("0.9.0", COMMIT, "websockets==17.4\n"), "does not name the pinned"),
        (render("0.9.1", "f" * 40, "websockets==17.4\n"), "does not name the pinned"),
        ("websockets==17.4\n", "does not name the pinned"),  # no header at all
        (CONSTRAINTS.replace("17.4", "17.5"), "content-sha256"),  # a hand edit to a pin
        (CONSTRAINTS.replace("\n", "\r\n"), "does not name the pinned"),  # not shipped as written
    ],
)
def test_constraints_from_another_release_or_edited_never_ship(tmp_path, constraints, error):
    """``just app-build`` and ``swift run`` never run pytest, so prepare() itself must refuse
    constraints whose header does not match the wheel's pin, before fetching anything."""
    fixture(tmp_path)
    (tmp_path / "server-pins/tts/constraints.txt").write_bytes(constraints.encode())
    with pytest.raises(ValueError, match=error):
        prepare(tmp_path / "out", root=tmp_path, fetch=lambda *_: pytest.fail("network"))
    assert not (tmp_path / "out").exists()


def test_the_recorded_constraints_pass_the_check_prepare_applies():
    """prepare() must accept the repository's own constraints for its own pin."""
    spec = json.loads((ROOT / "server-pins/tts/wheel.json").read_text())
    verify_header((ROOT / "server-pins/tts/constraints.txt").read_bytes(), spec)
