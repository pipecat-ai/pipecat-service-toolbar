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
            flags=re.MULTILINE,
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
    zipfile.ZipFile(a[2]).extractall(a[3])
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
        if stubs:
            self.stub("ditto", _DITTO_STUB)
            self.stub("codesign", _CODESIGN_COUNTING_STUB)
        self.out = self.root / "dist/tester"

    def stub(self, name: str, text: str) -> None:
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def run(self, **extra: str) -> subprocess.CompletedProcess:
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
        env = {k: v for k, v in env.items() if v != "__unset__"}
        return subprocess.run(
            ["/bin/bash", str(self.root / "scripts/package_tester.sh")],
            cwd=self.root,
            env=env,
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
        "git fetch --quiet origin",
        'die "tag $TAG already exists',
        'resolve_sign_identity.sh" --require',
        'build_app.sh"',
        '--verify-only "$BUNDLE"',
        "ditto -c -k --keepParent",
        '--check-archive "$CHECK/$ZIP"',
        "ditto -x -k",
        '--verify-only "$CHECK/$NAME"',
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


@pytest.mark.parametrize("where", ["remote", "local"])
def test_existing_tag_is_refused(repo, where):
    tag = repo.tag
    if where == "remote":
        _git(repo.root, "tag", tag)
        _git(repo.root, "push", "-q", "origin", tag)
        _git(repo.root, "tag", "-d", tag)
    else:
        _git(repo.root, "tag", tag)
    result = repo.run()
    assert result.returncode != 0 and "already exists" in result.stderr
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


@real_codesign
def test_real_packaging_end_to_end(throwaway_identity, tmp_path):
    """Real ditto and codesign: a bundle signed by the pinned identity packages and verifies."""
    sha1, keychain = throwaway_identity
    pinned = re.sub(
        r'^TESTER_CERT_SHA1=".*"$',
        f'TESTER_CERT_SHA1="{sha1}"',
        INSTALLER.read_text(),
        flags=re.MULTILINE,
    )
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
    for name in ("sysctl", "sw_vers", "uname", "defaults", "ditto", "codesign", "mv"):
        (env.bin / name).unlink()
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
# something outside the installer might while it runs.
_CURL_STUB = """#!/bin/bash
echo "$*" >> "$STUB_CURL_LOG"
[ -z "${STUB_CURL_SLEEP:-}" ] || sleep "$STUB_CURL_SLEEP"
[ -z "${STUB_CURL_RM:-}" ] || rm -rf "$STUB_CURL_RM"
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
[ "${STUB_FAIL_ON_CALL:-0}" != "$n" ] || exit 3
app="${@: -1}"
signer="$(cat "$app/Contents/SIGNER" 2>/dev/null)"
case " $* " in
  *" -R "*) [ "$signer" = good ] || exit 3 ;;
  *) [ "$signer" != broken ] || exit 1 ;;
esac
exit 0
"""

_MV_STUB = """#!/bin/bash
n=$(( $(cat "$STUB_MV_COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_MV_COUNT"
case ",${STUB_MV_FAIL:-}," in *",$n,"*) echo "mv: injected failure $n" >&2; exit 1 ;; esac
exec /bin/mv "$@"
"""

_DITTO_EXTRACT_FAIL = _DITTO_STUB.replace(
    "a = sys.argv[1:]",
    'a = sys.argv[1:]\nif os.environ.get("STUB_DITTO_FAIL"): sys.exit(1)',
)


def _snapshot(root: Path) -> dict[str, str]:
    """Every path under ``root`` with a content digest, to prove nothing else changed."""
    snap: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            snap[rel] = "link:" + os.readlink(path)
        elif path.is_dir():
            snap[rel] = "dir"
        else:
            snap[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
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
                info.external_attr = 0o100644 << 16
                z.writestr(info, data)


def _bundle_entries(signer: str = "good", version: str = VERSION, build: str = BUILD, extra=()):
    plist = plistlib.dumps(
        {
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": build,
        }
    )
    entries = [
        (f"{NAME}/", None, "file"),
        (f"{NAME}/Contents/", None, "file"),
        (f"{NAME}/Contents/Info.plist", plist, "file"),
        (f"{NAME}/Contents/SIGNER", signer.encode(), "file"),
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
            "mv": _MV_STUB,
        }.items():
            path = self.bin / name
            path.write_text(text)
            path.chmod(0o755)
        self.target = self.dest / NAME
        self.logs = {n: tmp_path / f"{n}.log" for n in ("curl", "codesign")}

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
            STUB_OSA_COUNT=str(self.tmp / "osa.count"),
            STUB_MV_COUNT=str(self.tmp / "mv.count"),
        )
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


def test_app_install_dir_is_honoured(inst, tmp_path):
    custom = tmp_path / "custom"
    inst.publish()
    result = inst.run(APP_INSTALL_DIR=str(custom))
    assert result.returncode == 0, result.stderr
    assert (custom / NAME).is_dir() and not inst.target.exists()


def test_update_replaces_the_app_and_reports_old_and_new(inst):
    inst.install_old()
    inst.publish()
    result = inst.run()
    assert result.returncode == 0, result.stderr
    assert f"installed {TAG}: version {VERSION}, build {BUILD}" in result.stdout
    assert "replaced version 0.0.1, build 1" in result.stdout
    assert [p.name for p in inst.dest.iterdir()] == [NAME]
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
    result = inst.run(STUB_MV_FAIL="1")
    assert result.returncode != 0 and "App Management" in result.stderr
    assert _snapshot(inst.home) == before


def test_failing_to_move_the_new_app_in_restores_the_previous_copy(inst):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run(STUB_MV_FAIL="2")
    assert result.returncode != 0 and "previous copy was restored" in result.stderr
    assert _snapshot(inst.home) == before


def test_failing_to_move_the_new_app_in_on_a_first_install_claims_no_restore(inst):
    inst.publish()
    result = inst.run(STUB_MV_FAIL="1")  # no old app, so the first mv is the move-in
    assert result.returncode != 0 and "could not install" in result.stderr
    assert "was restored" not in result.stderr and "no previous copy" in result.stderr
    assert [p.name for p in inst.dest.iterdir()] == []


def test_a_failed_verification_after_the_swap_restores_the_previous_copy(inst):
    inst.install_old()
    inst.publish()
    before = _snapshot(inst.home)
    # codesign calls 1-2 verify the staged copy; call 3 is the post-swap check of the target.
    result = inst.run(STUB_FAIL_ON_CALL="3")
    assert result.returncode != 0 and "previous copy was restored" in result.stderr
    assert _snapshot(inst.home) == before


def test_a_failed_first_install_leaves_no_app_and_no_staging(inst):
    inst.publish()
    result = inst.run(STUB_FAIL_ON_CALL="3")
    assert result.returncode != 0
    assert [p.name for p in inst.dest.iterdir()] == []


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
    assert [p.name for p in inst.dest.iterdir()] == [NAME]


def test_nothing_outside_the_app_bundle_changes_on_success(inst):
    inst.install_old()
    (inst.home / "Library").mkdir()
    (inst.home / "Library/prefs.plist").write_text("keep")
    (inst.dest / "Other.app").mkdir()
    inst.publish()
    before = _snapshot(inst.home)
    assert inst.run().returncode == 0
    after = _snapshot(inst.home)
    app_prefix = f"Applications/{NAME}"
    outside = lambda snap: {k: v for k, v in snap.items() if not k.startswith(app_prefix)}  # noqa: E731
    assert outside(after) == outside(before)


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
# install directory. Start-up state space: app absent/good/bad x previous absent/good x lock
# free/held; then every failure point of the swap, followed by a retry.

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


@pytest.mark.parametrize("lock", ["free", "held"])
@pytest.mark.parametrize("prev", ["absent", "good"])
@pytest.mark.parametrize("app", ["absent", "good", "bad"])
def test_start_up_state_space(inst, app, prev, lock):
    if app != "absent":
        _place(inst, inst.target, app, "1")
    if prev != "absent":
        _place(inst, _prev(inst), prev, "2")
    if lock == "held":
        (inst.dest / LOCK_NAME).mkdir()
    inst.publish()
    before = _snapshot(inst.home)
    result = inst.run()
    if lock == "held":
        assert result.returncode != 0
        assert "another tester install" in result.stderr
        assert str(inst.dest / LOCK_NAME) in result.stderr
        assert _snapshot(inst.home) == before  # the other run's lock and copies stay
        assert not inst.logs["curl"].exists()
        return
    assert not (inst.dest / LOCK_NAME).exists()
    if app == "bad" and prev == "good":
        # The previous copy may be the last good one: nothing is touched, and it is named.
        assert result.returncode != 0
        assert str(_prev(inst)) in result.stderr and "nothing was changed" in result.stderr
        assert _snapshot(inst.home) == before
        assert not inst.logs["curl"].exists()
        return
    assert result.returncode == 0, result.stderr
    assert [p.name for p in inst.dest.iterdir()] == [NAME]
    assert _build_at(inst.target) == BUILD
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
        ({"STUB_MV_FAIL": "1"}, False, "1", None),  # moving the old app aside
        ({"STUB_MV_FAIL": "2"}, False, "1", None),  # moving the new app in; restored
        ({"STUB_MV_FAIL": "2,3"}, False, None, "1"),  # ... and the restore fails
        ({"STUB_FAIL_ON_CALL": "3"}, False, "1", None),  # post-swap check fails; restored
        ({"STUB_FAIL_ON_CALL": "3", "STUB_MV_FAIL": "3"}, False, BUILD, "1"),  # rejected stays
        ({"STUB_FAIL_ON_CALL": "3", "STUB_MV_FAIL": "4"}, False, None, "1"),  # restore fails
        ({"STUB_MV_FAIL": "3"}, True, BUILD, "1"),  # clearing the replaced copy fails
    ],
    ids=[
        "aside",
        "move-in",
        "move-in+restore",
        "verify",
        "verify+reject",
        "verify+restore",
        "clear-replaced",
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
    result = inst.run(STUB_FAIL_ON_CALL="3", STUB_MV_FAIL="2")  # mv 1 moves it in, mv 2 rejects
    assert result.returncode != 0
    assert "could not be moved aside" in result.stderr and "do not open it" in result.stderr
    assert str(inst.target) in result.stderr and "no previous copy" in result.stderr


def test_staging_keeps_the_previous_copy_when_no_app_is_left(inst):
    """Cleanup never deletes a staged copy while neither the app nor .tester-previous exists."""
    inst.install_old()
    _place(inst, _prev(inst), "good", "2")
    inst.publish()
    result = inst.run(STUB_CURL_FAIL="all", STUB_CURL_RM=str(inst.target))
    assert result.returncode != 0
    assert _build_at(_prev(inst)) == "2", result.stderr
    assert _staging_left(inst) == []


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
    assert [p.name for p in inst.dest.iterdir()] == [NAME]
    assert _build_at(inst.target) == BUILD


# -- installer: what extraction writes ---------------------------------------------------------


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


def test_an_archive_at_the_size_limit_is_accepted(inst):
    zip_path = inst.publish(entries=_bundle_entries(extra=[(f"{NAME}/Contents/a", b"x", "file")]))
    assert _run(inst.installer, "--check-archive", str(zip_path)).returncode == 0
    limit = int(
        re.search(r"^MAX_UNZIPPED_BYTES=(\d+)", INSTALLER.read_text(), re.MULTILINE).group(1)
    )
    _with_declared_sizes(zip_path, {f"{NAME}/Contents/a": limit - 4096})
    assert _run(inst.installer, "--check-archive", str(zip_path)).returncode == 0
    _with_declared_sizes(zip_path, {f"{NAME}/Contents/a": limit + 1})
    assert _run(inst.installer, "--check-archive", str(zip_path)).returncode == 1


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


real_ditto = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("ditto") is None, reason="needs macOS ditto"
)


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
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (inst.release / f"{TAG}.zip.sha256").write_text(f"{digest}  {TAG}.zip\n")
    inst.install_old()
    before = _snapshot(inst.home)
    result = inst.run()
    assert result.returncode != 0 and "other than files and folders" in result.stderr, result.stderr
    assert _snapshot(inst.home) == before


