import hashlib
import io
import json
import zipfile

import pytest

from scripts.prepare_tools import prepare


def fixture(tmp_path, *, version="0.9.1"):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr(
            "pipecat_local_tts_server-0.9.1.dist-info/METADATA",
            f"Name: pipecat-local-tts-server\nVersion: {version}\n",
        )
        z.writestr("tts_server/__main__.py", "")
    content = raw.getvalue()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints/tts.txt").write_text("websockets==17.4\n")
    (tmp_path / "artifacts/tts.json").write_text(
        json.dumps(
            {
                "filename": "pipecat_local_tts_server-0.9.1-py3-none-any.whl",
                "version": "0.9.1",
                "url": "https://files.pythonhosted.org/test.whl",
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
    assert (wheel.parent / "constraints.txt").read_text() == "websockets==17.4\n"
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
