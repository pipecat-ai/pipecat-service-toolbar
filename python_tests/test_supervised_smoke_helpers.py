"""Cross-repository parity for the supervised smoke child environment."""

import re

from .smoke import _supervised_contract as sc


def _swift_base_env_keys() -> set[str]:
    text = (sc.REPO / "Sources" / "PipecatToolbarCore" / "ToolLocator.swift").read_text()
    body = text[text.index("public static func base(") :]
    body = body[: body.index("return env")]
    keys = set(re.findall(r'"([A-Z][A-Z0-9_]+)":', body))  # the literal dict
    keys |= set(re.findall(r'env\["([A-Z][A-Z0-9_]+)"\]', body))  # env["TMPDIR"] = ...
    for listed in re.findall(r"for key in \[([^\]]*)\]", body):  # inherited keys
        keys |= set(re.findall(r'"([A-Z][A-Z0-9_]+)"', listed))
    return keys


def test_app_env_mirrors_toollocator_base():
    """The smoke's child env is the app's ``ProcessEnvironment.base()`` key for key."""
    assert sc.APP_BASE_ENV_KEYS == _swift_base_env_keys()


def test_canonical_ports_come_from_the_recorded_tts_justfile():
    """This repository's own justfile has no ``_resolve`` table, so the default must be the
    recorded TTS one: every TTS family port the app serves is off limits to the smoke."""
    from ._macos_catalog import macos_catalog

    tts = {e.port for e in macos_catalog().values() if e.kind == "tts" and not e.is_variant}
    assert tts <= sc.canonical_ports()
    assert not sc.usable_smoke_port(8765, sc.canonical_ports())
