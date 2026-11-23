"""Self-signed tester channel: the pinned-signer verifier in ``scripts/install_tester.sh``.

Plan: docs/plans/20261121-feature-selfsigned-tester-channel.md. Tests are hermetic (stub tools on
PATH, ``/bin/bash``) and run on both CI jobs. The ``real_codesign`` tests need macOS ``codesign``
and a throwaway keychain; they run in the ``macos-app`` job, under macOS Bash 3.2.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts/install_tester.sh"
PACKAGER = REPO_ROOT / "scripts/package_tester.sh"
JUSTFILE = REPO_ROOT / "justfile"
BUNDLE_ID = "ai.pipecat.ServiceToolbar"

real_codesign = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("codesign") is None,
    reason="needs macOS codesign",
)


def _pin() -> str:
    """The pin as the installer reports it (``--print-pin``), not a second parse of its source."""
    result = subprocess.run(
        ["/bin/bash", str(INSTALLER), "--print-pin"], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


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
    assert len(re.findall(r"^TESTER_CERT_SHA1=", text, re.MULTILINE)) == 1
    assert re.fullmatch(r"[0-9A-F]{40}", _pin())
    assert f'TESTER_CERT_SHA1="{_pin()}"' in text


@pytest.mark.parametrize(
    ("tag", "rc"),
    [
        ("tester-1.0.0-b1-abc1234", 0),
        ("tester-10.20.30-b999-0123456789ab", 0),
        ("tester-1.0-b1-abc1234", 1),
        ("tester-1.0.0-alpha.1-b1-abc1234", 1),
        ("tester-1.0.0-b1-ABCDEF1", 1),
        ("v1.0.0", 1),
    ],
)
def test_check_tag_mode_answers_with_the_installers_own_pattern(tag, rc):
    assert _run(INSTALLER, "--check-tag", tag).returncode == rc


def test_print_pin_and_check_tag_usage_errors():
    assert _run(INSTALLER, "--print-pin", "extra").returncode == 2
    assert _run(INSTALLER, "--check-tag").returncode == 2
    assert _run(INSTALLER, "--check-tag", "a", "b").returncode == 2


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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)
    if result.returncode != 0:
        raise AssertionError(
            f"{cmd[0]} exited {result.returncode}: {' '.join(cmd[1:4])}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


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
    # On a CI runner `codesign` does not find an identity in a keychain outside the search
    # list, so add it for the module's duration. Not done on a developer machine, where the
    # `--keychain` option is enough and the user's search list stays untouched.
    saved: list[str] = []
    if os.environ.get("CI"):
        listing = _sh("security", "list-keychains", "-d", "user").stdout
        saved = [line.strip().strip('"') for line in listing.splitlines() if line.strip()]
        _sh("security", "list-keychains", "-d", "user", "-s", str(keychain), *saved)
    yield sha1, keychain
    if saved:
        subprocess.run(
            ["security", "list-keychains", "-d", "user", "-s", *saved], capture_output=True
        )
    subprocess.run(["security", "delete-keychain", str(keychain)], capture_output=True)


# The main executable of a bundle the tests sign with a real identity and may run: a shell script,
# never a copy of a system binary. Apple's binaries carry an arm64e slice, and macOS 15 on Apple
# silicon (the macos-15 CI runner) kills an arm64e process whose signature is not Apple's
# (SIGKILL, exit -9). A script has no architecture slice, so it runs under the test signature on
# every Mac; codesign signs it as a generic bundle executable and seals it like a Mach-O one.
_RUNNABLE_MAIN = b"#!/bin/sh\nexit 0\n"


def _write_runnable_main(path: Path) -> None:
    path.write_bytes(_RUNNABLE_MAIN)
    path.chmod(0o755)


def _bundle(root: Path, name: str) -> Path:
    app = root / name / "Pipecat Service Toolbar.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    _write_runnable_main(app / "Contents/MacOS/toolbar")
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
    try:
        _sh(*cmd, str(app))
    except AssertionError as error:
        if keychain:
            diag = subprocess.run(
                ["security", "find-identity", "-p", "codesigning", str(keychain)],
                capture_output=True,
                text=True,
            )
            lists = subprocess.run(["security", "list-keychains"], capture_output=True, text=True)
            raise AssertionError(
                f"{error}\nfind-identity: {diag.stdout}{diag.stderr}\nsearch list: {lists.stdout}"
            ) from None
        raise


def _installer_pinned_to(sha1: str) -> str:
    """The installer's text with its one pin assignment set to ``sha1`` (test copies only)."""
    text, n = re.subn(
        r'^TESTER_CERT_SHA1=".*"$',
        f'TESTER_CERT_SHA1="{sha1}"',
        INSTALLER.read_text(),
        flags=re.MULTILINE,
    )
    assert n == 1, "the installer must assign TESTER_CERT_SHA1 exactly once"
    return text


@pytest.fixture
def pinned_installer(throwaway_identity, tmp_path):
    """A temporary copy of the installer whose pin is the throwaway identity's SHA-1."""
    sha1, _ = throwaway_identity
    copy = tmp_path / "install_tester.sh"
    copy.write_text(_installer_pinned_to(sha1))
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


# -- packager ----------------------------------------------------------------------------------

NAME = "Pipecat Service Toolbar.app"
VERSION, BUILD = "9.8.7", "321"

# STUB_DITTO_EXTRA adds a top-level entry to a written ZIP; STUB_DITTO_PLANT makes extraction
# write something the ZIP's central directory does not show (as real ditto can, from fields
# unzip does not read).
_DITTO_STUB = """#!/usr/bin/env python3
import os, shutil, sys, zipfile
a = sys.argv[1:]
if a[:3] == ["-c", "-k", "--keepParent"]:
    src, dst = a[3], a[4]
    base = os.path.basename(src.rstrip("/"))
    with zipfile.ZipFile(dst, "w") as z:
        for d, _, files in os.walk(src):
            z.write(d, os.path.join(base, os.path.relpath(d, src)))
            for f in files:
                p = os.path.join(d, f)
                z.write(p, os.path.join(base, os.path.relpath(p, src)))
        if os.environ.get("STUB_DITTO_EXTRA"):
            z.writestr(os.environ["STUB_DITTO_EXTRA"], b"x")
elif a[:2] == ["-x", "-k"]:
    if os.environ.get("STUB_DITTO_LOG"):
        with open(os.environ["STUB_DITTO_LOG"], "a") as log:
            log.write("extract\\n")
    with zipfile.ZipFile(a[2]) as z:
        z.extractall(a[3])
        for info in z.infolist():
            if info.create_system == 3 and (info.external_attr >> 16) & 0o170000 == 0o100000:
                os.chmod(os.path.join(a[3], info.filename), (info.external_attr >> 16) & 0o7777)
    plant = os.environ.get("STUB_DITTO_PLANT", "")
    app = os.path.join(a[3], "Pipecat Service Toolbar.app")
    if plant == "symlink":
        os.symlink("/Users", os.path.join(app, "Contents", "link"))
    elif plant == "fifo":
        os.mkfifo(os.path.join(app, "Contents", "pipe"))
    elif plant == "top-symlink":
        shutil.move(app, os.path.join(a[3], ".real"))
        os.symlink(".real", app)
    elif plant == "extra":
        open(os.path.join(a[3], "extra"), "w").close()
    elif plant == "loose":
        os.chmod(os.path.join(app, "Contents"), 0o777)
        os.chmod(os.path.join(app, "Contents", "Info.plist"), 0o4666)
else:
    sys.exit("ditto stub: unsupported " + " ".join(a))
"""

_CODESIGN_COUNTING_STUB = """#!/bin/bash
n=$(( $(cat "$STUB_COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_COUNT"
echo "$*" >> "$STUB_LOG"
if [ -n "${STUB_FAIL_FROM:-}" ] && [ "$n" -ge "$STUB_FAIL_FROM" ]; then exit 3; fi
exit 0
"""

_SECURITY_NO_IDENTITY = """#!/bin/bash
cat <<'OUT'
Policy: Code Signing
  Matching identities
     0 identities found

  Valid identities only
     0 valid identities found
OUT
"""

_BUILD_SENTINEL = """#!/bin/bash
touch "$(dirname "$0")/../built"
"""

# The installer strips access lists with macOS `chmod -N`; GNU chmod has no ACL flag. Off macOS
# only, this stub drops `-N` calls and passes every other call to the real chmod.
_CHMOD_NO_ACL_STUB = """#!/bin/bash
for a in "$@"; do [ "$a" = -N ] && exit 0; done
exec {real} "$@"
"""


def _host_stubs() -> dict[str, str]:
    if sys.platform == "darwin":
        return {}
    return {"chmod": _CHMOD_NO_ACL_STUB.format(real=shutil.which("chmod"))}


# STUB_LS_REMOTE_RC makes `git ls-remote` fail with that exit code, as a network or auth failure
# does; every other git call is the real git.
_GIT_STUB = """#!/bin/bash
if [ "$1" = ls-remote ] && [ -n "${{STUB_LS_REMOTE_RC:-}}" ]; then
  echo "fatal: unable to access origin: injected failure" >&2
  exit "$STUB_LS_REMOTE_RC"
fi
exec {real} "$@"
"""


def _git(cwd: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
    )
    out = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _write_bundle(root: Path, version: str = VERSION, build: str = BUILD, name: str = NAME) -> Path:
    app = root / name
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/MacOS/toolbar").write_bytes(b"binary")
    (app / "Contents/MacOS/toolbar").chmod(0o755)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": BUNDLE_ID,
                "CFBundleExecutable": "toolbar",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": build,
            }
        )
    )
    return app


class PackagerRepo:
    """A throwaway clone with a bare origin, holding copies of the scripts under test."""

    def __init__(self, tmp_path: Path, *, installer_text: str | None = None, stubs: bool = True):
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.root = tmp_path / "repo"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.origin))
        _git(tmp_path, "clone", "-q", str(self.origin), str(self.root))
        _git(self.root, "checkout", "-q", "-b", "main")
        (self.root / "scripts").mkdir()
        for name in ("package_tester.sh", "resolve_sign_identity.sh", "has_sign_identity.sh"):
            shutil.copy(REPO_ROOT / "scripts" / name, self.root / "scripts" / name)
        (self.root / "scripts/install_tester.sh").write_text(
            installer_text if installer_text is not None else INSTALLER.read_text()
        )
        (self.root / "scripts/build_app.sh").write_text(_BUILD_SENTINEL)
        for f in (self.root / "scripts").iterdir():
            f.chmod(0o755)
        (self.root / "app-version.json").write_text(
            json.dumps({"version": VERSION, "build": int(BUILD)})
        )
        (self.root / ".gitignore").write_text("dist/\nbuild/\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "init")
        _git(self.root, "push", "-q", "-u", "origin", "main")
        self.bundle = _write_bundle(tmp_path / "bundle")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "codesign.log"
        self.count = tmp_path / "codesign.count"
        self.stub("git", _GIT_STUB.format(real=shutil.which("git")))
        if stubs:
            self.stub("ditto", _DITTO_STUB)
            self.stub("codesign", _CODESIGN_COUNTING_STUB)
            self.stub("defaults", _DEFAULTS_STUB)
            for name, text in _host_stubs().items():
                self.stub(name, text)
        self.out = self.root / "dist/tester"

    def stub(self, name: str, text: str) -> None:
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            PATH=f"{self.bin}:{env['PATH']}",
            STUB_LOG=str(self.log),
            STUB_COUNT=str(self.count),
            TESTER_APP_BUNDLE=str(self.bundle),
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_SYSTEM="/dev/null",
        )
        env.update(extra)
        return {k: v for k, v in env.items() if v != "__unset__"}

    @property
    def argv(self) -> list[str]:
        return ["/bin/bash", str(self.root / "scripts/package_tester.sh")]

    def run(self, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.argv,
            cwd=self.root,
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=120,
        )

    @property
    def tag(self) -> str:
        return f"tester-{VERSION}-b{BUILD}-{_git(self.root, 'rev-parse', '--short=7', 'HEAD')}"

    def commit_file(
        self, name: str = "extra.txt", *, push: bool = False, branch: str | None = None
    ):
        if branch:
            _git(self.root, "checkout", "-q", "-b", branch)
        (self.root / name).write_text("x")
        _git(self.root, "add", name)
        _git(self.root, "commit", "-q", "-m", name)
        if push:
            _git(self.root, "push", "-q", "-u", "origin", "HEAD")


@pytest.fixture
def repo(tmp_path):
    return PackagerRepo(tmp_path)


def _recipe_body(name: str) -> str:
    text = JUSTFILE.read_text()
    match = re.search(rf"^{re.escape(name)}[^\n]*:\n((?:[ \t]+.*\n|\n)+)", text, re.MULTILINE)
    assert match, f"recipe {name} not found"
    return match.group(1)


def test_recipe_only_calls_the_packager():
    assert _recipe_body("app-tester-package").strip() == "scripts/package_tester.sh"


def test_packager_checks_before_building_and_verifies_before_zipping():
    code = PACKAGER.read_text()
    order = [
        "git status --porcelain",
        "git fetch --quiet --prune --no-prune-tags origin",
        'die "tag $TAG already exists',
        'resolve_sign_identity.sh" --require',
        'build_app.sh"',
        '--verify-only "$BUNDLE"',
        "ditto -c -k --keepParent",
        '--check-drop "$CHECK/$ZIP" "$TAG"',
        "shasum",
    ]
    positions = [code.index(item) for item in order]
    assert positions == sorted(positions), dict(zip(order, positions))


def test_packager_is_isolated_from_the_apple_plan():
    code = "\n".join(
        line for line in PACKAGER.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert 'TAG="tester-' in code
    assert not re.match(r"^v", re.search(r'TAG="([^"]*)"', code).group(1))
    assert not re.search(r">\s*\"?\$ROOT/app-version\.json|app-version\.json\"?\s*$", code)
    assert not re.search(r"\bgh\b|release-evidence|rulesets|\bapi\b", code)


def test_missing_identity_refuses_before_building(tmp_path):
    repo = PackagerRepo(tmp_path)
    repo.stub("security", _SECURITY_NO_IDENTITY)
    result = repo.run(TESTER_APP_BUNDLE="__unset__", SIGN_IDENTITY="__unset__")
    assert result.returncode != 0
    assert not (repo.root / "built").exists()
    assert not repo.out.exists()


def test_ad_hoc_identity_is_refused(repo):
    result = repo.run(TESTER_APP_BUNDLE="__unset__", SIGN_IDENTITY="-")
    assert result.returncode != 0
    assert "ad-hoc" in result.stderr
    assert not (repo.root / "built").exists()


def test_dirty_tree_is_refused(repo):
    (repo.root / "scratch.txt").write_text("untracked")
    result = repo.run()
    assert result.returncode != 0 and "not clean" in result.stderr
    assert not repo.out.exists()


def test_unpushed_head_is_refused(repo):
    repo.commit_file()
    result = repo.run()
    assert result.returncode != 0 and "not pushed" in result.stderr
    assert not repo.out.exists()


@pytest.mark.parametrize(
    "where", ["remote", "local", "local-with-prune-tags-config", "local-with-tag-fetch-mapping"]
)
def test_existing_tag_is_refused(repo, where):
    tag = repo.tag
    extra = {}
    if where == "remote":
        _git(repo.root, "tag", tag)
        _git(repo.root, "push", "-q", "origin", tag)
        _git(repo.root, "tag", "-d", tag)
    else:
        _git(repo.root, "tag", tag)
    if where == "local-with-prune-tags-config":
        # The packager's fetch prunes branches, never local tags, whatever fetch.pruneTags says.
        extra = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "fetch.pruneTags",
            "GIT_CONFIG_VALUE_0": "true",
        }
    if where == "local-with-tag-fetch-mapping":
        # A configured tag mapping stays active under --no-prune-tags and --prune would delete
        # the local tag, so the fetch names its own branch-only refspec.
        _git(repo.root, "config", "--add", "remote.origin.fetch", "+refs/tags/*:refs/tags/*")
    result = repo.run(**extra)
    assert result.returncode != 0 and "already exists" in result.stderr
    assert not repo.out.exists()
    if where != "remote":
        assert _git(repo.root, "tag", "--list", tag) == tag


