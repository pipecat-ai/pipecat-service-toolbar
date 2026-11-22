"""Self-signed tester channel: the pinned-signer verifier in ``scripts/install_tester.sh``.

Plan: docs/plans/20261121-feature-selfsigned-tester-channel.md. Tests are hermetic (stub tools on
PATH, ``/bin/bash``) and run on both CI jobs. The ``real_codesign`` tests need macOS ``codesign``
and a throwaway keychain; they run in the ``macos-app`` job, under macOS Bash 3.2.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts/install_tester.sh"
BUNDLE_ID = "ai.pipecat.ServiceToolbar"

real_codesign = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("codesign") is None,
    reason="needs macOS codesign",
)


def _pin() -> str:
    match = re.search(r'^TESTER_CERT_SHA1="([^"]*)"$', INSTALLER.read_text(), re.M)
    assert match, "TESTER_CERT_SHA1 assignment not found"
    return match.group(1)


def _run(script: Path, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        env=env if env is not None else os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )


# -- static ------------------------------------------------------------------------------------


def _code_lines() -> list[str]:
    return [
        line
        for line in INSTALLER.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_pin_is_one_uppercase_sha1_constant():
    text = INSTALLER.read_text()
    assert len(re.findall(r"^TESTER_CERT_SHA1=", text, re.M)) == 1
    assert re.fullmatch(r"[0-9A-F]{40}", _pin())


def test_installer_has_no_eval_sudo_or_requirement_text_parse():
    code = "\n".join(_code_lines())
    assert not re.search(r"\beval\b", code)
    assert not re.search(r"\bsudo\b", code)
    # The designated requirement text is forgeable; only a `-R` verification may decide.
    assert not re.search(r"codesign\s+(-\w*\s+)*-d\w*\s", code)
    assert not re.search(r"\s-r-|\s-r\s+-|--requirements", code)
    assert re.search(r"codesign --verify --strict\s*\\?\s*-R", code)


def test_installer_runs_under_bash_3_2_syntax():
    # No bash 4+ features: associative arrays, mapfile, ${var,,}, ${var^^}, &>>.
    code = "\n".join(_code_lines())
    assert not re.search(r"declare\s+-A|\bmapfile\b|\breadarray\b|\$\{[^}]*(,,|\^\^)[^}]*\}", code)
    assert subprocess.run(["/bin/bash", "-n", str(INSTALLER)]).returncode == 0


# -- hermetic: stub codesign -------------------------------------------------------------------


@pytest.fixture
def stub_env(tmp_path):
    """PATH with a stub ``codesign`` that logs its arguments and fails on demand."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "codesign.log"
    stub = bin_dir / "codesign"
    stub.write_text(
        "#!/bin/bash\n"
        'echo "$*" >> "$STUB_LOG"\n'
        'case " $* " in\n'
        '  *" -R "*) exit "${STUB_R_RC:-0}" ;;\n'
        '  *) exit "${STUB_VERIFY_RC:-0}" ;;\n'
        "esac\n"
    )
    stub.chmod(0o755)
    app = tmp_path / "Pipecat Service Toolbar.app"
    app.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["STUB_LOG"] = str(log)
    return env, app, log


def test_verify_only_runs_the_pinned_requirement_check(stub_env):
    env, app, log = stub_env
    result = _run(INSTALLER, "--verify-only", str(app), env=env)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines[0] == f"--verify --deep --strict {app}"
    requirement = f'-R =identifier "{BUNDLE_ID}" and certificate leaf = H"{_pin()}"'
    assert lines[1] == f"--verify --strict {requirement} {app}"
    assert len(lines) == 2


def test_verify_only_refuses_a_broken_signature_without_asking_the_requirement(stub_env):
    env, app, log = stub_env
    env["STUB_VERIFY_RC"] = "1"
    result = _run(INSTALLER, "--verify-only", str(app), env=env)
    assert result.returncode == 1
    assert "-R" not in log.read_text()


def test_verify_only_refuses_another_signer(stub_env):
    env, app, _ = stub_env
    env["STUB_R_RC"] = "3"
    result = _run(INSTALLER, "--verify-only", str(app), env=env)
    assert result.returncode == 1
    assert "pinned tester certificate" in result.stderr


def test_verify_only_usage_errors(stub_env, tmp_path):
    env, app, log = stub_env
    assert _run(INSTALLER, env=env).returncode == 2
    assert _run(INSTALLER, "--verify-only", env=env).returncode == 2
    assert _run(INSTALLER, "--verify-only", str(app), "extra", env=env).returncode == 2
    missing = _run(INSTALLER, "--verify-only", str(tmp_path / "nope.app"), env=env)
    assert missing.returncode == 2
    assert not log.exists()


# -- real codesign (darwin only) ---------------------------------------------------------------

_OPENSSL_CONF = """\
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = Tester Channel Test Identity
[ext]
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
basicConstraints = critical, CA:false
"""


def _sh(*cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60, **kw)


