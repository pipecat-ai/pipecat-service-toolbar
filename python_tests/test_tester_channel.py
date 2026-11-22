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


# -- packager ----------------------------------------------------------------------------------

NAME = "Pipecat Service Toolbar.app"
VERSION, BUILD = "9.8.7", "321"

_DITTO_STUB = """#!/usr/bin/env python3
import os, sys, zipfile
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
elif a[:2] == ["-x", "-k"]:
    zipfile.ZipFile(a[2]).extractall(a[3])
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
    match = re.search(rf"^{re.escape(name)}[^\n]*:\n((?:[ \t]+.*\n|\n)+)", text, re.M)
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
    names = sorted(p.name for p in repo.out.iterdir())
    assert not any(n.endswith((".sha256", ".manifest.json")) for n in names), names


def test_bundle_version_must_match_app_version_json(repo):
    shutil.rmtree(repo.bundle)
    repo.bundle = _write_bundle(repo.tmp / "bundle2", version="0.0.1")
    result = repo.run()
    assert result.returncode != 0 and "version" in result.stderr


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
    pin = re.search(r'^TESTER_CERT_SHA1="([^"]*)"$', INSTALLER.read_text(), re.M).group(1)
    assert manifest["cert_sha1"] == pin
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


@real_codesign
def test_real_packaging_end_to_end(throwaway_identity, tmp_path):
    """Real ditto and codesign: a bundle signed by the pinned identity packages and verifies."""
    sha1, keychain = throwaway_identity
    pinned = re.sub(
        r'^TESTER_CERT_SHA1=".*"$', f'TESTER_CERT_SHA1="{sha1}"', INSTALLER.read_text(), flags=re.M
    )
    repo = PackagerRepo(tmp_path, installer_text=pinned, stubs=False)
    repo.bundle = _write_bundle(tmp_path / "signed")
    _sign(repo.bundle, sha1, keychain=keychain)
    result = repo.run()
    assert result.returncode == 0, result.stderr + result.stdout
    manifest = _consistent_real(repo, sha1)
    assert manifest["cert_sha1"] == sha1
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