def test_a_commit_only_on_a_branch_deleted_from_origin_is_not_pushed(repo):
    """A remote-tracking ref left by a deleted branch must not count as "pushed"."""
    repo.commit_file(push=True, branch="feature/gone")
    _git(repo.origin, "branch", "-D", "feature/gone")
    stale = "refs/remotes/origin/feature/gone"
    assert _git(repo.root, "for-each-ref", stale)  # the clone still holds the stale ref
    result = repo.run()
    assert result.returncode != 0 and "not pushed" in result.stderr, result.stderr
    assert not _git(repo.root, "for-each-ref", stale)
    assert not repo.out.exists()


def test_a_bundle_the_pin_rejects_is_never_zipped(repo):
    result = repo.run(STUB_FAIL_FROM="1")
    assert result.returncode != 0
    assert not repo.out.exists() or not list(repo.out.iterdir())


def test_a_zip_whose_unpacked_bundle_fails_is_not_published(repo):
    result = repo.run(STUB_FAIL_FROM="3")  # calls 1-2 verify the built bundle, 3-4 the unpacked one
    assert result.returncode != 0
    # The ZIP is verified in a scratch directory: nothing unverified is left for `gh release create`.
    assert not repo.out.exists() or not list(repo.out.iterdir())


def test_bundle_version_must_match_app_version_json(repo):
    shutil.rmtree(repo.bundle)
    repo.bundle = _write_bundle(repo.tmp / "bundle2", version="0.0.1")
    result = repo.run()
    assert result.returncode != 0 and "version" in result.stderr


def test_a_tag_the_installer_would_refuse_is_refused_before_building(repo):
    (repo.root / "app-version.json").write_text(
        json.dumps({"version": "1.0.0-alpha.1", "build": int(BUILD)})
    )
    _git(repo.root, "add", "app-version.json")
    _git(repo.root, "commit", "-q", "-m", "prerelease version")
    _git(repo.root, "push", "-q", "origin", "HEAD")
    result = repo.run()
    assert result.returncode != 0 and "install_tester.sh accepts" in result.stderr
    assert not repo.out.exists()


def test_bundle_must_carry_the_app_name(repo):
    repo.bundle = _write_bundle(repo.tmp / "bundle3", name="Other.app")
    result = repo.run()
    assert result.returncode != 0 and "must be named" in result.stderr


def _consistent(repo: PackagerRepo) -> dict:
    tag = repo.tag
    manifest = json.loads((repo.out / f"{tag}.manifest.json").read_text())
    zip_path = repo.out / f"{tag}.zip"
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert (repo.out / f"{tag}.zip.sha256").read_text() == f"{actual}  {tag}.zip\n"
    assert manifest["zip_sha256"] == actual and manifest["zip"] == zip_path.name
    commit = _git(repo.root, "rev-parse", "HEAD")
    assert manifest["commit"] == commit and tag.endswith(commit[:7])
    version = json.loads((repo.root / "app-version.json").read_text())
    assert manifest["version"] == version["version"] and manifest["build"] == version["build"]
    with zipfile.ZipFile(zip_path) as z:
        info = plistlib.loads(z.read(f"{NAME}/Contents/Info.plist"))
    assert info["CFBundleShortVersionString"] == manifest["version"]
    assert info["CFBundleVersion"] == str(manifest["build"])
    assert manifest["cert_sha1"] == _pin()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", manifest["created_utc"])
    return manifest


def test_success_writes_a_consistent_zip_checksum_and_manifest(repo):
    before = (repo.root / "app-version.json").read_text()
    result = repo.run()
    assert result.returncode == 0, result.stderr
    manifest = _consistent(repo)
    assert manifest["on_main"] is True and manifest["tag"] == repo.tag
    assert (repo.root / "app-version.json").read_text() == before
    assert _git(repo.root, "status", "--porcelain") == ""  # dist/ is ignored


def test_checksum_failure_leaves_no_partial_drop_or_damages_an_existing_one(repo):
    for existing in (False, True):
        if existing:
            (repo.bin / "shasum").unlink()
            result = repo.run()
            assert result.returncode == 0, result.stderr
        before = _snapshot(repo.out)
        repo.stub("shasum", "#!/bin/bash\nexit 1\n")
        result = repo.run()
        assert result.returncode == 1 and "cannot compute the ZIP checksum" in result.stderr
        assert _snapshot(repo.out) == before


# Logs each mv source to STUB_MV_LOG; fails a move whose source ends in STUB_MV_FAIL_SUFFIX.
_PKG_MV_STUB = """#!/bin/bash
printf '%s\\n' "$1" >> "$STUB_MV_LOG"
if [ -n "${STUB_MV_FAIL_SUFFIX:-}" ]; then
  case "$1" in *"$STUB_MV_FAIL_SUFFIX") echo "mv: injected failure" >&2; exit 1 ;; esac
fi
exec REAL_MV "$@"
"""


def test_a_failed_zip_move_never_leaves_a_mixed_or_partial_drop(repo):
    """Re-packaging a tag over an earlier drop: a failed move of the new ZIP must not leave the new
    checksum and manifest beside the old ZIP. Every publishing move is a same-volume rename out of
    a hidden scratch folder inside dist/tester, and the scratch folder never survives the run."""
    first = repo.run()
    assert first.returncode == 0, first.stderr
    log = repo.tmp / "mv.log"
    repo.stub("mv", _PKG_MV_STUB.replace("REAL_MV", shutil.which("mv")))
    failed = repo.run(STUB_MV_LOG=str(log), STUB_MV_FAIL_SUFFIX=".zip")
    assert failed.returncode != 0 and "injected failure" in failed.stderr
    assert sorted(p.name for p in repo.out.iterdir()) == [], failed.stderr
    sources = [Path(line) for line in log.read_text().splitlines()]
    assert sources and all(
        src.parent.parent == repo.out and src.parent.name.startswith(".tester-package.")
        for src in sources
    ), sources
    log.unlink()
    again = repo.run(STUB_MV_LOG=str(log))
    assert again.returncode == 0, again.stderr
    _consistent(repo)
    # The manifest, which marks a complete drop, is published last.
    assert [src.name for src in (Path(line) for line in log.read_text().splitlines())] == [
        f"{repo.tag}.zip",
        f"{repo.tag}.zip.sha256",
        f"{repo.tag}.manifest.json",
    ]
    assert sorted(p.name for p in repo.out.iterdir()) == sorted(
        [f"{repo.tag}.zip", f"{repo.tag}.zip.sha256", f"{repo.tag}.manifest.json"]
    )


# Moves like mv; the first move whose source ends in STUB_HOLD_SUFFIX touches STUB_HOLD_MARK once it
# has landed, then holds until STUB_HOLD_RELEASE exists (or STUB_HOLD_TICKS x 50 ms pass). A move
# whose source ends in STUB_MV_FAIL_SUFFIX fails.
_PKG_MV_HOLD_STUB = """#!/bin/bash
if [ -n "${STUB_MV_FAIL_SUFFIX:-}" ]; then
  case "$1" in *"$STUB_MV_FAIL_SUFFIX") echo "mv: injected failure" >&2; exit 1 ;; esac
fi
REAL_MV "$@"; rc=$?
case "$1" in
  *"${STUB_HOLD_SUFFIX:-//}")
    if [ ! -e "$STUB_HOLD_MARK" ]; then
      touch "$STUB_HOLD_MARK"; i=0
      while [ ! -e "${STUB_HOLD_RELEASE:-/nonexistent}" ] && [ "$i" -lt "${STUB_HOLD_TICKS:-200}" ]; do
        sleep 0.05; i=$((i + 1))
      done
    fi ;;
esac
exit "$rc"
"""


def _pkg_hold(repo: PackagerRepo, suffix: str, **extra: str) -> dict[str, str]:
    repo.stub("mv", _PKG_MV_HOLD_STUB.replace("REAL_MV", shutil.which("mv")))
    return {
        "STUB_HOLD_SUFFIX": suffix,
        "STUB_HOLD_MARK": str(repo.tmp / "hold.mark"),
        "STUB_HOLD_RELEASE": str(repo.tmp / "hold.release"),
        **extra,
    }