@pytest.fixture(scope="module")
def throwaway_identity(tmp_path_factory):
    """A self-signed code-signing identity in a temporary keychain, never the login keychain."""
    root = tmp_path_factory.mktemp("identity")
    conf = root / "openssl.cnf"
    conf.write_text(_OPENSSL_CONF)
    key, cert, p12 = root / "k.pem", root / "c.pem", root / "t.p12"
    ssl = "/usr/bin/openssl"
    _sh(
        ssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "2",
        "-config",
        str(conf),
        "-keyout",
        str(key),
        "-out",
        str(cert),
    )
    _sh(
        ssl,
        "pkcs12",
        "-export",
        "-inkey",
        str(key),
        "-in",
        str(cert),
        "-out",
        str(p12),
        "-passout",
        "pass:pw",
    )
    fingerprint = _sh(ssl, "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha1").stdout
    sha1 = fingerprint.split("=", 1)[1].strip().replace(":", "").upper()
    keychain = root / "tmp.keychain-db"
    _sh("security", "create-keychain", "-p", "pw", str(keychain))
    _sh("security", "set-keychain-settings", "-t", "3600", str(keychain))
    _sh("security", "unlock-keychain", "-p", "pw", str(keychain))
    _sh("security", "import", str(p12), "-k", str(keychain), "-P", "pw", "-T", "/usr/bin/codesign")
    _sh(
        "security",
        "set-key-partition-list",
        "-S",
        "apple-tool:,apple:",
        "-s",
        "-k",
        "pw",
        str(keychain),
    )
    yield sha1, keychain
    subprocess.run(["security", "delete-keychain", str(keychain)], capture_output=True)


def _bundle(root: Path, name: str) -> Path:
    app = root / name / "Pipecat Service Toolbar.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    shutil.copy("/usr/bin/true", app / "Contents/MacOS/toolbar")
    (app / "Contents/Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        f"<key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>"
        "<key>CFBundleExecutable</key><string>toolbar</string>"
        "<key>CFBundleName</key><string>toolbar</string>"
        "<key>CFBundlePackageType</key><string>APPL</string>"
        "</dict></plist>\n"
    )
    return app


def _sign(app: Path, identity: str, *extra: str, keychain: Path | None = None) -> None:
    cmd = ["codesign", "--force", "--deep", "--sign", identity, *extra]
    if keychain:
        cmd += ["--keychain", str(keychain)]
    _sh(*cmd, str(app))


@pytest.fixture
def pinned_installer(throwaway_identity, tmp_path):
    """A temporary copy of the installer whose pin is the throwaway identity's SHA-1."""
    sha1, _ = throwaway_identity
    copy = tmp_path / "install_tester.sh"
    copy.write_text(
        re.sub(
            r'^TESTER_CERT_SHA1=".*"$',
            f'TESTER_CERT_SHA1="{sha1}"',
            INSTALLER.read_text(),
            flags=re.M,
        )
    )
    return copy


@real_codesign
def test_real_signer_is_accepted(throwaway_identity, pinned_installer, tmp_path):
    sha1, keychain = throwaway_identity
    app = _bundle(tmp_path, "good")
    _sign(app, sha1, keychain=keychain)
    result = _run(pinned_installer, "--verify-only", str(app))
    assert result.returncode == 0, result.stderr


@real_codesign
def test_real_ad_hoc_bundle_is_rejected(pinned_installer, tmp_path):
    app = _bundle(tmp_path, "adhoc")
    _sign(app, "-")
    assert _run(pinned_installer, "--verify-only", str(app)).returncode == 1


@real_codesign
def test_real_other_identity_is_rejected(throwaway_identity, tmp_path):
    """The committed pin (the owner's certificate) refuses a bundle from another identity."""
    sha1, keychain = throwaway_identity
    app = _bundle(tmp_path, "other")
    _sign(app, sha1, keychain=keychain)
    assert _run(INSTALLER, "--verify-only", str(app)).returncode == 1


@real_codesign
def test_real_forged_requirement_naming_the_pin_is_rejected(
    throwaway_identity, pinned_installer, tmp_path
):
    """An ad-hoc bundle whose custom designated requirement prints the pinned hash must fail.

    The requirement text names the pin, so a text parse would accept it; only ``-R`` rejects it.
    """
    sha1, _ = throwaway_identity
    app = _bundle(tmp_path, "forged")
    forged = f'designated => identifier "{BUNDLE_ID}" and certificate leaf = H"{sha1}"'
    _sign(app, "-", "-r", f"={forged}")
    shown = _sh("codesign", "-d", "-r-", str(app))
    assert sha1.lower() in (shown.stdout + shown.stderr).lower()  # the forgery looks right
    assert _run(pinned_installer, "--verify-only", str(app)).returncode == 1


@real_codesign
def test_real_tampered_bundle_is_rejected(throwaway_identity, pinned_installer, tmp_path):
    sha1, keychain = throwaway_identity
    app = _bundle(tmp_path, "tampered")
    _sign(app, sha1, keychain=keychain)
    (app / "Contents/MacOS/toolbar").write_bytes(b"changed after signing")
    assert _run(pinned_installer, "--verify-only", str(app)).returncode == 1
