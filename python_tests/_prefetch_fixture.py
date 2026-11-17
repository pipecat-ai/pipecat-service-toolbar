"""``python -m tts_server prefetch`` — LEAN tests.

Nothing touches the network or the real HF cache: the download seam
``tts_server.prefetch._snapshot_download`` is stubbed to build fake snapshot
directories under ``tmp_path``. Invariants:

  * each backend's ``prefetch_specs`` names exactly what ``serve`` would fetch
    (dia's codec, pocket's voice embeddings, phonon's pin + enabled voices);
  * ``--check --json`` is offline and reports ``cached`` per snapshot plus a
    summary; a missing ``huggingface_hub`` is a clean error event, exit 1;
  * ``--json`` emits ``start`` -> ``progress`` -> ``done`` -> ``complete``;
  * an optional snapshot that fails is a ``warning``, a required one an ``error``.
"""

from __future__ import annotations

import json
import sys

import pytest
from tts_server import __main__ as M
from tts_server import prefetch as PF
from tts_server.backends import phonon as PH


def _run(monkeypatch, capsys, *argv: str) -> tuple[int, list[dict], str]:
    monkeypatch.setattr(sys, "argv", ["tts_server", "prefetch", *argv])
    code = 0
    try:
        M.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    out, err = capsys.readouterr()
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    return code, events, err


class _StubTqdm:
    """The slice of ``tqdm.tqdm`` that ``prefetch._progress_class`` subclasses."""

    def __init__(self, iterable=None, desc=None, total=None, unit="it", **_kwargs):
        self.iterable, self.desc, self.total, self.unit, self.n = iterable, desc, total, unit, 0

    def update(self, n=1):
        self.n += n
        return True

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _optional_hub_modules(monkeypatch):
    """Let these tests run in the lean env (``--extra client``), which has no
    ``tqdm``: stub it in ``sys.modules`` only when it is absent, so a full env
    still exercises the real class. ``huggingface_hub`` itself is never needed —
    every test stubs the ``_snapshot_download`` seam."""
    import importlib.util
    import types

    if importlib.util.find_spec("tqdm") is None:
        stub = types.ModuleType("tqdm")
        stub.tqdm = _StubTqdm
        monkeypatch.setitem(sys.modules, "tqdm", stub)


@pytest.fixture
def fake_hub(tmp_path, monkeypatch):
    """Stub the download seam. ``cached`` holds repo ids already "on disk"."""
    cached: set[str] = set()
    calls: list[tuple[str, bool]] = []

    def fake(spec, *, local_files_only, tqdm_class=None):
        calls.append((spec.repo_id, local_files_only))
        snap = tmp_path / "hub" / spec.repo_id.replace("/", "--")
        if local_files_only and spec.repo_id not in cached:
            raise FileNotFoundError(f"{spec.repo_id} not cached")
        if not local_files_only:
            if tqdm_class is not None:
                files = tqdm_class(total=2, desc="Fetching 2 files")
                bytes_bar = tqdm_class(
                    total=0, desc="Reconstructing (incomplete total...)", unit="B"
                )
                bytes_bar.total = 1000
                bytes_bar.update(400)
                bytes_bar.update(600)
                files.update(2)
            snap.mkdir(parents=True, exist_ok=True)
            (snap / "model.safetensors").write_bytes(b"x" * 1000)
            cached.add(spec.repo_id)
        return str(snap)

    monkeypatch.setattr(PF, "_snapshot_download", fake)
    monkeypatch.delenv(PH.VOICES_ENV, raising=False)
    return cached, calls