@pytest.mark.parametrize("first", ["fails", "succeeds"])
def test_a_second_run_never_publishes_while_the_first_is_publishing(repo, first):
    """Two runs for the same unpublished tag. The first holds between its publishing moves while
    the second reaches publication: the second refuses and names the lock, so the first one's
    cleanup cannot delete a drop the second published, and no drop mixes the two runs' files."""
    mark = repo.tmp / "hold.mark"
    env_a = repo.env(**_pkg_hold(repo, ".zip"))
    if first == "fails":
        env_a["STUB_MV_FAIL_SUFFIX"] = ".zip.sha256"
    run_a = subprocess.Popen(
        repo.argv,
        cwd=repo.root,
        env=env_a,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(800):
            if mark.exists() or run_a.poll() is not None:
                break
            time.sleep(0.025)
        assert mark.exists(), "the first run never reached publication"
        second = repo.run(STUB_HOLD_SUFFIX="//")  # the same mv stub, never holding
    finally:
        (repo.tmp / "hold.release").touch()
        out_a, err_a = run_a.communicate(timeout=120)
    lock = repo.out / ".tester-publish.lock"
    assert second.returncode != 0, second.stdout
    assert "another package_tester.sh run is publishing" in second.stderr
    assert str(lock) in second.stderr
    assert not lock.exists(), err_a
    assert not [p for p in repo.out.iterdir() if p.name.startswith(".tester-package.")]
    if first == "fails":
        assert run_a.returncode != 0 and "injected failure" in err_a
        assert list(repo.out.iterdir()) == [], err_a
        again = repo.run(STUB_HOLD_SUFFIX="//")
        assert again.returncode == 0, again.stderr
    else:
        assert run_a.returncode == 0, err_a
    _consistent(repo)


def test_a_stale_publication_lock_is_named_and_left(repo):
    lock = repo.out / ".tester-publish.lock"
    lock.mkdir(parents=True)
    result = repo.run()
    assert result.returncode != 0 and f"remove the folder {lock}" in result.stderr
    assert lock.is_dir()
    assert [p.name for p in repo.out.iterdir()] == [lock.name]


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_a_signal_during_publication_is_ignored_and_publication_completes(repo, sig):
    """Bash 3.2 runs the EXIT trap at once on a signal it does not trap, which removed published
    files while an mv still ran. Publication ignores HUP, INT and TERM until it is done."""
    env = repo.env(**_pkg_hold(repo, ".zip", STUB_HOLD_TICKS="20"))
    rc, out, err = _signal_when_held(repo.argv, env, sig, cwd=repo.root)
    assert rc == 0, err
    _consistent(repo)
    assert sorted(p.name for p in repo.out.iterdir()) == sorted(
        [f"{repo.tag}.zip", f"{repo.tag}.zip.sha256", f"{repo.tag}.manifest.json"]
    )


def test_a_pushed_branch_off_main_is_marked_rehearsal(repo):
    repo.commit_file(push=True, branch="feature/x")
    result = repo.run()
    assert result.returncode == 0, result.stderr
    assert _consistent(repo)["on_main"] is False
    assert "rehearsal" in result.stdout


def test_a_second_drop_needs_a_new_commit_and_gets_a_new_tag(repo):
    first = repo.run()
    assert first.returncode == 0, first.stderr
    first_tag = repo.tag
    _git(repo.root, "tag", first_tag)  # what `gh release create` leaves behind
    again = repo.run()
    assert again.returncode != 0 and "already exists" in again.stderr
    repo.commit_file(push=True)
    second = repo.run()
    assert second.returncode == 0, second.stderr
    assert repo.tag != first_tag
    _consistent(repo)


def test_packager_never_reads_the_pin_from_a_second_copy(repo):
    code = PACKAGER.read_text()
    assert len(re.findall(r"[0-9A-F]{40}", code)) == 0


def test_packager_asks_the_installer_instead_of_parsing_its_source():
    code = "\n".join(
        line for line in PACKAGER.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"\bsed\b|\bgrep\b|\bawk\b", code)
    assert '"$INSTALLER" --check-tag "$TAG"' in code
    assert '"$INSTALLER" --print-pin' in code


def test_a_zip_the_installer_would_refuse_is_never_published(repo):
    """The packager runs the installer's archive gate on the ZIP it wrote."""
    result = repo.run(STUB_DITTO_EXTRA="stray.txt")  # a top-level entry beside the app
    assert result.returncode != 0
    assert "outside" in result.stderr, result.stderr
    assert not repo.out.exists() or not list(repo.out.iterdir())


@pytest.mark.parametrize("rc", ["128", "1"])
def test_a_failed_remote_tag_lookup_refuses_instead_of_reading_as_absent(repo, rc):
    """`ls-remote --exit-code` exits 2 for "no such tag"; any other failure is no answer."""
    result = repo.run(STUB_LS_REMOTE_RC=rc)
    assert result.returncode != 0
    assert f"could not check whether tag {repo.tag} exists on origin" in result.stderr
    assert f"exited {rc}" in result.stderr and "injected failure" in result.stderr
    assert not repo.out.exists()


@pytest.mark.parametrize(
    ("plant", "message"),
    [("symlink", "other than files and folders"), ("extra", "more than the app")],
)
def test_the_packager_runs_the_installers_whole_acceptance_gate(repo, plant, message):
    """What extraction writes is checked as the installer checks it, not only the listing."""
    result = repo.run(STUB_DITTO_PLANT=plant)
    assert result.returncode != 0
    assert message in result.stderr, result.stderr
    assert not repo.out.exists() or not list(repo.out.iterdir())


@real_codesign
def test_real_packaging_end_to_end(throwaway_identity, tmp_path):
    """Real ditto and codesign: a bundle signed by the pinned identity packages and verifies."""
    sha1, keychain = throwaway_identity
    pinned = _installer_pinned_to(sha1)
    repo = PackagerRepo(tmp_path, installer_text=pinned, stubs=False)
    repo.bundle = _write_bundle(tmp_path / "signed")
    _sign(repo.bundle, sha1, keychain=keychain)
    result = repo.run()
    assert result.returncode == 0, result.stderr + result.stdout
    manifest = _consistent_real(repo, sha1)
    assert manifest["cert_sha1"] == sha1
    # The real ditto ZIP goes through the real installer (only curl and osascript stubbed).
    (tmp_path / "inst").mkdir()
    installer = tmp_path / "inst-installer.sh"
    installer.write_text(pinned)
    env = InstallEnv(tmp_path / "inst", installer=installer)
    for name in ("sysctl", "sw_vers", "uname", "defaults", "ditto", "codesign"):
        (env.bin / name).unlink()
    env.rename_tool.unlink()
    for suffix in (".zip", ".zip.sha256"):
        shutil.copy(repo.out / f"{repo.tag}{suffix}", env.release / f"{repo.tag}{suffix}")
    installed = env.run(repo.tag)
    assert installed.returncode == 0, installed.stderr + installed.stdout
    assert f"installed {repo.tag}" in installed.stdout
    # An ad-hoc bundle is refused and nothing is written.
    (tmp_path / "second").mkdir()
    repo2 = PackagerRepo(tmp_path / "second", installer_text=pinned, stubs=False)
    repo2.bundle = _write_bundle(tmp_path / "adhoc")
    _sign(repo2.bundle, "-")
    refused = repo2.run()
    assert refused.returncode != 0
    assert not repo2.out.exists() or not list(repo2.out.iterdir())


def _consistent_real(repo: PackagerRepo, sha1: str) -> dict:
    manifest = json.loads((repo.out / f"{repo.tag}.manifest.json").read_text())
    zip_path = repo.out / f"{repo.tag}.zip"
    assert manifest["zip_sha256"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert manifest["commit"] == _git(repo.root, "rev-parse", "HEAD")
    return manifest


# -- installer: install path -------------------------------------------------------------------

TAG = f"tester-{VERSION}-b{BUILD}-abc1234"
BASE_URL = "https://github.com/pipecat-ai/pipecat-service-toolbar/releases/download"

# STUB_CURL_SLEEP holds a download open (an overlapping run); STUB_CURL_RM removes a path, as
# something outside the installer might while it runs; STUB_CURL_HOOK runs a shell snippet once,
# on the first download, as another program might; STUB_CURL_DONE is touched as a call ends.
_CURL_STUB = """#!/bin/bash
echo "$*" >> "$STUB_CURL_LOG"
[ -z "${STUB_CURL_SLEEP:-}" ] || sleep "$STUB_CURL_SLEEP"
[ -z "${STUB_CURL_DONE:-}" ] || touch "$STUB_CURL_DONE"
[ -z "${STUB_CURL_RM:-}" ] || rm -rf "$STUB_CURL_RM"
if [ -n "${STUB_CURL_HOOK:-}" ] && [ ! -e "$STUB_CURL_LOG.hooked" ]; then
  touch "$STUB_CURL_LOG.hooked"; /bin/bash -c "$STUB_CURL_HOOK"
fi
out=""; url=""
while [ "$#" -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2 ;; -*) shift ;; *) url="$1"; shift ;; esac
done
file="$(basename "$url")"
case ",${STUB_CURL_FAIL:-}," in *",$file,"*|*,all,*) exit 22 ;; esac
[ -f "$STUB_RELEASE/$file" ] || exit 22
cp "$STUB_RELEASE/$file" "$out"
"""

_SYSCTL_STUB = """#!/bin/bash
if [ "${STUB_ARM:-1}" = none ]; then echo "sysctl: unknown oid 'hw.optional.arm64'" >&2; exit 1; fi
echo "${STUB_ARM:-1}"
"""

_SW_VERS_STUB = """#!/bin/bash
[ -z "${STUB_MACOS_FAIL:-}" ] || exit 1
echo "${STUB_MACOS-26.0}"
"""

_UNAME_STUB = """#!/bin/bash
echo "${STUB_UNAME:-arm64}"
"""

# STUB_OSA_RC set: answer exactly STUB_OSA_OUT on stdout and STUB_OSA_ERR on stderr, exit STUB_OSA_RC.
_OSASCRIPT_STUB = """#!/bin/bash
if [ "$1" = -l ]; then
  exec "${STUB_RENAME_TOOL:-/usr/bin/osascript}" "$@"
fi
n=$(( $(cat "$STUB_OSA_COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_OSA_COUNT"
if [ -n "${STUB_OSA_RC:-}" ]; then
  printf '%s' "${STUB_OSA_OUT:-}"; printf '%s' "${STUB_OSA_ERR:-}" >&2; exit "$STUB_OSA_RC"
fi
[ -z "${STUB_OSA_FAIL:-}" ] || exit 1
if [ -n "${STUB_OSA_UNKNOWN:-}" ]; then
  echo "syntax error: Can\u2019t get application id \"ai.pipecat.ServiceToolbar\". (-1728)" >&2
  exit 1
fi
if [ "$n" -gt 1 ] && [ -n "${STUB_RUNNING_LATER:-}" ]; then echo "$STUB_RUNNING_LATER"; exit 0; fi
echo "${STUB_RUNNING:-false}"
"""

_DEFAULTS_STUB = """#!/usr/bin/env python3
import plistlib, sys
assert sys.argv[1] == "read", sys.argv
try:
    with open(sys.argv[2] + ".plist", "rb") as f:
        print(plistlib.load(f)[sys.argv[3]])
except Exception:
    sys.exit(1)
"""

# `Contents/SIGNER` stands in for a signature: good, bad (another signer) or broken.
_CODESIGN_MARKER_STUB = """#!/bin/bash
n=$(( $(cat "$STUB_COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_COUNT"
echo "$*" >> "$STUB_LOG"
if [ "${STUB_CODESIGN_HOOK_AT:-}" = "$n" ]; then /bin/bash -c "$STUB_CODESIGN_HOOK"; fi
[ "${STUB_FAIL_ON_CALL:-0}" != "$n" ] || exit 3
app="${@: -1}"
signer="$(cat "$app/Contents/SIGNER" 2>/dev/null)"
case " $* " in
  *" -R "*) [ "$signer" = good ] || exit 3 ;;
  *) [ "$signer" != broken ] || exit 1 ;;
esac
exit 0
"""

# The installer's exclusive rename (`osascript -l JavaScript` calling renamex_np), counted: the
# osascript stub hands `-l` calls to this tool, which is not on PATH. STUB_RENAME_FAIL lists the
# calls that fail. STUB_RENAME_HOOK runs at call STUB_RENAME_HOOK_AT, before the real rename (or
# after it, with STUB_RENAME_HOOK_AFTER set), with the rename's arguments (source "${@: -2:1}",
# destination "${@: -1}"); STUB_RENAME_SLEEP_AT holds call n for STUB_RENAME_SLEEP seconds,
# touching STUB_RENAME_MARK first. Off macOS, renameat2(RENAME_NOREPLACE) stands in for
# renamex_np(RENAME_EXCL): the kernel refuses an occupied destination on both.
_RENAME_STUB = """#!/bin/bash
n=$(( $(cat "$STUB_RENAME_COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_RENAME_COUNT"
case ",${STUB_RENAME_FAIL:-}," in *",$n,"*) echo "rename: injected failure $n" >&2; exit 1 ;; esac
if [ "${STUB_RENAME_SLEEP_AT:-}" = "$n" ]; then touch "$STUB_RENAME_MARK"; sleep "$STUB_RENAME_SLEEP"; fi
if [ "${STUB_RENAME_HOOK_AT:-}" = "$n" ] && [ -z "${STUB_RENAME_HOOK_AFTER:-}" ]; then
  /bin/bash -c "$STUB_RENAME_HOOK" hook "$@"
fi
if [ -x /usr/bin/osascript ]; then
  /usr/bin/osascript "$@"; rc=$?
else
  python3 -c '
import ctypes, errno, os, sys
s, d = (os.fsencode(p) for p in sys.argv[-2:])
libc = ctypes.CDLL(None, use_errno=True)
if hasattr(libc, "renameat2"):
    if libc.renameat2(-100, s, -100, d, 1) == 0:  # AT_FDCWD, RENAME_NOREPLACE
        sys.exit(0)
    if ctypes.get_errno() not in (errno.EINVAL, errno.ENOSYS):  # the file system lacks the flag
        sys.exit(1)
sys.exit(1) if os.path.lexists(d) else os.rename(s, d)
' "$@"; rc=$?
fi
if [ "${STUB_RENAME_HOOK_AT:-}" = "$n" ] && [ -n "${STUB_RENAME_HOOK_AFTER:-}" ]; then
  /bin/bash -c "$STUB_RENAME_HOOK" hook "$@"
fi
exit "$rc"
"""

_DITTO_EXTRACT_FAIL = _DITTO_STUB.replace(
    "a = sys.argv[1:]",
    'a = sys.argv[1:]\nif os.environ.get("STUB_DITTO_FAIL"): sys.exit(1)',
)


def _snapshot(root: Path) -> dict[str, str]:
    """Every path under ``root`` with its mode, file flags and content digest (or symlink
    target), to prove nothing else changed."""
    snap: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        st = path.lstat()
        meta = f"{st.st_mode:o}:{getattr(st, 'st_flags', 0):x}:"
        if path.is_symlink():
            snap[rel] = meta + "link:" + os.readlink(path)
        elif path.is_dir():
            snap[rel] = meta + "dir"
        else:
            snap[rel] = meta + hashlib.sha256(path.read_bytes()).hexdigest()
    return snap


def _make_zip(path: Path, entries: list[tuple[str, bytes | None, str]]) -> None:
    """entries: (arcname, data, kind) where kind is file or symlink; data None means a directory."""
    with zipfile.ZipFile(path, "w") as z:
        for name, data, kind in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            if data is None:
                info.external_attr = (0o40755 << 16) | 0x10
                z.writestr(info, b"")
            elif kind == "symlink":
                info.external_attr = 0o120777 << 16
                z.writestr(info, data)
            else:
                info.external_attr = (0o100755 if kind == "executable" else 0o100644) << 16
                z.writestr(info, data)


def _bundle_entries(signer: str = "good", version: str = VERSION, build: str = BUILD, extra=()):
    plist = plistlib.dumps(
        {
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": "toolbar",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": build,
        }
    )
    entries = [
        (f"{NAME}/", None, "file"),
        (f"{NAME}/Contents/", None, "file"),
        (f"{NAME}/Contents/Info.plist", plist, "file"),
        (f"{NAME}/Contents/SIGNER", signer.encode(), "file"),
        (f"{NAME}/Contents/MacOS/", None, "file"),
        (f"{NAME}/Contents/MacOS/toolbar", b"binary", "executable"),
    ]
    return entries + list(extra)


class InstallEnv:
    def __init__(self, tmp_path: Path, *, installer: Path = INSTALLER):
        self.tmp = tmp_path
        self.installer = installer
        self.home = tmp_path / "home"
        self.dest = self.home / "Applications"
        self.dest.mkdir(parents=True)
        self.release = tmp_path / "release"
        self.release.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, text in {
            "curl": _CURL_STUB,
            "sysctl": _SYSCTL_STUB,
            "sw_vers": _SW_VERS_STUB,
            "uname": _UNAME_STUB,
            "osascript": _OSASCRIPT_STUB,
            "defaults": _DEFAULTS_STUB,
            "ditto": _DITTO_EXTRACT_FAIL,
            "codesign": _CODESIGN_MARKER_STUB,
            **_host_stubs(),
        }.items():
            path = self.bin / name
            path.write_text(text)
            path.chmod(0o755)
        # Not on PATH: only the osascript stub's `-l` (exclusive rename) calls reach it.
        self.rename_tool = tmp_path / "stubs/rename"
        self.rename_tool.parent.mkdir()
        self.rename_tool.write_text(_RENAME_STUB)
        self.rename_tool.chmod(0o755)
        self.target = self.dest / NAME
        self.logs = {n: tmp_path / f"{n}.log" for n in ("curl", "codesign", "ditto")}

    def publish(self, tag: str = TAG, entries=None, *, sum_text: str | None = None) -> Path:
        zip_path = self.release / f"{tag}.zip"
        _make_zip(zip_path, entries if entries is not None else _bundle_entries())
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        (self.release / f"{tag}.zip.sha256").write_text(
            sum_text if sum_text is not None else f"{digest}  {tag}.zip\n"
        )
        return zip_path

    def install_old(self, version: str = "0.0.1", build: str = "1") -> None:
        _write_bundle(self.dest, version=version, build=build)
        (self.target / "Contents/SIGNER").write_text("good")

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            PATH=f"{self.bin}:{env['PATH']}",
            HOME=str(self.home),
            STUB_RELEASE=str(self.release),
            STUB_CURL_LOG=str(self.logs["curl"]),
            STUB_LOG=str(self.logs["codesign"]),
            STUB_COUNT=str(self.tmp / "codesign.count"),
            STUB_DITTO_LOG=str(self.logs["ditto"]),
            STUB_OSA_COUNT=str(self.tmp / "osa.count"),
            STUB_RENAME_COUNT=str(self.tmp / "rename.count"),
        )
        if self.rename_tool.exists():
            env["STUB_RENAME_TOOL"] = str(self.rename_tool)
        env.pop("APP_INSTALL_DIR", None)
        env.update(extra)
        return env

    def run(self, tag: str = TAG, *, stdin: bool = False, **extra: str):
        env = self.env(**extra)
        if stdin:
            return subprocess.run(
                f'cat "{self.installer}" | /bin/bash -s -- {tag}',
                shell=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        return _run(self.installer, tag, env=env)


@pytest.fixture
def inst(tmp_path):
    assert shutil.which("unzip"), "unzip is required by the installer and its tests"
    return InstallEnv(tmp_path)


def _staging_left(env: InstallEnv) -> list[str]:
    return [p.name for p in env.dest.iterdir() if p.name.startswith(".tester-install")]


def test_fresh_install_succeeds_and_leaves_one_app_and_no_staging(inst):
    inst.publish()
    result = inst.run()
    assert result.returncode == 0, result.stderr
    assert f"installed {TAG}: version {VERSION}, build {BUILD}" in result.stdout
    assert [p.name for p in inst.dest.iterdir()] == [NAME]
    assert "replaced" not in result.stdout


def test_install_reads_the_script_from_stdin_as_curl_pipe_bash_does(inst):
    inst.publish()
    result = inst.run(stdin=True)
    assert result.returncode == 0, result.stderr
    assert inst.target.is_dir()


def test_default_install_directory_is_home_applications(inst):
    inst.publish()
    assert inst.run().returncode == 0
    assert (inst.home / "Applications" / NAME).is_dir()


@pytest.mark.parametrize("relative", [False, True])
def test_app_install_dir_is_honoured(inst, tmp_path, monkeypatch, relative):
    custom = tmp_path / "custom"
    inst.publish()
    monkeypatch.chdir(tmp_path)
    if sys.platform == "darwin":
        (inst.bin / "defaults").unlink()
    result = inst.run(APP_INSTALL_DIR="custom" if relative else str(custom))
    assert result.returncode == 0, result.stderr
    assert (custom / NAME).is_dir() and not inst.target.exists()


def test_update_replaces_the_app_and_reports_old_and_new(inst):
    inst.install_old()
    inst.publish()
    result = inst.run()
    assert result.returncode == 0, result.stderr
    assert f"installed {TAG}: version {VERSION}, build {BUILD}" in result.stdout
    assert "replaced version 0.0.1, build 1" in result.stdout
    assert {p.name for p in inst.dest.iterdir()} == {NAME, ".tester-previous"}
    assert (inst.target / "Contents/SIGNER").read_text() == "good"
    new = plistlib.loads((inst.target / "Contents/Info.plist").read_bytes())
    assert new["CFBundleVersion"] == BUILD


@pytest.mark.parametrize(
    "tag",
    [
        "tester-1.0-b1-abc1234",
        "tester-1.0.0-b1-ABCDEF1",
        "tester-1.0.0-b1-abc",
        "tester-1.0.0-b1-abc1234/../x",
        "tester-1.0.0-bX-abc1234",
        "tester-1.0.0-b1-abc1234 extra",
    ],
)
def test_bad_tags_are_refused_before_anything_happens(inst, tag):
    before = _snapshot(inst.home)
    result = inst.run(tag)
    assert result.returncode != 0 and "not a tester tag" in result.stderr
    assert _snapshot(inst.home) == before
    assert not inst.logs["curl"].exists()


@pytest.mark.parametrize("args", [[], ["v1.0.0"], ["--verify-only"], [TAG, "extra"]])
def test_other_argument_shapes_are_usage_errors(inst, args):
    result = _run(inst.installer, *args, env=inst.env())
    assert result.returncode == 2 and "usage" in result.stderr


@pytest.mark.parametrize("arm", ["0", "none"])
def test_intel_is_refused(inst, arm):
    inst.publish()
    result = inst.run(STUB_ARM=arm)
    assert result.returncode != 0 and "Apple silicon" in result.stderr
    assert not inst.logs["curl"].exists()


def test_apple_silicon_under_rosetta_is_not_refused(inst):
    inst.publish()
    result = inst.run(STUB_UNAME="x86_64")  # `uname -m` lies under Rosetta; sysctl does not
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("version", "ok"), [("14.7.1", False), ("15.0", True), ("26.6.2", True), ("", False)]
)
def test_macos_version_gate(inst, version, ok):
    inst.publish()
    result = inst.run(STUB_MACOS=version)
    assert (result.returncode == 0) is ok, result.stderr
    if not ok:
        assert not inst.logs["curl"].exists()


@pytest.mark.parametrize("fail", ["all", f"{TAG}.zip", f"{TAG}.zip.sha256"])
def test_interrupted_download_changes_nothing(inst, fail):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run(STUB_CURL_FAIL=fail)
    assert result.returncode != 0 and "could not download" in result.stderr
    assert _snapshot(inst.home) == before


def test_checksum_mismatch_is_refused(inst):
    inst.install_old()
    inst.publish(sum_text=f"{'0' * 64}  {TAG}.zip\n")
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and "corrupt" in result.stderr
    assert _snapshot(inst.home) == before


@pytest.mark.parametrize("sum_text", ["", "not-a-hash  x.zip\n", "ABC  x.zip\n"])
def test_malformed_checksum_file_is_refused(inst, sum_text):
    inst.publish(sum_text=sum_text)
    result = inst.run()
    assert result.returncode != 0 and "malformed" in result.stderr


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ([("Other.app/Contents/x", b"x", "file")], "outside"),
        ([("../escape", b"x", "file")], "outside"),
        ([(f"{NAME}/../../escape", b"x", "file")], "unsafe"),
        ([("/abs/path", b"x", "file")], "outside"),
        ([(f"{NAME}/Contents/link", b"/Users", "symlink")], "symbolic link"),
    ],
)
def test_archive_shape_is_checked_before_extraction(inst, extra, message):
    inst.install_old()
    inst.publish(entries=_bundle_entries(extra=extra))
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and message in result.stderr, result.stderr
    assert _snapshot(inst.home) == before


