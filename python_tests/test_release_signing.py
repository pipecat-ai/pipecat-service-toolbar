"""Release signing (SIGN_MODE=release): .env loading, Developer ID identity resolution, the
signature check and build_app.sh's preflight. Every external tool is a stub, so no certificate,
keychain or network is needed. All scripts run under /bin/bash (3.2 on macOS)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
RELEASE_ENV = SCRIPTS / "lib" / "release_env.sh"
RESOLVE = SCRIPTS / "resolve_release_identity.sh"
VERIFY = SCRIPTS / "verify_release_signature.sh"
BUILD = SCRIPTS / "build_app.sh"

TEAM = "ABCDE12345"
OTHER_TEAM = "ZYXWV98765"
HASH_A = "A" * 40
HASH_B = "B" * 40
HASH_C = "C" * 40
NAME_A = f"Developer ID Application: Example Co. ({TEAM})"
NAME_B = f"Developer ID Application: Example Co. Second ({TEAM})"
NAME_OTHER = f"Developer ID Application: Other Co. ({OTHER_TEAM})"


def _listing(*rows: tuple[str, str]) -> str:
    lines = [f'  {i}) {h} "{n}"' for i, (h, n) in enumerate(rows, 1)]
    lines.append(f"     {len(rows)} valid identities found")
    return "\n".join(lines) + "\n"


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    # security: prints $STUB_LISTING, or fails like a locked keychain when it is unset.
    _stub(
        d,
        "security",
        'echo "$*" >> "$STUB_CALLS"\n'
        'if [[ -n "${STUB_LISTING:-}" ]]; then cat "$STUB_LISTING"; exit 0; fi\n'
        "echo 'security: keychain locked' >&2; exit 45\n",
    )
    return d


def _env(bindir: Path, tmp_path: Path, listing: str | None, **extra: str) -> dict[str, str]:
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "STUB_CALLS": str(tmp_path / "security-calls"),
    }
    if listing is not None:
        f = tmp_path / "listing"
        f.write_text(listing)
        env["STUB_LISTING"] = str(f)
    env.update(extra)
    return env


# --- .env loading -------------------------------------------------------------------------


def _load(tmp_path: Path, content: str | None, **env: str) -> subprocess.CompletedProcess:
    dotenv = tmp_path / ".env"
    if content is not None:
        dotenv.write_text(content)
    script = (
        f'source "{RELEASE_ENV}"\n'
        f'load_release_env "{dotenv}" || exit $?\n'
        'for k in $RELEASE_ENV_KEYS UNRELATED; do printf "%s=[%s]\\n" "$k" "${!k:-}"; done\n'
    )
    return subprocess.run(
        ["/bin/bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", **env},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _vars(result: subprocess.CompletedProcess) -> dict[str, str]:
    return dict(re.findall(r"^(\w+)=\[(.*)\]$", result.stdout, re.MULTILINE))


def test_env_loader_parses_plain_quoted_exported_and_crlf_lines(tmp_path):
    r = _load(
        tmp_path,
        "# comment\n\n"
        f"  APPLE_TEAM_ID={TEAM}\r\n"
        "export NOTARY_PROFILE='my profile'\n"
        'SIGN_IDENTITY="Developer ID Application: X (Y)"   \n',
    )
    assert r.returncode == 0, r.stderr
    v = _vars(r)
    assert v["APPLE_TEAM_ID"] == TEAM
    assert v["NOTARY_PROFILE"] == "my profile"
    assert v["SIGN_IDENTITY"] == "Developer ID Application: X (Y)"


def test_env_loader_takes_an_empty_last_line_without_a_newline(tmp_path):
    r = _load(tmp_path, f"APPLE_TEAM_ID={TEAM}\nSIGN_IDENTITY=")
    assert r.returncode == 0, r.stderr
    v = _vars(r)
    assert v["APPLE_TEAM_ID"] == TEAM and v["SIGN_IDENTITY"] == ""


def test_env_loader_ignores_the_retired_notary_id_keys(tmp_path):
    # Older .env files may still carry them; nothing reads them, so they must stay harmless.
    r = _load(tmp_path, f"APPLE_TEAM_ID={TEAM}\nNOTARY_KEY_ID=KEY123\nNOTARY_ISSUER_ID=abc\n")
    assert r.returncode == 0, r.stderr
    assert _vars(r)["APPLE_TEAM_ID"] == TEAM


def test_env_loader_ignores_unknown_keys(tmp_path):
    r = _load(tmp_path, "UNRELATED=1\nPATH=/evil\n")
    assert r.returncode == 0, r.stderr
    assert _vars(r)["UNRELATED"] == ""


def test_env_loader_lets_the_environment_win(tmp_path):
    r = _load(tmp_path, f"APPLE_TEAM_ID={TEAM}\n", APPLE_TEAM_ID=OTHER_TEAM)
    assert _vars(r)["APPLE_TEAM_ID"] == OTHER_TEAM


def test_env_loader_never_executes_the_file(tmp_path):
    r = _load(tmp_path, "APPLE_TEAM_ID=$(touch pwned)`touch pwned2`\n")
    assert r.returncode == 0, r.stderr
    assert _vars(r)["APPLE_TEAM_ID"] == "$(touch pwned)`touch pwned2`"
    assert not (tmp_path / "pwned").exists() and not (tmp_path / "pwned2").exists()


def test_env_loader_names_the_bad_line_without_echoing_it(tmp_path):
    r = _load(tmp_path, "APPLE_TEAM_ID=X\nthis is secret-ish\n")
    assert r.returncode == 2
    assert ".env:2" in r.stderr
    assert "secret-ish" not in r.stderr


def test_env_loader_accepts_a_missing_file(tmp_path):
    r = _load(tmp_path, None)
    assert r.returncode == 0, r.stderr


def test_env_example_lists_exactly_the_loaded_keys_and_holds_no_values():
    example = (REPO_ROOT / ".env.example").read_text()
    pairs = dict(re.findall(r"^([A-Z_]+)=(.*)$", example, re.MULTILINE))
    keys = (
        re.search(r'^RELEASE_ENV_KEYS="([^"]*)"', RELEASE_ENV.read_text(), re.MULTILINE)
        .group(1)
        .split()
    )
    assert sorted(pairs) == sorted(keys)
    assert {k: v for k, v in pairs.items() if v} == {"NOTARY_PROFILE": "pipecat-notary"}


def test_private_files_are_git_ignored():
    for path in (
        ".env",
        ".private/note.md",
        ".local_certificates/key.p12",
        "AuthKey_ABC123.p8",
        "devid.p12",
        "scripts/developerID_application.cer",
    ):
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO_ROOT)
        assert r.returncode == 0, f"{path} is not git-ignored"
    r = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=REPO_ROOT)
    assert r.returncode == 1, ".env.example must stay committed"


# --- identity resolution ------------------------------------------------------------------


def _resolve(bindir, tmp_path, listing, *args, **extra):
    args = args or (TEAM,)
    return subprocess.run(
        ["/bin/bash", str(RESOLVE), *args],
        env=_env(bindir, tmp_path, listing, **extra),
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("rows", "sign_identity", "code", "printed"),
    [
        pytest.param([(HASH_A, NAME_A)], None, 0, HASH_A, id="one"),
        pytest.param([(HASH_A, NAME_A), (HASH_A, NAME_A)], None, 0, HASH_A, id="listed-twice"),
        pytest.param(
            [(HASH_C, NAME_OTHER), (HASH_A, NAME_A)], None, 0, HASH_A, id="other-team-skipped"
        ),
        pytest.param([], None, 1, None, id="none"),
        pytest.param([(HASH_C, NAME_OTHER)], None, 1, None, id="only-other-team"),
        pytest.param([(HASH_C, "Code Signing")], None, 1, None, id="self-signed"),
        pytest.param(
            [(HASH_C, f"Apple Development: Dev ({TEAM})")], None, 1, None, id="apple-development"
        ),
        pytest.param([(HASH_A, NAME_A), (HASH_B, NAME_B)], None, 1, None, id="two-ambiguous"),
        pytest.param([(HASH_A, NAME_A), (HASH_B, NAME_B)], HASH_B, 0, HASH_B, id="two-pick-hash"),
        pytest.param(
            [(HASH_A, NAME_A), (HASH_B, NAME_B)], HASH_B.lower(), 0, HASH_B, id="hash-any-case"
        ),
        pytest.param([(HASH_A, NAME_A), (HASH_B, NAME_B)], NAME_B, 0, HASH_B, id="two-pick-name"),
        pytest.param([(HASH_A, NAME_A)], "Code Signing", 1, None, id="explicit-self-signed"),
        pytest.param([(HASH_A, NAME_A)], "-", 1, None, id="explicit-ad-hoc"),
        pytest.param([(HASH_A, NAME_A)], "Developer ID", 1, None, id="partial-name"),
        pytest.param([(HASH_C, NAME_OTHER)], HASH_C, 1, None, id="explicit-other-team"),
    ],
)
def test_resolve_release_identity(bindir, tmp_path, rows, sign_identity, code, printed):
    extra = {} if sign_identity is None else {"SIGN_IDENTITY": sign_identity}
    r = _resolve(bindir, tmp_path, _listing(*rows), **extra)
    assert r.returncode == code, r.stderr
    assert r.stdout == (f"{printed}\n" if printed else "")


def test_resolve_lists_the_candidates_when_ambiguous(bindir, tmp_path):
    r = _resolve(bindir, tmp_path, _listing((HASH_A, NAME_A), (HASH_B, NAME_B)))
    assert HASH_A in r.stderr and HASH_B in r.stderr and "SIGN_IDENTITY" in r.stderr


def test_resolve_asks_only_for_valid_identities(bindir, tmp_path):
    _resolve(bindir, tmp_path, _listing((HASH_A, NAME_A)))
    assert (tmp_path / "security-calls").read_text().split() == [
        "find-identity",
        "-v",
        "-p",
        "codesigning",
    ]


def _stub_security_with_untrusted(bindir: Path) -> None:
    """`security` reports no valid identity with -v, but lists $UNTRUSTED_NAME without it."""
    _stub(
        bindir,
        "security",
        'if [[ " $* " == *" -v "* ]]; then printf "     0 valid identities found\\n"; exit 0; fi\n'
        'printf \'  1) %s "%s"\\n     1 identities found\\n\' "$UNTRUSTED_HASH" "$UNTRUSTED_NAME"\n',
    )


def test_resolve_hints_at_the_missing_intermediate(bindir, tmp_path):
    # Imported but untrusted: not in the -v listing, present without -v.
    _stub_security_with_untrusted(bindir)
    r = _resolve(bindir, tmp_path, None, UNTRUSTED_HASH=HASH_A, UNTRUSTED_NAME=NAME_A)
    assert r.returncode == 1 and r.stdout == ""
    assert "not valid" in r.stderr and "intermediate" in r.stderr
    assert "import the Developer ID .p12" not in r.stderr


@pytest.mark.parametrize(
    ("name", "sign_identity"),
    [
        pytest.param(NAME_OTHER, None, id="other-team"),
        pytest.param("Code Signing", None, id="self-signed"),
        pytest.param(NAME_A, "Code Signing", id="not-the-requested-identity"),
    ],
)
def test_resolve_gives_no_intermediate_hint_when_nothing_matches(
    bindir, tmp_path, name, sign_identity
):
    _stub_security_with_untrusted(bindir)
    extra = {} if sign_identity is None else {"SIGN_IDENTITY": sign_identity}
    r = _resolve(bindir, tmp_path, None, UNTRUSTED_HASH=HASH_C, UNTRUSTED_NAME=name, **extra)
    assert r.returncode == 1
    assert "intermediate" not in r.stderr


def test_resolve_still_says_to_import_when_no_identity_exists(bindir, tmp_path):
    r = _resolve(bindir, tmp_path, _listing())
    assert r.returncode == 1
    assert "import the Developer ID .p12" in r.stderr and "intermediate" not in r.stderr


def test_resolve_ignores_an_invalid_identity_line(bindir, tmp_path):
    listing = f'  1) {HASH_A} "{NAME_A}" (CSSMERR_TP_CERT_EXPIRED)\n     1 identities found\n'
    assert _resolve(bindir, tmp_path, listing).returncode == 1


def test_resolve_fails_closed_when_the_keychain_query_fails(bindir, tmp_path):
    r = _resolve(bindir, tmp_path, None)
    assert r.returncode == 2 and r.stdout == ""


@pytest.mark.parametrize("args", [(), ("abcde12345",), ("SHORT",), (TEAM, "extra")])
def test_resolve_rejects_bad_usage(bindir, tmp_path, args):
    r = subprocess.run(
        ["/bin/bash", str(RESOLVE), *args],
        env=_env(bindir, tmp_path, _listing((HASH_A, NAME_A))),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 2


# --- signature verification ---------------------------------------------------------------

_GOOD_DV = (
    "Executable=/x\n"
    "Identifier=ai.pipecat.ServiceToolbar\n"
    "Format=app bundle with Mach-O thin (arm64)\n"
    "CodeDirectory v=20500 size=1 flags=0x10000(runtime) hashes=1+7 location=embedded\n"
    f"Authority={NAME_A}\n"
    "Authority=Developer ID Certification Authority\n"
    "Authority=Apple Root CA\n"
    "Timestamp=23 Nov 2026 at 14:11:09\n"
    f"TeamIdentifier={TEAM}\n"
)
_GOOD_ENT = '<?xml version="1.0"?><plist><dict><key>com.apple.security.device.audio-input</key><true/></dict></plist>'


@pytest.fixture
def fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "Pipecat Service Toolbar.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources/bin").mkdir(parents=True)
    uv = app / "Contents/Resources/bin/uv"
    uv.write_text("")
    uv.chmod(0o755)
    return app


def _verify(tmp_path, app, dv=_GOOD_DV, ent=_GOOD_ENT, verify_rc=0, team=TEAM, ent_rc=0):
    bindir = tmp_path / "codesign-bin"
    bindir.mkdir(exist_ok=True)
    (tmp_path / "dv").write_text(dv)
    (tmp_path / "ent").write_text(ent)
    _stub(
        bindir,
        "codesign",
        'case " $* " in\n'
        '  *" --verify "*) exit "$STUB_VERIFY_RC" ;;\n'
        '  *" --entitlements "*) cat "$STUB_ENT"; exit "$STUB_ENT_RC" ;;\n'
        '  *" -dvvv "*) cat "$STUB_DV" >&2 ;;\n'
        "  *) exit 64 ;;\n"
        "esac\n",
    )
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "STUB_DV": str(tmp_path / "dv"),
        "STUB_ENT": str(tmp_path / "ent"),
        "STUB_VERIFY_RC": str(verify_rc),
        "STUB_ENT_RC": str(ent_rc),
    }
    return subprocess.run(
        ["/bin/bash", str(VERIFY), str(app), team],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_verify_accepts_a_release_signature(tmp_path, fake_app):
    r = _verify(tmp_path, fake_app)
    assert r.returncode == 0, r.stderr


def test_verify_accepts_no_entitlements(tmp_path, fake_app):
    # The nested uv has none: codesign prints nothing and succeeds.
    r = _verify(tmp_path, fake_app, ent="")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(
            {"dv": _GOOD_DV.replace(NAME_A, "Apple Development: Dev (X)")},
            "Developer ID Application",
            id="apple-development",
        ),
        pytest.param(
            {"dv": _GOOD_DV.replace(NAME_A, "Code Signing")},
            "Developer ID Application",
            id="self-signed",
        ),
        pytest.param(
            {"dv": _GOOD_DV.replace("Authority=Apple Root CA\n", "")}, "Apple Root CA", id="chain"
        ),
        pytest.param(
            {"dv": _GOOD_DV.replace("Timestamp=", "Signed Time=")},
            "secure timestamp",
            id="no-timestamp",
        ),
        pytest.param(
            {"dv": _GOOD_DV.replace("(runtime)", "(none)")}, "hardened runtime", id="no-runtime"
        ),
        pytest.param({"team": OTHER_TEAM}, "TeamIdentifier", id="wrong-team"),
        pytest.param(
            {"dv": _GOOD_DV.replace("ai.pipecat.ServiceToolbar", "com.example.x")},
            "Identifier",
            id="bundle-id",
        ),
        pytest.param(
            {"ent": "<key>com.apple.security.get-task-allow</key><true/>"},
            "get-task-allow",
            id="get-task-allow",
        ),
        pytest.param({"verify_rc": 1}, "--verify", id="verify-fails"),
        # A failed entitlements read, with or without output, must not count as "no entitlements".
        pytest.param(
            {"ent": "", "ent_rc": 42}, "cannot read the entitlements", id="ent-read-fails"
        ),
        pytest.param({"ent_rc": 1}, "cannot read the entitlements", id="ent-read-fails-output"),
    ],
)
def test_verify_refuses(tmp_path, fake_app, change, message):
    r = _verify(tmp_path, fake_app, **change)
    assert r.returncode == 1
    assert message in r.stderr


def test_verify_checks_the_nested_uv_too(tmp_path, fake_app):
    # The stub answers the same for both paths, so a failure is reported for each.
    r = _verify(tmp_path, fake_app, dv=_GOOD_DV.replace("Timestamp=", "Signed Time="))
    assert "FAIL app:" in r.stderr and "FAIL Contents/Resources/bin/uv:" in r.stderr


# --- build_app.sh preflight ---------------------------------------------------------------


@pytest.fixture
def build_root(tmp_path: Path, bindir: Path) -> Path:
    """A copy of the scripts the preflight runs, in a root with its own .env, and a uv stub
    that marks the point where the build proper starts."""
    root = tmp_path / "repo"
    (root / "scripts/lib").mkdir(parents=True)
    for rel in ("build_app.sh", "resolve_release_identity.sh", "lib/release_env.sh"):
        shutil.copy(SCRIPTS / rel, root / "scripts" / rel)
        (root / "scripts" / rel).chmod(0o755)
    _stub(bindir, "uv", "echo BUILD_STARTED; exit 99\n")
    return root


def _build(root, bindir, tmp_path, listing=None, dotenv=None, **extra):
    if dotenv is not None:
        (root / ".env").write_text(dotenv)
    env = _env(
        bindir, tmp_path, listing if listing is not None else _listing((HASH_A, NAME_A)), **extra
    )
    return subprocess.run(
        ["/bin/bash", str(root / "scripts/build_app.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _started(r) -> bool:
    return "BUILD_STARTED" in r.stdout


def test_release_preflight_passes_and_resolves_the_identity(build_root, bindir, tmp_path):
    r = _build(build_root, bindir, tmp_path, dotenv=f"APPLE_TEAM_ID={TEAM}\n", SIGN_MODE="release")
    assert _started(r), r.stderr
    assert f"Developer ID identity {HASH_A} (team {TEAM})" in r.stdout


def test_release_preflight_takes_the_team_from_the_environment(build_root, bindir, tmp_path):
    r = _build(build_root, bindir, tmp_path, SIGN_MODE="release", APPLE_TEAM_ID=TEAM)
    assert _started(r), r.stderr


@pytest.mark.parametrize(
    ("dotenv", "extra", "message"),
    [
        pytest.param(None, {}, "APPLE_TEAM_ID is not set", id="no-team"),
        pytest.param("APPLE_TEAM_ID=\n", {}, "APPLE_TEAM_ID is not set", id="empty-team"),
        pytest.param("APPLE_TEAM_ID=abc\n", {}, "not a 10-character", id="bad-team"),
        pytest.param("garbage line\n", {}, ".env:1", id="bad-dotenv"),
        pytest.param(
            f"APPLE_TEAM_ID={TEAM}\n", {"CONFIG": "debug"}, "CONFIG=release", id="debug-config"
        ),
        pytest.param(
            f"APPLE_TEAM_ID={TEAM}\n", {"BUNDLE_ID": "com.example.x"}, "BUNDLE_ID", id="bundle-id"
        ),
        pytest.param(
            f"APPLE_TEAM_ID={OTHER_TEAM}\n", {}, "no valid Developer ID", id="no-identity"
        ),
        pytest.param(
            f"APPLE_TEAM_ID={TEAM}\n",
            {"SIGN_IDENTITY": "Code Signing"},
            "SIGN_IDENTITY matches no",
            id="self-signed",
        ),
    ],
)
def test_release_preflight_refuses_before_building(
    build_root, bindir, tmp_path, dotenv, extra, message
):
    r = _build(build_root, bindir, tmp_path, dotenv=dotenv, SIGN_MODE="release", **extra)
    assert r.returncode != 0
    assert not _started(r), "the build started before the preflight refused"
    assert message in r.stderr


def test_unknown_sign_mode_is_refused(build_root, bindir, tmp_path):
    r = _build(build_root, bindir, tmp_path, SIGN_MODE="Release")
    assert r.returncode != 0 and not _started(r)
    assert "SIGN_MODE must be dev or release" in r.stderr


def test_dev_mode_never_reads_dotenv_or_the_keychain(build_root, bindir, tmp_path):
    r = _build(build_root, bindir, tmp_path, dotenv="garbage line\n")
    assert _started(r), r.stderr
    assert not (tmp_path / "security-calls").exists()


def test_sign_modes_pick_the_timestamp_and_release_verifies():
    code = BUILD.read_text()
    dev = code[code.index("    dev)") : code.index("    release)")]
    release = code[code.index("    release)") : code.index("    *) die")]
    assert 'TIMESTAMP="--timestamp=none"' in dev
    assert 'TIMESTAMP="--timestamp"' in release
    signing = code[code.index('step "Signing') :]
    assert signing.count('"$TIMESTAMP"') == 2
    assert "--timestamp=none" not in signing
    assert 'verify_release_signature.sh" "$APP" "$APPLE_TEAM_ID"' in signing


def test_release_scripts_are_executable():
    for script in (RESOLVE, VERIFY, BUILD):
        assert os.access(script, os.X_OK), script