def test_check_archive_mode_is_the_archive_gate_alone(inst, tmp_path):
    good = inst.publish()
    result = _run(inst.installer, "--check-archive", str(good), env=inst.env())
    assert result.returncode == 0, result.stderr
    bad = tmp_path / "bad.zip"
    _make_zip(bad, _bundle_entries(extra=[("Other.app/x", b"x", "file")]))
    refused = _run(inst.installer, "--check-archive", str(bad), env=inst.env())
    assert refused.returncode == 1 and "outside" in refused.stderr
    assert _run(inst.installer, "--check-archive", str(tmp_path / "nope.zip")).returncode == 2
    assert _run(inst.installer, "--check-archive").returncode == 2
    assert _run(inst.installer, "--check-archive", str(good), "x").returncode == 2
    assert list(inst.dest.iterdir()) == [] and not inst.logs["curl"].exists()


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
    for name in ("sysctl", "sw_vers", "uname", "defaults", "ditto", "codesign", "mv"):
        (env.bin / name).unlink()
    zip_path = env.release / f"{TAG}.zip"

    def publish(signer: str | None, *extra: str) -> None:
        app = _write_bundle(tmp_path / f"b-{signer}-{len(extra)}")
        _sign(app, signer or "-", *extra, keychain=keychain if signer == sha1 else None)
        zip_path.unlink(missing_ok=True)
        _sh("ditto", "-c", "-k", "--keepParent", str(app), str(zip_path))
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        (env.release / f"{TAG}.zip.sha256").write_text(f"{digest}  {TAG}.zip\n")

    publish(sha1)
    ok = env.run()
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert f"installed {TAG}: version {VERSION}, build {BUILD}" in ok.stdout
    assert _sh("codesign", "--verify", "--deep", "--strict", str(env.target)).returncode == 0
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
    one_liner = re.search(r"^curl .*$", text, re.M)
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
    assert "nothing was installed" in text and "when the download itself fails" in text


def test_guide_uninstall_removes_what_an_interrupted_install_leaves():
    section = GUIDE.read_text().split("## Uninstall", 1)[1].split("\n## ", 1)[0]
    assert "~/Applications/.tester-previous" in section
    assert "~/Applications/.tester-install.*" in section


def test_development_docs_match_the_tester_scripts():
    text = (REPO_ROOT / "docs/development.md").read_text()
    rows = {
        line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| `")
    }
    assert "install_tester.sh" in rows["`APP_INSTALL_DIR`"]
    assert "package_tester.sh" in rows["`TESTER_APP_BUNDLE`"]
    section = text.split("## Tester drops", 1)[1].split("\n## ", 1)[0]
    assert "`CHANGELOG.md`" in section and "--check-archive" in section
    assert "needs `on_main: true`" not in section and "does not refuse" in section


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


def test_guide_is_linked_from_docs_readme_but_not_the_root_readme():
    assert "(tester-install.md)" in DOCS_README.read_text()
    assert "tester-install" not in ROOT_README.read_text()
    for link in re.findall(r"\]\((?!https?:)([^)#]+)", GUIDE.read_text()):
        assert (GUIDE.parent / link).exists(), link