def test_a_symlink_is_refused_in_an_archive_longer_than_a_pipe_buffer(inst):
    """`unzip -Z | grep -q` under pipefail misses an early symlink once the listing is large."""
    link = [(f"{NAME}/Contents/link", b"/Users", "symlink")]
    filler = [
        (f"{NAME}/Contents/Resources/some/long/path/for/an/entry/file_{n:05d}.txt", b"x", "file")
        for n in range(3000)
    ]
    inst.install_old()
    inst.publish(entries=_bundle_entries(extra=link + filler))
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and "symbolic link" in result.stderr, result.stderr
    assert _snapshot(inst.home) == before


def test_wrong_signer_is_refused_and_the_installed_app_is_untouched(inst):
    inst.install_old()
    inst.publish(entries=_bundle_entries(signer="bad"))
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and "pinned tester certificate" in result.stderr
    assert _snapshot(inst.home) == before


def test_broken_signature_is_refused(inst):
    inst.publish(entries=_bundle_entries(signer="broken"))
    result = inst.run()
    assert result.returncode != 0
    assert not inst.target.exists() and _staging_left(inst) == []


def test_an_app_that_does_not_match_its_tag_is_refused(inst):
    inst.install_old()
    inst.publish(entries=_bundle_entries(version="1.2.3"))
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and "the tag names" in result.stderr
    assert _snapshot(inst.home) == before


@pytest.mark.parametrize(
    "extra",
    [
        {"STUB_RUNNING": "true"},
        {"STUB_OSA_FAIL": "1"},
        {"STUB_RUNNING": "maybe"},
        {"STUB_RUNNING_LATER": "true"},
    ],
    ids=["running", "probe-fails", "probe-garbage", "started-during-download"],
)
def test_a_running_or_unknowable_app_is_never_replaced(inst, extra):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run(**extra)
    assert result.returncode != 0
    assert ("quit" in result.stderr) or ("could not tell" in result.stderr)
    assert _snapshot(inst.home) == before


def test_an_app_launch_services_has_never_seen_is_not_running(inst):
    """A first install: osascript fails with -1728, which quit_app.sh also reads as not running."""
    inst.publish()
    result = inst.run(STUB_OSA_UNKNOWN="1")
    assert result.returncode == 0, result.stderr
    assert inst.target.is_dir() and _staging_left(inst) == []


def test_a_failed_staging_copy_leaves_the_installed_app(inst):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run(STUB_DITTO_FAIL="1")
    assert result.returncode != 0
    assert _snapshot(inst.home) == before


def test_failing_to_move_the_old_app_aside_leaves_it_and_names_app_management(inst):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run(STUB_RENAME_FAIL="1")
    assert result.returncode != 0 and "App Management" in result.stderr
    # The workaround removes the rollback copy too: with only the app gone, the next run restores
    # .tester-previous as the app and stops at the same move.
    assert (
        f"remove both the app and its rollback copy {_prev(inst)} (if present) by hand"
        in result.stderr
    )
    assert "nothing was changed" in result.stderr
    assert _snapshot(inst.home) == before


def test_failing_to_move_the_new_app_in_restores_the_previous_copy(inst):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run(STUB_RENAME_FAIL="2")
    assert result.returncode != 0 and "previous copy was restored" in result.stderr
    assert _snapshot(inst.home) == before


def test_failing_to_move_the_new_app_in_on_a_first_install_claims_no_restore(inst):
    inst.publish()
    result = inst.run(STUB_RENAME_FAIL="1")  # no old app, so the first rename is the move-in
    assert result.returncode != 0 and "could not install" in result.stderr
    assert "was restored" not in result.stderr and "no previous copy" in result.stderr
    assert [p.name for p in inst.dest.iterdir()] == []


def _leftovers(env: InstallEnv) -> list[Path]:
    return [p for p in env.dest.iterdir() if p.name.startswith(".tester-leftover.")]


def _outside_leftovers(snap: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in snap.items() if ".tester-leftover." not in k}


def test_a_failed_verification_after_the_swap_restores_the_previous_copy(inst):
    """The copy that failed is never left at the app path and never deleted (owner rule)."""
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    # codesign calls 1-2 verify the staged copy; call 3 is the post-swap check of the target.
    result = inst.run(STUB_FAIL_ON_CALL="3")
    assert result.returncode != 0 and "previous copy was restored" in result.stderr
    assert _outside_leftovers(_snapshot(inst.home)) == before
    (kept,) = _leftovers(inst)
    assert _build_at(kept / "rejected") == BUILD
    assert f"kept at {kept / 'rejected'}; do not open it" in result.stderr, result.stderr


def test_a_failed_first_install_leaves_no_app_and_keeps_the_failed_copy_aside(inst):
    inst.publish()
    result = inst.run(STUB_FAIL_ON_CALL="3")
    assert result.returncode != 0 and "no previous copy" in result.stderr
    (kept,) = _leftovers(inst)
    assert [p.name for p in inst.dest.iterdir()] == [kept.name]
    assert _bundles(inst) == {kept / "rejected": BUILD}
    assert "do not open it" in result.stderr


def test_an_interrupted_swap_is_recovered_on_the_next_run(inst):
    inst.install_old()
    (inst.target).rename(inst.dest / ".tester-previous")  # killed after moving the old app aside
    inst.publish()
    result = inst.run(STUB_CURL_FAIL="all")  # recovery happens before any download
    assert result.returncode != 0
    assert (inst.target / "Contents/Info.plist").is_file()
    assert not (inst.dest / ".tester-previous").exists()
    assert _staging_left(inst) == []


def test_a_stale_previous_copy_is_cleared_when_the_app_is_present(inst):
    inst.install_old()
    stale = _write_bundle(inst.tmp / "stale", version="0.0.0", build="0")
    shutil.copytree(stale, inst.dest / ".tester-previous")
    inst.publish()
    result = inst.run()
    assert result.returncode == 0, result.stderr
    assert _bundles(inst) == {inst.target: BUILD, _prev(inst): "1"}


def test_nothing_outside_the_app_and_backup_changes_on_success(inst):
    inst.install_old()
    (inst.home / "Library").mkdir()
    (inst.home / "Library/prefs.plist").write_text("keep")
    (inst.dest / "Other.app").mkdir()
    (inst.dest / f"{NAME}.unrelated").write_text("keep")
    inst.publish()
    before = _snapshot(inst.home)
    assert inst.run().returncode == 0
    after = _snapshot(inst.home)

    def outside(snap):
        return {
            k: v
            for k, v in snap.items()
            if Path(k).parts[:2]
            not in {("Applications", NAME), ("Applications", ".tester-previous")}
        }

    assert outside(after) == outside(before)


def test_the_home_snapshot_notices_a_mode_or_flag_change(inst):
    """The isolation checks compare modes too: a run that only removed write permission from an
    unrelated file must not pass as "nothing changed"."""
    (inst.home / "Library").mkdir()
    prefs = inst.home / "Library/prefs.plist"
    prefs.write_text("keep")
    before = _snapshot(inst.home)
    prefs.chmod(0o444)
    assert _snapshot(inst.home) != before
    prefs.chmod(0o644)
    assert _snapshot(inst.home) == before
    if hasattr(os, "chflags"):
        os.chflags(prefs, stat.UF_HIDDEN)
        assert _snapshot(inst.home) != before


def test_installer_fetches_only_the_fixed_release_host_with_hardening_flags(inst):
    inst.publish()
    assert inst.run().returncode == 0
    lines = inst.logs["curl"].read_text().splitlines()
    assert len(lines) == 2
    for line, suffix in zip(lines, (".zip", ".zip.sha256")):
        assert f"{BASE_URL}/{TAG}/{TAG}{suffix}" in line
        for flag in (
            "-q",
            "--fail",
            "--proto =https",
            "--proto-redir =https",
            "--max-redirs 5",
            "--max-filesize",
            "--max-time",
        ):
            assert flag in line, (flag, line)
    code = "\n".join(_code_lines())
    assert (
        code.count("https://") == 1
        and "https://github.com/pipecat-ai/pipecat-service-toolbar" in code
    )
    assert not re.search(r"\buname\b", code)
    assert "sysctl -n hw.optional.arm64" in code
    assert 'main "$@" </dev/null' in code
    assert "http://" not in code


# -- installer: one run at a time, and the last good copy survives -----------------------------
#
# Contract: the last verified-good app copy (the app once it verifies, otherwise .tester-previous)
# is never deleted while the installed copy is unverified, and only one installer runs per
# install directory. A copy is restored to, kept at or left at the app path only once it verifies
# there, and the rollback copy is always one that verified. Start-up state space: app
# absent/good/bad x previous absent/good/bad x lock free/held; then every failure point of the
# swap, followed by a retry.

LOCK_NAME = ".tester-install.lock"


def _prev(env: InstallEnv) -> Path:
    return env.dest / ".tester-previous"


def _build_at(app: Path) -> str | None:
    plist = app / "Contents/Info.plist"
    return plistlib.loads(plist.read_bytes())["CFBundleVersion"] if plist.is_file() else None


def _place(env: InstallEnv, where: Path, signer: str, build: str) -> None:
    app = _write_bundle(env.tmp / f"src-{where.name}", version=f"0.0.{build}", build=build)
    (app / "Contents/SIGNER").write_text(signer)
    shutil.move(str(app), str(where))


def _stray(env: InstallEnv, kind: str) -> Path | None:
    """What an earlier run can leave beside the app: a staging folder or a kept copy."""
    if kind == "none":
        return None
    path = env.dest / (".tester-install.Ab12Cd" if kind == "staging" else ".tester-leftover.Ab12Cd")
    _place(env, path, "good", "5") if kind == "leftover" else path.mkdir()
    if kind == "staging":
        (path / "drop.zip").write_bytes(b"partial")
    return path


@pytest.mark.parametrize("stray", ["none", "staging", "leftover"])
@pytest.mark.parametrize("lock", ["free", "held"])
@pytest.mark.parametrize("prev", ["absent", "good", "bad"])
@pytest.mark.parametrize("app", ["absent", "good", "bad"])
def test_start_up_state_space(inst, app, prev, lock, stray):
    if app != "absent":
        _place(inst, inst.target, app, "1")
    if prev != "absent":
        _place(inst, _prev(inst), prev, "2")
    if lock == "held":
        (inst.dest / LOCK_NAME).mkdir()
    left = _stray(inst, stray)
    left_snap = _snapshot(left) if left else None
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run()
    if left:
        # Named on every outcome, and never removed or changed.
        assert str(left) in result.stderr, result.stderr
        assert left.is_dir() and _snapshot(left) == left_snap
    if lock == "held":
        assert result.returncode != 0
        assert "another tester install" in result.stderr
        assert str(inst.dest / LOCK_NAME) in result.stderr
        assert _snapshot(inst.home) == before  # the other run's lock and copies stay
        assert not inst.logs["curl"].exists()
        return
    assert not (inst.dest / LOCK_NAME).exists()
    if app != "good" and prev != "absent" and (app, prev) != ("absent", "good"):
        # No verified app: a previous copy counts only if it verifies itself. Nothing moves, and
        # the previous copy is named; an unverified one is never offered as a way back.
        assert result.returncode != 0
        assert str(_prev(inst)) in result.stderr and "nothing was changed" in result.stderr
        assert _snapshot(inst.home) == before
        assert not inst.logs["curl"].exists()
        if prev == "bad":
            assert "do not open" in result.stderr.lower() and "rename" not in result.stderr
        return
    assert result.returncode == 0, result.stderr
    expected = {NAME}
    if app == "good" or prev == "good":
        expected.add(".tester-previous")
    others = [p for p in inst.dest.iterdir() if p != left]
    kept = [p for p in others if p.name.startswith(".tester-leftover.")]
    assert {p.name for p in others if p not in kept} == expected
    assert _build_at(inst.target) == BUILD
    if ".tester-previous" in expected:
        # The rollback copy is always one that verified when it was saved.
        assert (_prev(inst) / "Contents/SIGNER").read_text() == "good"
        assert _build_at(_prev(inst)) == ("1" if app == "good" else "2")
    if app == "bad":
        # The replaced build did not verify: kept aside and named, not as the rollback copy.
        (folder,) = kept
        assert _bundles(inst).get(folder / "unverified-previous") == "1"
        assert f"kept at {folder / 'unverified-previous'}" in result.stderr, result.stderr
        assert "do not open it" in result.stderr
    else:
        assert kept == []
    if app == "absent" and prev == "good":
        assert "restored the previous copy" in result.stdout


def _good_copy_left(env: InstallEnv) -> bool:
    """A copy that passes the (stub) pin check is at the app path, or the old one is kept."""
    for app in (env.target, _prev(env)):
        signer = app / "Contents/SIGNER"
        if signer.is_file() and signer.read_text() == "good":
            return True
    return False


@pytest.mark.parametrize(
    ("inject", "ok", "app_build", "prev_build"),
    [
        ({"STUB_RENAME_FAIL": "1"}, False, "1", None),  # moving the old app aside
        ({"STUB_RENAME_FAIL": "2"}, False, "1", None),  # moving the new app in; restored
        ({"STUB_RENAME_FAIL": "2,3"}, False, None, "1"),  # ... and the restore fails
        ({"STUB_FAIL_ON_CALL": "3"}, False, "1", None),  # post-swap check fails; restored
        ({"STUB_FAIL_ON_CALL": "3", "STUB_RENAME_FAIL": "3"}, False, BUILD, "1"),  # rejected stays
        ({"STUB_FAIL_ON_CALL": "3", "STUB_RENAME_FAIL": "4"}, False, None, "1"),  # restore fails
        ({}, True, BUILD, "1"),  # a successful update retains the latest backup
    ],
    ids=[
        "aside",
        "move-in",
        "move-in+restore",
        "verify",
        "verify+reject",
        "verify+restore",
        "keep-backup",
    ],
)
def test_every_swap_failure_and_its_retry_keep_a_good_copy(inst, inject, ok, app_build, prev_build):
    inst.install_old()  # build 1, verifies
    inst.publish()
    result = inst.run(**inject)
    assert (result.returncode == 0) is ok, result.stderr
    assert _build_at(inst.target) == app_build
    assert _build_at(_prev(inst)) == prev_build
    assert not (inst.dest / LOCK_NAME).exists()
    if inst.target.exists() and _build_at(inst.target) == BUILD and not ok:
        # The build that failed its check is still installed: it says so, and where the good
        # copy is. From here on it keeps failing the check.
        assert str(inst.target) in result.stderr and "do not open it" in result.stderr
        assert str(_prev(inst)) in result.stderr
        (inst.target / "Contents/SIGNER").write_text("bad")
    assert _good_copy_left(inst)
    retry = inst.run(STUB_CURL_FAIL="all")  # a retry that fails as well
    assert retry.returncode != 0
    assert _good_copy_left(inst), retry.stderr
    assert not (inst.dest / LOCK_NAME).exists()


def test_a_first_install_that_cannot_reject_its_copy_says_so(inst):
    inst.publish()
    result = inst.run(
        STUB_FAIL_ON_CALL="3", STUB_RENAME_FAIL="2"
    )  # rename 1 moves it in, rename 2 rejects
    assert result.returncode != 0
    assert "could not be moved aside" in result.stderr and "do not open it" in result.stderr
    assert str(inst.target) in result.stderr and "no previous copy" in result.stderr


def test_a_failed_swap_never_restores_an_unverified_previous_copy(inst):
    """The installed app did not verify at start-up (no backup, so the run went ahead) and the new
    copy fails after the move-in. Neither may stay at, or go back to, the app path: both are kept
    aside, named, with "do not open it"."""
    _place(inst, inst.target, "bad", "1")
    inst.publish()
    result = inst.run(STUB_FAIL_ON_CALL="3")  # accept_drop 1-2; call 3 checks the moved-in copy
    assert result.returncode != 0
    assert "was restored" not in result.stderr and "do not open it" in result.stderr
    assert not inst.target.exists() and not _prev(inst).exists(), result.stderr
    (kept,) = _leftovers(inst)
    assert _bundles(inst) == {kept / "rejected": BUILD, kept / "unverified-previous": "1"}
    assert f"kept at {kept / 'unverified-previous'}" in result.stderr


def test_a_failed_download_leaves_the_previous_copy_when_the_app_disappears(inst):
    """Fails before the swap: .tester-previous is never moved, whatever happens to the app."""
    inst.install_old()
    _place(inst, _prev(inst), "good", "2")
    inst.publish()
    result = inst.run(STUB_CURL_FAIL="all", STUB_CURL_RM=str(inst.target))
    assert result.returncode != 0
    assert _build_at(_prev(inst)) == "2", result.stderr
    assert _staging_left(inst) == []


@pytest.mark.parametrize("restore", ["ok", "fails"])
def test_a_staged_previous_copy_survives_a_swap_failure_with_no_app_left(inst, restore):
    """Swap step 1 moves the stale .tester-previous into staging; then the app vanishes and step 2
    fails. The staged copy goes back to .tester-previous or, if it cannot, to a named leftover:
    never deleted with the staging folder, though neither the app nor .tester-previous exists."""
    inst.install_old()
    _place(inst, _prev(inst), "good", "2")
    inst.publish()
    inject = {"STUB_RENAME_FAIL": "2"} if restore == "fails" else {}  # rename 2 is the restore
    result = inst.run(
        STUB_RENAME_HOOK_AT="1",  # right after step 1 moved .tester-previous to staging
        STUB_RENAME_HOOK_AFTER="1",
        STUB_RENAME_HOOK='rm -rf "$TGT"',
        TGT=str(inst.target),
        **inject,
    )
    assert result.returncode != 0
    assert "could not move the installed app aside" in result.stderr, result.stderr
    assert "installed tester-" not in result.stdout
    assert not inst.target.exists()
    assert _staging_left(inst) == [], result.stderr
    assert not (inst.dest / LOCK_NAME).exists()
    leftovers = [p for p in inst.dest.iterdir() if p.name.startswith(".tester-leftover.")]
    if restore == "ok":
        assert leftovers == []
        staged_copy = _prev(inst)
    else:
        assert not _prev(inst).exists()
        (kept,) = leftovers
        staged_copy = kept / "superseded"
        assert f"kept at {staged_copy}" in result.stderr, result.stderr
    # The only app copy left is the staged one, at the one place the outcome names.
    assert _bundles(inst) == {staged_copy: "2"}, result.stderr


# Contract (swap and cleanup ownership): a run deletes only what it created, and the older backup
# only once its own copy is verified in place and the new backup has itself verified. Every other
# copy it moved is put back or kept in a named .tester-leftover.* folder; no move nests one app in
# another. Whatever the run's exclusive rename puts at the app path stays only if it verifies there
# (a copy that replaced the source during the rename included); a copy that fails is never left at
# the app path and never deleted. Copies are told apart by build: old app 1, stale
# .tester-previous 2, another program's app 7, this run's drop BUILD.

# Hooks, by action: "good"/"bad" replace the app path with another program's app of that signer;
# "source" replaces the rename's source with another program's (good) app, moving the original
# out of the install directory first so that the replacement gets a new inode on any file system;
# "corrupt-app"/"corrupt-prev" break the signature of the app or .tester-previous in place (the
# bundle folder keeps its inode); "contents" swaps the app's contents for another program's
# (good) app while the bundle folder keeps its inode, as Linux's inode reuse does after a
# delete and copy.
_FOREIGN_HOOK = 'rm -rf "$TGT"; cp -R "$FOREIGN" "$TGT"'
_SOURCE_HOOK = 'src="${@: -2:1}"; /bin/mv "$src" "$AWAY"; cp -R "$FOREIGN" "$src"'
_HOOKS = {
    "good": _FOREIGN_HOOK,
    "bad": _FOREIGN_HOOK,
    "source": _SOURCE_HOOK,
    "source-bad": _SOURCE_HOOK,  # ... with another program's app that does not verify
    "corrupt-app": 'echo broken > "$TGT/Contents/SIGNER"',
    "contents": 'rm -rf "$TGT/Contents"; cp -R "$FOREIGN/Contents" "$TGT/Contents"',
    "corrupt-prev": 'echo broken > "$PREV/Contents/SIGNER"',
}


def _bundles(env: InstallEnv) -> dict[Path, str]:
    """Every app copy under the install directory, by bundle folder, with its build."""
    return {
        plist.parent.parent: plistlib.loads(plist.read_bytes())["CFBundleVersion"]
        for plist in env.dest.rglob("Info.plist")
    }


def _foreign(env: InstallEnv, signer: str) -> Path:
    app = _write_bundle(env.tmp / f"foreign-{signer}", version="0.0.7", build="7")
    (app / "Contents/SIGNER").write_text(signer)
    return app


def _hook_env(env: InstallEnv, where: str, action: str) -> dict[str, str]:
    signer = "bad" if action in {"bad", "source-bad"} else "good"
    extra = {
        "FOREIGN": str(_foreign(env, signer)),
        "TGT": str(env.target),
        "PREV": str(_prev(env)),
        "AWAY": str(env.tmp / "moved-away"),
    }
    if where == "curl":
        extra["STUB_CURL_HOOK"] = _HOOKS[action]
    else:
        _, when, n = where.split("-")
        extra.update(STUB_RENAME_HOOK=_HOOKS[action], STUB_RENAME_HOOK_AT=n)
        if when == "after":
            extra["STUB_RENAME_HOOK_AFTER"] = "1"
    return extra


def _signer(app: Path) -> str:
    return (app / "Contents/SIGNER").read_text().strip()


@pytest.mark.parametrize(
    ("start", "inject", "ok", "survive"),
    [
        # An unverified app takes the target during the download, which then fails.
        (("good", "good"), {"STUB_CURL_FAIL": "all", "hook": ("curl", "bad")}, False, {"2", "7"}),
        # The download fails while a stale .tester-previous waits beside a verified app.
        (("good", "good"), {"STUB_CURL_FAIL": "all"}, False, {"1", "2"}),
        # Another program recreates the target right after the old app moved aside.
        (("good", "absent"), {"hook": ("rename-after-1", "good")}, False, {"1", "7"}),
        (("good", "absent"), {"hook": ("rename-after-1", "bad")}, False, {"1", "7"}),
        # ... or between the installer's check and its rename (a plain mv would nest the new app inside).
        (("good", "absent"), {"hook": ("rename-before-2", "good")}, False, {"1", "7"}),
        (("absent", "absent"), {"hook": ("rename-before-1", "good")}, False, {"7"}),
        # ... or after the new copy moved in, before it was verified. A replacement that verifies is
        # left there (where Linux reuses the inode it is indistinguishable from this run's copy, which
        # fails the drop check: moved out and the previous copy restored); one that does not verify is
        # moved out.
        (
            ("good", "absent"),
            {"hook": ("rename-after-2", "good"), "at": {"7", "1"}},
            False,
            {"1", "7"},
        ),
        (
            ("good", "absent"),
            {
                "hook": ("rename-after-2", "bad"),
                "at": {"1"},
                "says": ["do not open it", "previous copy was restored"],
            },
            False,
            {"1", "7"},
        ),
        # ... with the same inode: only the drop's version and build tell it from this run's copy.
        (
            ("good", "absent"),
            {
                "hook": ("rename-after-2", "contents"),
                "at": {"1"},
                "says": ["do not open it"],
                "not_says": ["nothing was changed"],
            },
            False,
            {"1", "7"},
        ),
        # The rename's source is replaced after its check: what lands at the app path stays only if
        # it verifies (and the message says so), otherwise it is moved out.
        (
            ("good", "absent"),
            {
                "hook": ("rename-before-2", "source"),
                "at": {"7"},
                "says": ["could not restore the previous copy"],
            },
            False,
            {"1", "7"},
        ),
        (
            ("good", "absent"),
            {
                "hook": ("rename-before-2", "source-bad"),
                "at": {"1"},
                "says": ["do not open it", "previous copy was restored"],
            },
            False,
            {"1", "7"},
        ),
        (("absent", "good"), {"hook": ("rename-before-1", "source"), "at": {"7"}}, False, {"7"}),
        (
            ("absent", "good"),
            {
                "hook": ("rename-before-1", "source-bad"),
                "at": {None},
                "says": ["did not pass verification", "do not open it"],
            },
            False,
            {"7"},
        ),
        # ... during rollback's restore of the previous copy (renames: 1 aside, 2 move-in, 3 reject).
        (
            ("good", "absent"),
            {"STUB_FAIL_ON_CALL": "3", "hook": ("rename-before-4", "source"), "at": {"7"}},
            False,
            {"7", BUILD},
        ),
        # Swap step 1 or 2 renamed another program's copy (safe_move 4): the rename is recorded, so
        # the message says where the moved copy is, never "nothing was changed".
        (
            ("good", "good"),
            {
                "hook": ("rename-before-1", "source"),
                "at": {"1"},
                "says": ["another program changed", "could not be verified, do not open it"],
                "not_says": ["nothing was changed"],
            },
            False,
            {"1", "7"},
        ),
        (
            ("good", "absent"),
            {
                "hook": ("rename-before-1", "source"),
                "at": {None},
                "says": ["another program changed", "do not open it", "could not restore"],
                "not_says": ["nothing was changed"],
            },
            False,
            {"7"},
        ),
        # A stale .tester-previous was moved to staging; the swap fails and it cannot go back.
        (("good", "good"), {"STUB_RENAME_FAIL": "2,3"}, False, {"1", "2"}),
        # ... and it never verified (start-up checks only the app): named with "do not open it".
        (
            ("good", "bad"),
            {
                "STUB_RENAME_FAIL": "2,3",
                "says": ["could not be verified, do not open it"],
                "at": {"1"},
            },
            False,
            {"1", "2"},
        ),
        # The swap fails and every step is undone.
        (("good", "good"), {"STUB_RENAME_FAIL": "2"}, False, {"1", "2"}),
        (("good", "good"), {"STUB_FAIL_ON_CALL": "5"}, False, {"1", "2"}),
        # Success retires the older backup and retains the most recent known copy.
        (("good", "good"), {}, True, set()),
        (("absent", "good"), {}, True, set()),
        # The app breaks in place during the download, or the new backup right after the move
        # aside: the install succeeds, the broken build is not kept as the rollback copy, and the
        # older backup is kept (in a leftover), not deleted.
        (("good", "good"), {"hook": ("curl", "corrupt-app")}, True, {"1", "2"}),
        (("good", "absent"), {"hook": ("curl", "corrupt-app")}, True, {"1"}),
        (("good", "good"), {"hook": ("rename-after-2", "corrupt-prev")}, True, {"1", "2"}),
    ],
    ids=[
        "unverified-target-during-failed-download",
        "failed-download-with-stale-previous",
        "target-recreated-after-move-aside-good",
        "target-recreated-after-move-aside-bad",
        "target-created-between-check-and-rename",
        "first-install-target-created-between-check-and-rename",
        "target-replaced-before-verification",
        "target-replaced-by-unverified-copy-before-verification",
        "target-replaced-in-place-before-verification",
        "source-replaced-during-move-in",
        "source-replaced-by-unverified-copy-during-move-in",
        "source-replaced-during-start-up-restore",
        "source-replaced-by-unverified-copy-during-start-up-restore",
        "source-replaced-during-rollback-restore",
        "stale-previous-replaced-while-cleared",
        "app-replaced-while-moved-aside",
        "superseded-copy-cannot-go-back",
        "unverified-superseded-copy-cannot-go-back",
        "aside-fails-with-stale-previous",
        "verify-fails-with-stale-previous",
        "success-with-stale-previous",
        "success-after-restoring-previous",
        "app-corrupted-in-place-during-download",
        "app-corrupted-in-place-during-download-no-previous",
        "new-backup-corrupted-in-place",
    ],
)
def test_no_run_deletes_a_copy_it_did_not_create(inst, start, inject, ok, survive):
    app, prev = start
    if app != "absent":
        inst.install_old()  # build 1, verifies
    if prev != "absent":
        _place(inst, _prev(inst), prev, "2")
    inject = dict(inject)
    hook = inject.pop("hook", None)
    at = inject.pop("at", None)  # the builds acceptable at the app path afterwards (None: absent)
    says = inject.pop("says", [])
    not_says = inject.pop("not_says", [])
    if hook:
        inject.update(_hook_env(inst, *hook))
    good_before = _good_copy_left(inst)
    inst.publish()
    result = inst.run(**inject)
    assert (result.returncode == 0) is ok, result.stderr
    assert not (inst.dest / LOCK_NAME).exists()
    assert _staging_left(inst) == [], result.stderr
    bundles = _bundles(inst)
    leftovers = _leftovers(inst)
    for folder in leftovers:
        assert str(folder) in result.stderr, result.stderr
    # No app is nested inside another: every copy sits at a known place.
    known = {inst.target, _prev(inst)} | {c for f in leftovers for c in f.iterdir()}
    assert set(bundles) <= known, (bundles, result.stderr)
    assert survive <= set(bundles.values()), (bundles, result.stderr)
    # A copy that failed verification and was kept aside is named with "do not open it".
    for copy in bundles:
        if copy.parent in leftovers and copy.name in {"rejected", "unverified-previous"}:
            assert f"kept at {copy}" in result.stderr and "do not open it" in result.stderr
        if copy.parent in leftovers and copy.name == "superseded" and _signer(copy) != "good":
            assert f"kept at {copy}; it could not be verified, do not open it" in result.stderr
    if at is not None:
        assert bundles.get(inst.target) in at, (bundles, result.stderr)
    for text in says:
        assert text in result.stderr, (text, result.stderr)
    for text in not_says:
        assert text not in result.stderr, (text, result.stderr)
    if at is not None:
        # Rows with "at" are those where one of the run's own renames may land another program's
        # copy at the app path. Such a copy stays only if it verifies, and the message says so;
        # whatever is left there unverified is named with "do not open it". (A copy another
        # program puts there while the run's rename is refused, or during the download, was never
        # moved by the run and is not judged by it.)
        if bundles.get(inst.target) == "7":
            assert _signer(inst.target) == "good", result.stderr
            assert "passes verification and was left there" in result.stderr, result.stderr
        if inst.target.exists() and _signer(inst.target) != "good":
            assert str(inst.target) in result.stderr and "do not open it" in result.stderr
    if ok:
        assert f"installed {TAG}" in result.stdout
        assert bundles[inst.target] == BUILD and _signer(inst.target) == "good"
        if not survive:
            assert bundles == {inst.target: BUILD, _prev(inst): "1" if app != "absent" else "2"}
        # The rollback copy is always one that verified.
        assert not _prev(inst).exists() or _signer(_prev(inst)) == "good", result.stderr
        return
    assert "installed tester-" not in result.stdout
    if good_before or (hook and hook[1] in {"good", "source", "contents"}):
        # A good copy survives: in the install directory, or where another program moved it.
        away = inst.tmp / "moved-away"
        signers = [_signer(b) for b in bundles] + ([_signer(away)] if away.is_dir() else [])
        assert "good" in signers, result.stderr
    if inst.target.exists() and _build_at(inst.target) == BUILD:
        assert "do not open it" in result.stderr


def test_a_copy_kept_after_a_failed_run_is_named_and_left_for_the_person(inst):
    inst.install_old()
    _place(inst, _prev(inst), "good", "2")
    inst.publish()
    failed = inst.run(
        STUB_RENAME_FAIL="2,3"
    )  # superseded copy moved, aside fails, it cannot go back
    assert failed.returncode != 0
    (kept,) = [p for p in inst.dest.iterdir() if p.name.startswith(".tester-leftover.")]
    assert f"kept at {kept / 'superseded'}" in failed.stderr
    assert _build_at(kept / "superseded") == "2"
    again = inst.run()
    assert again.returncode == 0, again.stderr
    assert str(kept) in again.stderr and _build_at(kept / "superseded") == "2"


def test_exclusive_move_preserves_an_occupied_target_and_its_empty_child(inst):
    inst.install_old()
    inst.publish()
    before = inst.run(
        STUB_RENAME_HOOK_AT="2",
        STUB_RENAME_HOOK='mkdir -p "$TGT/${TGT##*/}"',
        TGT=str(inst.target),
    )
    assert before.returncode == 1
    assert list((inst.target / NAME).iterdir()) == []
    assert _build_at(_prev(inst)) == "1"


@pytest.mark.parametrize("verified", [True, False])
def test_a_replaced_backup_is_never_consumed(inst, verified):
    inst.install_old()
    _place(inst, _prev(inst), "good", "2")
    inst.publish()
    saved = inst.tmp / "saved-backup"
    result = inst.run(
        STUB_CODESIGN_HOOK_AT="5",
        STUB_CODESIGN_HOOK='/bin/mv "$PREV" "$SAVED"; /bin/cp -R "$FOREIGN" "$PREV"',
        STUB_FAIL_ON_CALL="0" if verified else "5",
        PREV=str(_prev(inst)),
        SAVED=str(saved),
        FOREIGN=str(_foreign(inst, "good")),
    )
    assert (result.returncode == 0) is verified
    assert _build_at(saved) == "1"
    assert _build_at(_prev(inst)) == "7"
    assert _build_at(inst.target) == (BUILD if verified else None)
    assert "2" in _bundles(inst).values()


# `ls`, whose `ls -did -- <path>` is the installer's inode read. Once STUB_LS_ARMED exists, a read
# of STUB_LS_PATH fails (as for a bundle whose identity cannot be read) or, with STUB_LS_HOOK set,
# runs the hook once before the real read.
_LS_STUB = """#!/bin/bash
if [ "${1:-}" = -did ] && [ "${3:-}" = "$STUB_LS_PATH" ] && [ -e "$STUB_LS_ARMED" ]; then
  if [ -z "${STUB_LS_HOOK:-}" ]; then
    exit 1
  elif [ ! -e "$STUB_LS_ARMED.fired" ]; then
    touch "$STUB_LS_ARMED.fired"; /bin/bash -c "$STUB_LS_HOOK"
  fi
fi
exec /bin/ls "$@"
"""


def _stub_ls(env: InstallEnv) -> dict[str, str]:
    (env.bin / "ls").write_text(_LS_STUB)
    (env.bin / "ls").chmod(0o755)
    return {"STUB_LS_PATH": str(env.target), "STUB_LS_ARMED": str(env.tmp / "ls.armed")}


@pytest.mark.parametrize("start", ["update", "first-install"])
def test_an_unreadable_identity_at_the_app_path_is_never_reported_as_moved_out(inst, start):
    """The moved-in copy fails its check and then its inode cannot be read: it may still be at the
    app path, so it counts as unverified and stuck there (named, "do not open it"), never as moved
    out."""
    if start == "update":
        inst.install_old()
    inst.publish()
    result = inst.run(
        STUB_FAIL_ON_CALL="3",  # accept_drop 1-2; call 3 checks the moved-in copy
        STUB_CODESIGN_HOOK_AT="3",
        STUB_CODESIGN_HOOK='touch "$STUB_LS_ARMED"',
        **_stub_ls(inst),
    )
    assert result.returncode != 0
    assert _build_at(inst.target) == BUILD  # the unverified copy is still there
    assert "was not left there" not in result.stderr, result.stderr
    assert f"{inst.target} is an unverified build, do not open it" in result.stderr, result.stderr
    if start == "update":
        assert _build_at(_prev(inst)) == "1" and str(_prev(inst)) in result.stderr
    assert not (inst.dest / LOCK_NAME).exists() and _staging_left(inst) == []


def test_a_backup_replaced_just_before_the_older_one_is_deleted_keeps_the_older_one(inst):
    """backup_ok is set, then .tester-previous is replaced while cleanup reads the app's inode: the
    older backup is deleted only if the backup still verifies as the saved copy right before."""
    inst.install_old()
    _place(inst, _prev(inst), "good", "2")
    inst.publish()
    saved = inst.tmp / "saved-backup"
    arm = inst.bin / "rm"
    # Cleanup's first rm (the probe output) arms the hook; backup_ok has not been checked yet.
    arm.write_text(
        '#!/bin/bash\ncase "$2" in */probe.err) touch "$STUB_LS_ARMED" ;; esac\nexec /bin/rm "$@"\n'
    )
    arm.chmod(0o755)
    result = inst.run(
        STUB_LS_HOOK='/bin/mv "$PREV" "$SAVED"; /bin/cp -R "$FOREIGN" "$PREV"',
        PREV=str(_prev(inst)),
        SAVED=str(saved),
        FOREIGN=str(_foreign(inst, "good")),
        **_stub_ls(inst),
    )
    assert (inst.tmp / "ls.armed.fired").exists(), result.stderr
    assert result.returncode == 0, result.stderr
    assert _build_at(saved) == "1" and _build_at(_prev(inst)) == "7"
    (kept,) = _leftovers(inst)
    assert _bundles(inst).get(kept / "superseded") == "2", result.stderr
    assert f"kept at {kept / 'superseded'}" in result.stderr


def test_target_disappearing_during_success_cleanup_leaves_the_backups(inst):
    inst.install_old()
    _place(inst, _prev(inst), "good", "2")
    inst.publish()
    cleanup_rm = inst.bin / "rm"
    cleanup_rm.write_text(
        '#!/bin/bash\ncase "$2" in */probe.err) /bin/rm -rf "$TGT" ;; esac\nexec /bin/rm "$@"\n'
    )
    cleanup_rm.chmod(0o755)
    result = inst.run(TGT=str(inst.target))
    assert result.returncode != 0 and "changed" in result.stderr
    assert "installed tester-" not in result.stdout
    assert not inst.target.exists()
    assert _build_at(_prev(inst)) == "1"
    assert "2" in _bundles(inst).values()


@pytest.mark.parametrize(
    ("sig", "rc"),
    [("SIGTERM", 143), ("SIGHUP", 129), ("SIGINT", 130), ("SIGPIPE", 141), ("SIGQUIT", 131)],
)
def test_a_signal_waits_for_the_running_command_before_releasing_the_lock(inst, sig, rc):
    """Bash 3.2 without a signal trap runs the EXIT trap at once and leaves the child running."""
    import signal

    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    done = inst.tmp / "curl.done"
    run = subprocess.Popen(
        ["/bin/bash", "-s", "--", TAG],
        stdin=subprocess.PIPE,
        env=inst.env(STUB_CURL_SLEEP="2", STUB_CURL_DONE=str(done)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert run.stdin is not None
        run.stdin.write(inst.installer.read_text())
        run.stdin.close()
        run.stdin = None
        for _ in range(200):
            if inst.logs["curl"].exists():
                break
            time.sleep(0.05)
        run.send_signal(getattr(signal, sig))
        time.sleep(0.5)
        # The download is still running: the run has not exited and still holds its lock.
        assert run.poll() is None and (inst.dest / LOCK_NAME).is_dir() and not done.exists()
    finally:
        out, err = run.communicate(timeout=60)
    assert run.returncode == rc, err
    assert done.exists()  # the child finished before the run exited
    assert _snapshot(inst.home) == before, err


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGHUP", "SIGINT", "SIGPIPE", "SIGQUIT"])
def test_a_signal_during_the_swap_is_ignored_and_the_swap_completes(inst, sig):
    """Sent to the whole process group, as Control-C or a closed Terminal window does."""
    import signal

    inst.install_old()
    inst.publish()
    mark = inst.tmp / "rename.mark"
    run = subprocess.Popen(
        ["/bin/bash", str(inst.installer), TAG],
        env=inst.env(STUB_RENAME_SLEEP_AT="1", STUB_RENAME_SLEEP="1", STUB_RENAME_MARK=str(mark)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        for _ in range(400):
            if mark.exists():
                break
            time.sleep(0.025)
        assert mark.exists()
        os.killpg(run.pid, getattr(signal, sig))  # while the old app is being moved aside
    finally:
        out, err = run.communicate(timeout=60)
    assert run.returncode == 0, err
    assert f"installed {TAG}" in out
    assert _bundles(inst) == {inst.target: BUILD, _prev(inst): "1"}
    assert _build_at(inst.target) == BUILD


# Holds the named tool's call on a matching path for STUB_HOLD_SECS after the real tool ran,
# touching STUB_HOLD_MARK first: the window in which a signal lands.
_HOLD_STUB = """#!/bin/bash
out="$({real} "$@")"; rc=$?
[ -z "$out" ] || printf '%s\\n' "$out"
case " $* " in *"$STUB_HOLD_MATCH"*) touch "$STUB_HOLD_MARK"; sleep "$STUB_HOLD_SECS" ;; esac
exit "$rc"
"""


def _hold(env: InstallEnv, tool: str, match: str) -> dict[str, str]:
    real = shutil.which(tool)
    assert real is not None and not real.startswith(str(env.bin)), real
    stub = env.bin / tool
    stub.write_text(_HOLD_STUB.format(real=real))
    stub.chmod(0o755)
    return {
        "STUB_HOLD_MATCH": match,
        "STUB_HOLD_MARK": str(env.tmp / "hold.mark"),
        "STUB_HOLD_SECS": "1",
    }


def _signal_when_held(argv: list[str], env: dict[str, str], sig: str, cwd: Path | None = None):
    """Start the run in its own process group and signal the group, as Control-C or a closed
    Terminal window does, once the held call has done its work."""
    import signal

    mark = Path(env["STUB_HOLD_MARK"])
    run = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        for _ in range(400):
            if mark.exists():
                break
            time.sleep(0.025)
        assert mark.exists(), "the held call never ran"
        os.killpg(run.pid, getattr(signal, sig))
    finally:
        out, err = run.communicate(timeout=60)
    return run.returncode, out, err


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGHUP", "SIGINT", "SIGPIPE", "SIGQUIT"])
def test_a_signal_during_cleanup_is_ignored_and_cleanup_finishes(inst, sig):
    """A failed run's cleanup removes its staging folder and then its lock. A second signal while
    it runs (here during its rm of the download) must not end the shell with the lock held."""
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    env = inst.env(STUB_CURL_FAIL=f"{TAG}.zip.sha256", **_hold(inst, "rm", "/probe.err"))
    rc, out, err = _signal_when_held(["/bin/bash", str(inst.installer), TAG], env, sig)
    assert rc == 1 and "could not download the checksum" in err, err
    assert not (inst.dest / LOCK_NAME).exists(), err
    assert _staging_left(inst) == [], err
    assert _snapshot(inst.home) == before, err


# Contract (lock and staging acquisition): whenever a trap can run, LOCK is set exactly when this
# run created the lock, and STAGE and STAGE_INODE name the staging folder exactly when it exists.
# A signal that lands between creating one and naming it is discarded, never acted on in between.
@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT"])
@pytest.mark.parametrize(
    ("tool", "match"),
    [("mkdir", f"/{LOCK_NAME} "), ("mktemp", "/.tester-install.XXXXXX ")],
    ids=["lock", "staging"],
)
def test_a_signal_between_creating_and_naming_the_lock_or_staging_leaves_nothing(
    inst, tool, match, sig
):
    inst.install_old()
    inst.publish()
    env = inst.env(**_hold(inst, tool, match))
    rc, out, err = _signal_when_held(["/bin/bash", str(inst.installer), TAG], env, sig)
    # The signal is discarded and the run completes; either way nothing it created is left.
    assert not (inst.dest / LOCK_NAME).exists(), err
    assert _staging_left(inst) == [], err
    assert rc == 0 and f"installed {TAG}" in out, err
    assert _bundles(inst) == {inst.target: BUILD, _prev(inst): "1"}


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT"])
def test_a_signal_while_check_drop_creates_its_scratch_folder_leaves_nothing(inst, sig):
    zip_path = inst.publish()
    scratch = inst.tmp / "tmpdir"
    scratch.mkdir()
    env = inst.env(TMPDIR=str(scratch), **_hold(inst, "mktemp", "/tester-check-drop.XXXXXX "))
    rc, out, err = _signal_when_held(
        ["/bin/bash", str(inst.installer), "--check-drop", str(zip_path), TAG], env, sig
    )
    assert list(scratch.iterdir()) == [], err
    assert rc == 0 and "ok:" in out, err


@pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS chflags")
@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_a_staging_folder_that_cannot_be_removed_is_named_then_and_later(inst, outcome):
    """A file this run cannot delete (here: user-immutable) leaves its staging folder; it is named."""
    inst.publish()
    pin = (
        'for d in "$DEST"/.tester-install.*; do [ "$d" = "$DEST/.tester-install.lock" ] && continue;'
        ' touch "$d/pinned" && chflags uchg "$d/pinned"; done'
    )
    extra = {} if outcome == "success" else {"STUB_CURL_FAIL": f"{TAG}.zip.sha256"}
    try:
        first = inst.run(STUB_CURL_HOOK=pin, DEST=str(inst.dest), **extra)
        assert (first.returncode == 0) is (outcome == "success"), first.stderr
        (stage,) = _staging_left(inst)
        assert f"staging folder {inst.dest / stage}" in first.stderr, first.stderr
        assert not [p for p in inst.dest.iterdir() if p.name.startswith(".tester-leftover.")]
        second = inst.run()
        assert second.returncode == 0, second.stderr
        assert str(inst.dest / stage) in second.stderr and (inst.dest / stage).is_dir()
    finally:
        subprocess.run(["chflags", "-R", "nouchg", str(inst.dest)], capture_output=True)


def test_a_second_installer_refuses_while_one_runs(inst):
    inst.install_old()
    inst.publish()
    first = subprocess.Popen(
        ["/bin/bash", str(inst.installer), TAG],
        env=inst.env(STUB_CURL_SLEEP="3"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(200):
            if _staging_left(inst):
                break
            time.sleep(0.05)
        second = inst.run()
    finally:
        out, err = first.communicate(timeout=60)
    assert second.returncode != 0, second.stdout
    assert "another tester install" in second.stderr
    assert first.returncode == 0, err
    assert _bundles(inst) == {inst.target: BUILD, _prev(inst): "1"}
    assert _build_at(inst.target) == BUILD


# -- installer: what extraction writes ---------------------------------------------------------

real_ditto = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("ditto") is None, reason="needs macOS ditto"
)


def _with_declared_sizes(path: Path, sizes: dict[str, int]) -> None:
    """Rewrite central-directory uncompressed sizes, the field ``unzip -Z`` reports."""
    data = bytearray(path.read_bytes())
    at = 0
    while (at := data.find(b"PK\x01\x02", at)) >= 0:
        name_len = int.from_bytes(data[at + 28 : at + 30], "little")
        name = data[at + 46 : at + 46 + name_len].decode()
        if name in sizes:
            data[at + 24 : at + 28] = sizes[name].to_bytes(4, "little")
        at += 4
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    "sizes",
    [
        {f"{NAME}/Contents/big": 0xFFFF0000},
        {
            f"{NAME}/Contents/a": 400_000_000,
            f"{NAME}/Contents/b": 400_000_000,
            f"{NAME}/Contents/c": 400_000_000,
        },
    ],
    ids=["one-entry", "sum-of-entries"],
)
def test_an_archive_that_unpacks_too_large_is_refused_before_extraction(inst, sizes):
    inst.install_old()
    extra = [(name, b"x", "file") for name in sizes]
    zip_path = inst.publish(entries=_bundle_entries(extra=extra))
    _with_declared_sizes(zip_path, sizes)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (inst.release / f"{TAG}.zip.sha256").write_text(f"{digest}  {TAG}.zip\n")
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and "unpacks to" in result.stderr, result.stderr
    assert _snapshot(inst.home) == before
    assert not inst.logs["ditto"].exists()


# The documented limits (docs/development.md), asserted as values so a silent change fails.
UNPACKED_LIMIT = 1 << 30  # 1 GiB
ENTRY_LIMIT = 10_000
DOWNLOAD_LIMIT = 250 << 20  # 250 MiB
PATH_LIMIT = 1024  # bytes


def test_the_documented_limits_are_the_ones_the_tests_assert():
    section = (REPO_ROOT / "docs/development.md").read_text().split("## Tester drops", 1)[1]
    section = " ".join(section.split("\n## ", 1)[0].split())
    for text in (
        f"a ZIP of at most {DOWNLOAD_LIMIT >> 20} MiB",
        f"{UNPACKED_LIMIT >> 30} GiB unpacked",
        f"{ENTRY_LIMIT:,} entries",
        f"paths of at most {PATH_LIMIT:,} bytes",
    ):
        assert text in section, text


def _check_drop(env: InstallEnv, zip_path: Path, tag: str = TAG, **extra: str):
    scratch = env.tmp / "tmpdir"
    scratch.mkdir(exist_ok=True)
    result = _run(
        env.installer, "--check-drop", str(zip_path), tag, env=env.env(TMPDIR=str(scratch), **extra)
    )
    assert list(scratch.iterdir()) == [], "--check-drop must remove its scratch folder"
    return result


def _republish(env: InstallEnv, zip_path: Path) -> None:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (env.release / f"{TAG}.zip.sha256").write_text(f"{digest}  {TAG}.zip\n")


def test_an_archive_at_the_size_limit_is_accepted(inst):
    zip_path = inst.publish(entries=_bundle_entries(extra=[(f"{NAME}/Contents/a", b"x", "file")]))
    assert _check_drop(inst, zip_path).returncode == 0
    with zipfile.ZipFile(zip_path) as archive:
        other_bytes = sum(info.file_size for info in archive.infolist()) - 1
    _with_declared_sizes(zip_path, {f"{NAME}/Contents/a": UNPACKED_LIMIT - other_bytes})
    at_limit = _check_drop(inst, zip_path)
    assert at_limit.returncode == 0, at_limit.stderr
    inst.logs["ditto"].unlink()
    _with_declared_sizes(zip_path, {f"{NAME}/Contents/a": UNPACKED_LIMIT - other_bytes + 1})
    over = _check_drop(inst, zip_path)
    assert over.returncode == 1 and "unpacks to" in over.stderr
    assert not inst.logs["ditto"].exists()


def test_the_per_entry_size_gate_refuses_a_wrapped_zipinfo_total(inst):
    extra = [(f"{NAME}/Contents/{n}", b"x", "file") for n in ("large-a", "large-b")]
    zip_path = inst.publish(entries=_bundle_entries(extra=extra))
    unzip = shutil.which("unzip")
    assert unzip is not None
    listing = _sh(unzip, "-Zl", str(zip_path)).stdout
    listing = re.sub(
        r"(?m)^(\S+\s+\S+\s+\S+\s+)\d+(.*large-[ab])$",
        rf"\g<1>{1 << 63}\2",
        listing,
    )
    assert listing.count(str(1 << 63)) == 2
    mocked = inst.bin / "unzip"
    mocked.write_text(
        '#!/bin/bash\ncase "$1" in\n'
        '-Zt) printf "%s\\n" "$TOTALS" ;;\n'
        '-Zl) printf "%s" "$LISTING" ;;\n'
        '*) exec "$REAL_UNZIP" "$@" ;;\nesac\n'
    )
    mocked.chmod(0o755)
    result = _check_drop(
        inst,
        zip_path,
        LISTING=listing,
        TOTALS=f"{len(extra) + len(_bundle_entries())} files, "
        "0 bytes uncompressed, 0 bytes compressed: 0%",
        REAL_UNZIP=unzip,
    )
    assert result.returncode == 1 and "unpacks to" in result.stderr
    assert not inst.logs["ditto"].exists()


def test_the_entry_count_is_bounded_before_extraction(inst):
    """Empty entries add no bytes, so the byte limit alone does not bound what ditto creates."""
    base = _bundle_entries()
    base = base + [(f"{NAME}/Contents/e/", None, "file")]
    filler = lambda n: [(f"{NAME}/Contents/e/{i:05d}", b"", "file") for i in range(n)]  # noqa: E731
    zip_path = inst.publish(entries=base + filler(ENTRY_LIMIT - len(base)))
    ok = _check_drop(inst, zip_path)
    assert ok.returncode == 0, ok.stderr
    zip_path = inst.publish(entries=base + filler(ENTRY_LIMIT - len(base) + 1))
    inst.logs["ditto"].unlink()
    inst.logs["codesign"].unlink()
    inst.install_old()
    before = _snapshot(inst.home)
    refused = inst.run()
    assert refused.returncode != 0
    assert f"more than the {ENTRY_LIMIT} allowed" in refused.stderr, refused.stderr
    assert _snapshot(inst.home) == before
    assert not inst.logs["codesign"].exists()
    assert not inst.logs["ditto"].exists()


@pytest.mark.parametrize("over", [0, 1], ids=["at-cap", "one-byte-over"])
def test_the_zip_size_cap_is_checked_before_unzip_reads_it(inst, tmp_path, over):
    """MAX_ZIP_BYTES is inclusive. Sparse files of zeros: a ZIP at the cap passes the size check
    and only then fails to read as an archive; one byte over is refused before unzip runs."""
    real = shutil.which("unzip")
    assert real is not None
    log = inst.tmp / "unzip.log"
    wrapper = inst.bin / "unzip"
    wrapper.write_text(f'#!/bin/bash\necho "$*" >> "{log}"\nexec "{real}" "$@"\n')
    wrapper.chmod(0o755)
    big = tmp_path / "big.zip"
    with big.open("wb") as f:
        f.truncate(DOWNLOAD_LIMIT + over)
    result = _check_drop(inst, big)
    assert result.returncode == 1, result.stderr
    if over:
        assert f"more than the {DOWNLOAD_LIMIT} allowed" in result.stderr, result.stderr
        assert not log.exists()
    else:
        assert "more than the" not in result.stderr, result.stderr
        assert "cannot read the archive" in result.stderr, result.stderr
        assert log.exists()
    assert not inst.logs["ditto"].exists()


@pytest.fixture
def utf8_locale() -> str:
    listed = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split()
    utf8 = next((loc for loc in ("en_US.UTF-8", "C.UTF-8", "C.utf8") if loc in listed), None)
    if utf8 is None:
        pytest.skip("no UTF-8 locale on this host")
    return utf8


# Over PATH_LIMIT bytes but under PATH_LIMIT characters: only a byte count refuses it.
_LONG_NAME = f"{NAME}/Contents/" + "/".join(["é" * 100] * 6)  # 200-byte components, < NAME_MAX


def test_a_path_over_the_byte_limit_is_refused_before_extraction_in_a_utf8_locale(
    inst, tmp_path, utf8_locale
):
    """`curl | bash` inherits the person's UTF-8 locale. Stock unzip already fails on a name of
    PATH_LIMIT bytes or more (whatever the locale), so the refusal can come from it."""
    assert len(_LONG_NAME.encode()) > PATH_LIMIT > len(_LONG_NAME)
    bad = tmp_path / "long.zip"
    _make_zip(bad, _bundle_entries(extra=[(_LONG_NAME, b"x", "file")]))
    result = _check_drop(inst, bad, LC_ALL=utf8_locale, LANG=utf8_locale)
    assert result.returncode == 1
    assert (
        "cannot read the archive" in result.stderr
        or f"a path longer than {PATH_LIMIT} bytes" in result.stderr
    ), result.stderr
    assert not inst.logs["ditto"].exists()


def test_the_path_limit_counts_bytes_not_characters_in_a_utf8_locale(inst, tmp_path, utf8_locale):
    """The installer's own check is the backstop for an unzip that lists such a name. Under a
    UTF-8 locale ${#name} counts characters; the check must count bytes (LC_ALL=C)."""
    real = shutil.which("unzip")
    assert real is not None
    short = f"{NAME}/Contents/long"
    zip_path = tmp_path / "long.zip"
    _make_zip(zip_path, _bundle_entries(extra=[(short, b"x", "file")]))
    names = _sh(real, "-Z1", str(zip_path)).stdout.replace(short, _LONG_NAME)
    mocked = inst.bin / "unzip"
    mocked.write_text(
        '#!/bin/bash\n[ "$1" = -Z1 ] && { printf "%s" "$NAMES"; exit 0; }\nexec "$REAL_UNZIP" "$@"\n'
    )
    mocked.chmod(0o755)
    result = _check_drop(
        inst, zip_path, NAMES=names, REAL_UNZIP=real, LC_ALL=utf8_locale, LANG=utf8_locale
    )
    assert result.returncode == 1
    assert f"a path longer than {PATH_LIMIT} bytes" in result.stderr, result.stderr
    assert not inst.logs["ditto"].exists()


def test_check_drop_mode_is_the_whole_offline_acceptance(inst, tmp_path):
    good = inst.publish()
    result = _check_drop(inst, good)
    assert result.returncode == 0, result.stderr
    assert f"accepts for {TAG} (version {VERSION}, build {BUILD})" in result.stdout
    cases = [
        (_bundle_entries(extra=[("Other.app/x", b"x", "file")]), {}, "outside"),
        (_bundle_entries(signer="bad"), {}, "pinned tester certificate"),
        (_bundle_entries(version="1.2.3"), {}, "the tag names"),
        (_bundle_entries(), {"STUB_DITTO_PLANT": "symlink"}, "other than files and folders"),
        # A folder extraction would create without an entry of its own escapes the entry count.
        (
            _bundle_entries(extra=[(f"{NAME}/Contents/deep/er/file", b"x", "file")]),
            {},
            f"does not list the folder {NAME}/Contents/deep/",
        ),
        (
            _bundle_entries(
                extra=[(f"{NAME}/f^Joo/", None, "file"), (f"{NAME}/f\noo/item", b"x", "file")]
            ),
            {},
            "ambiguous path",
        ),
    ]
    for n, (entries, extra, message) in enumerate(cases):
        bad = tmp_path / f"bad{n}.zip"
        _make_zip(bad, entries)
        refused = _check_drop(inst, bad, **extra)
        assert refused.returncode == 1 and message in refused.stderr, (message, refused.stderr)
    refused = _check_drop(inst, good, tag="tester-1.0-b1-abc1234")
    assert refused.returncode == 1 and "not a tester tag" in refused.stderr
    assert _run(inst.installer, "--check-drop", str(tmp_path / "nope.zip"), TAG).returncode == 2
    assert _run(inst.installer, "--check-drop", str(good)).returncode == 2
    assert _run(inst.installer, "--check-drop", str(good), TAG, "x").returncode == 2
    assert _run(inst.installer, "--check-archive", str(good)).returncode == 2  # retired mode
    assert list(inst.dest.iterdir()) == [] and not inst.logs["curl"].exists()


def test_the_installed_bundle_gets_no_group_or_other_write_and_no_setuid(inst):
    """Modes come from the ZIP and codesign does not seal them; the installer resets them."""
    inst.publish()
    result = inst.run(STUB_DITTO_PLANT="loose")
    assert result.returncode == 0, result.stderr
    for path in [inst.target, *inst.target.rglob("*")]:
        mode = path.stat().st_mode
        assert not mode & 0o022 and not mode & 0o6000, (path, oct(mode))


@real_ditto
def test_real_ditto_acl_and_modes_from_the_zip_are_removed(inst, tmp_path):
    """Real ditto applies a ZIP's modes and an AppleDouble ACL (even with --noacl)."""
    (inst.bin / "ditto").unlink()
    src = _write_bundle(tmp_path / "src")
    (src / "Contents/SIGNER").write_text("good")
    (src / "Contents").chmod(0o777)
    (src / "Contents/Info.plist").chmod(0o666)
    _sh("chmod", "+a", "everyone allow add_file,delete_child", str(src / "Contents"))
    zip_path = inst.release / f"{TAG}.zip"
    _sh("ditto", "-c", "-k", "--keepParent", str(src), str(zip_path))
    _republish(inst, zip_path)
    result = inst.run()
    assert result.returncode == 0, result.stderr
    listing = _sh("ls", "-leR", str(inst.target)).stdout
    assert "everyone" not in listing, listing
    for path in [inst.target, *inst.target.rglob("*")]:
        assert not path.stat().st_mode & 0o022, (path, oct(path.stat().st_mode))


@pytest.mark.parametrize(
    ("plant", "message"),
    [
        ("symlink", "other than files and folders"),
        ("fifo", "other than files and folders"),
        ("top-symlink", "other than files and folders"),
        ("extra", "more than the app"),
    ],
)
def test_what_extraction_wrote_is_checked_before_verification(inst, plant, message):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run(STUB_DITTO_PLANT=plant)
    assert result.returncode != 0 and message in result.stderr, result.stderr
    assert _snapshot(inst.home) == before
    assert not inst.logs["codesign"].exists() or "extract" not in inst.logs["codesign"].read_text()


@real_ditto
def test_real_ditto_symlink_hidden_from_unzip_is_refused(inst):
    """A non-Unix host entry with symlink mode bits: unzip -Z shows no `l`, ditto makes a link."""
    (inst.bin / "ditto").unlink()
    zip_path = inst.release / f"{TAG}.zip"
    entries = _bundle_entries(extra=[(f"{NAME}/Contents/link", b"../../../outside", "symlink")])
    with zipfile.ZipFile(zip_path, "w") as z:
        for name, payload, kind in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 0  # MS-DOS host
            mode = 0o40755 if payload is None else 0o120777 if kind == "symlink" else 0o100644
            info.external_attr = (mode << 16) | (0x10 if payload is None else 0)
            z.writestr(info, payload or b"")
    listing = _sh("unzip", "-Z", str(zip_path)).stdout
    assert not re.search(r"^l", listing, re.MULTILINE)  # the central-directory check cannot see it
    _republish(inst, zip_path)
    inst.install_old()
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and "other than files and folders" in result.stderr, result.stderr
    assert _snapshot(inst.home) == before


# -- installer: platform and running-app probes ------------------------------------------------


def test_a_failing_sw_vers_is_reported(inst):
    inst.publish()
    result = inst.run(STUB_MACOS_FAIL="1")
    assert result.returncode != 0
    assert "could not read the macOS version" in result.stderr
    assert not inst.logs["curl"].exists()


_1728 = 'execution error: Can’t get application id "ai.pipecat.ServiceToolbar". (-1728)\n'


@pytest.mark.parametrize(
    ("rc", "out", "err", "proceeds"),
    [
        ("0", "false\n", "", True),
        ("0", "false\n", "osascript: warning: some noise\n", True),  # stderr is not the answer
        ("0", "true\n", "", False),
        ("0", "maybe\n", "", False),
        ("0", "", _1728, False),  # -1728 counts only with a failed exit
        ("1", _1728, "", False),  # ... and only on stderr
        ("1", "", _1728, True),
        ("1", "", "some other failure\n", False),
        ("1", "false\n", "", False),
    ],
)
def test_running_probe_reads_stdout_stderr_and_exit_apart(inst, rc, out, err, proceeds):
    inst.publish()
    result = inst.run(STUB_OSA_RC=rc, STUB_OSA_OUT=out, STUB_OSA_ERR=err)
    assert (result.returncode == 0) is proceeds, result.stderr
    if not proceeds:
        assert "quit" in result.stderr or "could not tell" in result.stderr
        assert not inst.target.exists()


# -- fidelity: the stubs match the real tools (darwin only) -------------------------------------


@real_codesign
def test_stubbed_tool_output_matches_the_real_tools(tmp_path):
    real_arm = _sh("sysctl", "-n", "hw.optional.arm64").stdout
    stub_arm = _sh("/bin/bash", "-c", _SYSCTL_STUB).stdout
    assert real_arm.strip() in ("0", "1") and re.fullmatch(r"[01]\n", stub_arm)
    assert re.fullmatch(r"\d+(\.\d+){1,2}\n", _sh("sw_vers", "-productVersion").stdout)
    assert re.fullmatch(r"\d+(\.\d+){1,2}\n", _sh("/bin/bash", "-c", _SW_VERS_STUB).stdout)
    app = _write_bundle(tmp_path)
    real = _sh("defaults", "read", f"{app}/Contents/Info", "CFBundleShortVersionString").stdout
    stub_path = tmp_path / "defaults"
    stub_path.write_text(_DEFAULTS_STUB)
    stub_path.chmod(0o755)
    stub = _sh(str(stub_path), "read", f"{app}/Contents/Info", "CFBundleShortVersionString").stdout
    assert real == stub == f"{VERSION}\n"
    # `unzip -Z` marks a symlink entry with a leading `l`, which the installer relies on.
    z = tmp_path / "s.zip"
    _make_zip(z, [("a", b"x", "file"), ("b", b"a", "symlink")])
    listing = _sh("unzip", "-Z", str(z)).stdout.splitlines()
    assert [ln[0] for ln in listing if ln and ln[0] in "-l"] == ["-", "l"]
    # The installer reads a first install's osascript failure by its -1728 text.
    probe = subprocess.run(
        ["osascript", "-e", 'application id "ai.pipecat.NoSuchApp.fidelity" is running'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode != 0 and "(-1728)" in probe.stderr, probe.stderr
    stub_path = tmp_path / "osascript"
    stub_path.write_text(_OSASCRIPT_STUB)
    stub_path.chmod(0o755)
    stub_probe = subprocess.run(
        [str(stub_path), "-e", "x"],
        env={**os.environ, "STUB_OSA_COUNT": str(tmp_path / "osa.count"), "STUB_OSA_UNKNOWN": "1"},
        capture_output=True,
        text=True,
    )
    assert stub_probe.returncode == probe.returncode and "(-1728)" in stub_probe.stderr


@real_codesign
def test_real_install_end_to_end(throwaway_identity, pinned_installer, tmp_path):
    """Real codesign, ditto, defaults, unzip, sysctl and sw_vers; only curl and osascript are stubbed."""
    sha1, keychain = throwaway_identity
    env = InstallEnv(tmp_path, installer=pinned_installer)
    for name in ("sysctl", "sw_vers", "uname", "defaults", "ditto", "codesign"):
        (env.bin / name).unlink()
    env.rename_tool.unlink()
    zip_path = env.release / f"{TAG}.zip"

    def publish(signer: str | None, *extra: str, executable: bool = True) -> None:
        app = _write_bundle(tmp_path / f"b-{signer}-{len(extra)}-{executable}")
        main = app / "Contents/MacOS/toolbar"
        _write_runnable_main(main)
        _sign(app, signer or "-", *extra, keychain=keychain if signer == sha1 else None)
        if not executable:
            main.chmod(0o644)
        zip_path.unlink(missing_ok=True)
        _sh("ditto", "-c", "-k", "--keepParent", str(app), str(zip_path))
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        (env.release / f"{TAG}.zip.sha256").write_text(f"{digest}  {TAG}.zip\n")

    publish(sha1)
    ok = env.run()
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert f"installed {TAG}: version {VERSION}, build {BUILD}" in ok.stdout
    _sh("codesign", "--verify", "--deep", "--strict", str(env.target))
    _sh(str(env.target / "Contents/MacOS/toolbar"))
    publish(sha1, executable=False)
    before = _snapshot(env.home)
    refused = _check_drop(env, zip_path)
    assert refused.returncode == 1 and "main executable" in refused.stderr
    refused = env.run()
    assert refused.returncode == 1 and "main executable" in refused.stderr
    assert _snapshot(env.home) == before
    _sh(str(env.target / "Contents/MacOS/toolbar"))
    for signer, extra in (
        (None, ()),  # ad hoc
        (None, ("-r", f'=designated => identifier "{BUNDLE_ID}" and certificate leaf = H"{sha1}"')),
    ):
        publish(signer, *extra)
        before = _snapshot(env.home)
        refused = env.run()
        assert refused.returncode != 0, refused.stdout
        assert "pinned tester certificate" in refused.stderr
        assert _snapshot(env.home) == before


# -- tester guide ------------------------------------------------------------------------------

GUIDE = REPO_ROOT / "docs/tester-install.md"
DOCS_README = REPO_ROOT / "docs/README.md"
ROOT_README = REPO_ROOT / "README.md"


def _guide() -> str:
    return " ".join(GUIDE.read_text().split())


def test_guide_has_the_one_liner_with_placeholders_not_a_real_commit():
    text = GUIDE.read_text()
    one_liner = re.search(r"^curl .*$", text, re.MULTILINE)
    assert one_liner, "no curl one-liner"
    # -q first: curl reads ~/.curlrc (which can set `insecure`) unless -q is the first argument.
    # The quotes keep zsh, the default macOS shell, from expanding `=https`.
    assert one_liner.group(0) == (
        "curl -q -fsSL --proto '=https' --proto-redir '=https' "
        "https://raw.githubusercontent.com/pipecat-ai/pipecat-service-toolbar/"
        "<INSTALLER_COMMIT_SHA>/scripts/install_tester.sh | bash -s -- <TAG>"
    )
    assert not re.search(r"\b[0-9a-f]{40}\b", text), (
        "a real commit SHA must not be committed in the guide"
    )
    assert "scripts/install_tester.sh" in text


def test_guide_defines_success_by_the_installed_line():
    """`curl ... | bash` exits 0 when the download fails, so the exit status proves nothing."""
    text = _guide()
    assert "`installed tester-`" in text
    assert "when the download itself fails" in text
    # A missing line is no guarantee that nothing changed: an unverified copy can stay in place.
    assert "nothing was installed" not in text
    assert "did not complete" in text and "may still be in place" in text


def test_guide_covers_the_installers_refusals_and_what_it_leaves():
    text = _guide()
    assert "is an unverified build, do not open it" in text
    assert "until the owner replies" in text
    assert "~/Applications/.tester-install.lock" in text
    assert "~/Applications/.tester-leftover.*" in text


@pytest.mark.parametrize(
    ("message", "steps"),
    [
        # Stop: a build the pin rejects is never retried or worked around.
        ("not signed by the pinned tester certificate", ("Stop", "tell the owner")),
        # Corruption on the way: one retry, then the owner.
        ("the download is corrupt (SHA-256 mismatch)", ("once more", "tell the owner")),
        # The running-app probe failed: quit by hand, then retry.
        ("could not tell whether the app is running", ("quit", "by hand", "run the command again")),
        # macOS App Management stopped the move aside: allow Terminal or remove the app and its
        # rollback copy (removing only the app makes the next run restore the rollback copy and
        # stop at the same move), then retry.
        (
            "could not move the installed app aside",
            (
                "System Settings > Privacy & Security > App Management",
                "allow Terminal",
                "remove the app and its rollback copy by hand",
                "~/Applications/.tester-previous",
                "Removing only the app is not enough",
                "run the command again",
            ),
        ),
    ],
)
def test_guide_says_what_to_do_for_each_refusal_the_installer_prints(message, steps):
    assert message in INSTALLER.read_text(), message
    section = GUIDE.read_text().split("## If the installer refuses", 1)[1].split("\n## ", 1)[0]
    bullets = [" ".join(b.split()) for b in section.split("\n- ")[1:]]
    (bullet,) = [b for b in bullets if b.startswith("`") and message in b.split("`")[1]]
    for step in steps:
        assert step.lower() in bullet.lower(), (step, bullet)


def test_guide_and_development_docs_describe_the_retained_rollback_copy():
    """A successful update keeps the build it replaced at .tester-previous (owner decision)."""
    text = _guide()
    update = " ".join(GUIDE.read_text().split("## Update", 1)[1].split("\n## ", 1)[0].split())
    assert "~/Applications/.tester-previous" in update and "rollback copy" in update
    assert "rollback copy" in " ".join(
        GUIDE.read_text().split("## Uninstall", 1)[1].split("\n## ", 1)[0].split()
    )
    dev = " ".join((REPO_ROOT / "docs/development.md").read_text().split())
    header = INSTALLER.read_text().split("\nset -euo pipefail", 1)[0]
    for doc in (text, dev, " ".join(header.split())):
        assert "rollback copy" in doc
        for stale in ("removes the previous", "previous copy is deleted", "deletes the previous"):
            assert stale not in doc, stale


def test_guide_uninstall_removes_what_an_interrupted_install_leaves():
    section = GUIDE.read_text().split("## Uninstall", 1)[1].split("\n## ", 1)[0]
    assert "~/Applications/.tester-previous" in section
    assert "~/Applications/.tester-install.*" in section
    assert "~/Applications/.tester-leftover.*" in section


def test_development_docs_match_the_tester_scripts():
    text = (REPO_ROOT / "docs/development.md").read_text()
    rows = {
        line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| `")
    }
    assert "install_tester.sh" in rows["`APP_INSTALL_DIR`"]
    assert "package_tester.sh" in rows["`TESTER_APP_BUNDLE`"]
    section = text.split("## Tester drops", 1)[1].split("\n## ", 1)[0]
    assert "`CHANGELOG.md`" in section and "--check-drop <zip> <tag>" in section
    assert "--check-archive" not in text
    assert "needs `on_main: true`" not in section and "does not refuse" in section
    # The packager packages HEAD (clean, pushed); it does not pull. An offline check has no download.
    flat = " ".join(section.split())
    assert (
        "Pull `main`" not in flat
        and "Check out the commit to package (`main` for a real drop)" in flat
    )
    assert "MiB downloaded" not in flat
    # Owner decision: a macOS 15 clean-guest run is required before a drop.
    (macos15,) = [line for line in section.splitlines() if line.strip().startswith("| macOS 15 |")]
    assert macos15.split("|")[2].strip() == "yes", macos15


def test_guide_states_the_gatekeeper_bypass_and_the_audience_limit():
    text = _guide().lower()
    assert "not notarized" in text and "self-signed" in text
    assert "gatekeeper" in text and "deliberate bypass of gatekeeper" in text
    assert "known testers" in text
    assert "directly from the owner" in text
    assert "apple silicon" in text and "macos 15" in text


def test_guide_covers_update_uninstall_feedback_and_the_launch_at_login_step():
    text = _guide()
    for heading in ("## Update", "## Uninstall", "## Feedback"):
        assert heading in GUIDE.read_text()
    assert "Open at Login" in text and "Application Support/Pipecat Service Toolbar" in text
    assert "tag the installer printed" in text


def test_guide_names_open_anyway_only_as_unsupported():
    text = GUIDE.read_text()
    assert "Open Anyway" in text
    for match in re.finditer(r"Open Anyway", text):
        context = " ".join(text[max(0, match.start() - 200) : match.end() + 250].split()).lower()
        assert "not supported" in context or "skips the certificate check" in context, context


def test_guide_explains_the_permission_reset_and_manual_removal_when_switching():
    text = _guide().lower()
    assert "different signing identity" in text
    assert "again once" in text and "permission" in text
    assert "no install receipt" in text and "remove the app by hand" in text
    # Everything a tester install leaves beside the app goes too, not only the app.
    section = GUIDE.read_text().split("## Moving to the Apple-signed release", 1)[1]
    section = " ".join(section.split("\n## ", 1)[0].split())
    for path in (
        "~/Applications/Pipecat Service Toolbar.app",
        "~/Applications/.tester-previous",
        "~/Applications/.tester-install.*",
        "~/Applications/.tester-leftover.*",
    ):
        assert path in section, path


def test_guide_says_an_unverified_copy_is_never_kept_as_the_app_or_rollback_copy():
    text = _guide()
    assert "only if it passes the certificate check" in text
    assert "once the new rollback copy has passed the certificate check" in text
    assert '"do not open it"' in text


def test_guide_is_linked_from_docs_readme_but_not_the_root_readme():
    assert "(tester-install.md)" in DOCS_README.read_text()
    assert "tester-install" not in ROOT_README.read_text()
    for link in re.findall(r"\]\((?!https?:)([^)#]+)", GUIDE.read_text()):
        assert (GUIDE.parent / link).exists(), link
