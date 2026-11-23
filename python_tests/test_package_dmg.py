"""scripts/package_dmg.sh with every Apple tool stubbed (security, codesign, xcrun, spctl, hdiutil,
ditto), in a throwaway git repository. No certificate, network or macOS needed; runs under
/bin/bash (3.2 on macOS)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
TEAM = "ABCDE12345"
HASH = "A" * 40
NAME = f"Developer ID Application: Example Co. ({TEAM})"
APP = "Pipecat Service Toolbar.app"

_DV = (
    "Identifier=ai.pipecat.ServiceToolbar\n"
    "CodeDirectory v=20500 size=1 flags=0x10000(runtime) hashes=1+7 location=embedded\n"
    f"Authority={NAME}\n"
    "Authority=Developer ID Certification Authority\n"
    "Authority=Apple Root CA\n"
    "Timestamp=23 Nov 2026 at 14:11:09\n"
    f"TeamIdentifier={TEAM}\n"
)

_STUBS = {
    "security": r"""
echo "security $*" >> "$STUB_LOG"
case "$1" in
  find-identity) printf '  1) %s "%s"\n     1 valid identities found\n' "$STUB_HASH" "$STUB_NAME" ;;
  find-certificate) if [ -n "${STUB_CERTS:-}" ]; then cat "$STUB_CERTS"; fi ;;
  *) exit 0 ;;
esac
""",
    "codesign": r"""
echo "codesign $*" >> "$STUB_LOG"
case " $* " in
  *" --verify "*) exit 0 ;;
  *" --entitlements "*) echo '<plist><dict/></plist>' ;;
  *" -dvvv "*) cat "$STUB_DV" >&2 ;;
  *" --sign "*) exit 0 ;;
  *) exit 64 ;;
esac
""",
    "xcrun": r"""
echo "xcrun $*" >> "$STUB_LOG"
case "$1 $2" in
  "notarytool history") exit "${STUB_HISTORY_RC:-0}" ;;
  "notarytool submit")
    n=$(( $(cat "$STUB_DIR/n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STUB_DIR/n"
    status="${STUB_NOTARY_STATUS:-Accepted}"
    if [ "$n" = "2" ] && [ -n "${STUB_NOTARY_STATUS_2:-}" ]; then status="$STUB_NOTARY_STATUS_2"; fi
    printf '{"id":"sub-%s","status":"%s","message":"m"}\n' "$n" "$status"
    [ "$status" = "Accepted" ] || exit 1 ;;
  "notarytool log") echo '{"issues":[{"message":"stub issue"}]}' > "${@: -1}" ;;
  "stapler staple"|"stapler validate") exit "${STUB_STAPLE_RC:-0}" ;;
  *) exit 64 ;;
esac
""",
    "spctl": r"""
echo "spctl $*" >> "$STUB_LOG"
printf 'accepted\nsource=%s\n' "${STUB_SPCTL_SOURCE:-Notarized Developer ID}" >&2
""",
    "hdiutil": r"""
echo "hdiutil $*" >> "$STUB_LOG"
case "$1" in
  create)
    src=""; prev=""; for a in "$@"; do [ "$prev" = "-srcfolder" ] && src="$a"; prev="$a"; out="$a"; done
    echo "$src" > "$STUB_DIR/src"; ls -A "$src" > "$STUB_DIR/dmg-contents"; echo dmg > "$out" ;;
  attach)
    mnt=""; prev=""; for a in "$@"; do [ "$prev" = "-mountpoint" ] && mnt="$a"; prev="$a"; done
    src="$(cat "$STUB_DIR/src")"; cp -R "$src/." "$mnt/" ;;
  detach)
    # STUB_DETACH_BUSY=<n>: the first n plain detaches fail like a busy volume; -force never does.
    if [ "${STUB_DETACH_BUSY:-0}" -gt 0 ] && [[ " $* " != *" -force "* ]]; then
      n=$(( $(cat "$STUB_DIR/busy" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STUB_DIR/busy"
      [ "$n" -le "$STUB_DETACH_BUSY" ] && exit 1
    fi
    echo detached >> "$STUB_DIR/detached" ;;
  *) exit 64 ;;
esac
""",
    "sleep": "exit 0\n",
    "ditto": r"""
echo "ditto $*" >> "$STUB_LOG"
if [ "$1" = "-c" ]; then echo zip > "${@: -1}"; else cp -R "$1" "$2"; fi
""",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class Env:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.root = tmp_path / "repo"
        self.bin = tmp_path / "bin"
        self.stub_dir = tmp_path / "stub"
        self.log = tmp_path / "log"
        for d in (self.root / "scripts/lib", self.bin, self.stub_dir):
            d.mkdir(parents=True)
        for rel in (
            "package_dmg.sh",
            "resolve_release_identity.sh",
            "verify_release_signature.sh",
            "lib/release_env.sh",
        ):
            shutil.copy(SCRIPTS / rel, self.root / "scripts" / rel)
            (self.root / "scripts" / rel).chmod(0o755)
        for name, body in _STUBS.items():
            (self.bin / name).write_text("#!/bin/bash\n" + body)
            (self.bin / name).chmod(0o755)
        (self.tmp / "dv").write_text(_DV)
        bundle = tmp_path / "prebuilt" / APP
        (bundle / "Contents/Resources/bin").mkdir(parents=True)
        (bundle / "Contents/Resources/bin/uv").write_text("")
        (bundle / "Contents/Resources/bin/uv").chmod(0o755)
        self.bundle = bundle
        (self.root / "app-version.json").write_text(json.dumps({"version": "1.2.3", "build": 42}))
        (self.root / ".gitignore").write_text("dist/\n.env\n")
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "init")

    def run(self, *args: str, dotenv: str | None = f"APPLE_TEAM_ID={TEAM}\n", **extra: str):
        if dotenv is not None:
            (self.root / ".env").write_text(dotenv)
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "STUB_LOG": str(self.log),
            "STUB_DIR": str(self.stub_dir),
            "STUB_DV": str(self.tmp / "dv"),
            "STUB_HASH": HASH,
            "STUB_NAME": NAME,
            "DMG_APP_BUNDLE": str(self.bundle),
            **extra,
        }
        return subprocess.run(
            ["/bin/bash", str(self.root / "scripts/package_dmg.sh"), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    @property
    def out(self) -> Path:
        return self.root / "dist/dmg"

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def published(self) -> list[str]:
        return sorted(p.name for p in self.out.iterdir()) if self.out.exists() else []


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


DMG = "Pipecat-Service-Toolbar-1.2.3-b42-TESTBUNDLE.dmg"


def test_happy_path_publishes_dmg_checksum_and_manifest(env):
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert env.published() == [
        DMG,
        f"{DMG}.sha256",
        "Pipecat-Service-Toolbar-1.2.3-b42-TESTBUNDLE.manifest.json",
    ]
    manifest = json.loads(
        (env.out / "Pipecat-Service-Toolbar-1.2.3-b42-TESTBUNDLE.manifest.json").read_text()
    )
    assert manifest["dmg"] == DMG
    assert manifest["version"] == "1.2.3" and manifest["build"] == 42
    assert manifest["commit"] == _git(env.root, "rev-parse", "HEAD")
    assert manifest["notarized"] is True
    assert manifest["notary_submissions"] == {"app": "sub-1", "dmg": "sub-2"}
    assert manifest["test_bundle"] is True
    sha = (env.out / f"{DMG}.sha256").read_text().split()
    assert sha == [manifest["sha256"], DMG]
    # No scratch folder is left behind, and the mount was detached.
    assert not [p for p in env.out.iterdir() if p.name.startswith(".")]
    assert (env.stub_dir / "detached").exists()


@pytest.mark.parametrize(
    ("busy", "forced"),
    [
        pytest.param("2", False, id="busy-twice-then-free"),
        pytest.param("9", True, id="always-busy"),
    ],
)
def test_a_busy_volume_is_retried_then_forced_never_fatal(env, busy, forced):
    # The volume was just assessed and may still be busy; the finished notarizations must not be
    # thrown away over it.
    r = env.run(STUB_DETACH_BUSY=busy)
    assert r.returncode == 0, r.stderr
    assert DMG in env.published()
    detaches = [c for c in env.calls() if c.startswith("hdiutil detach")]
    assert any("-force" in c for c in detaches) is forced


def test_order_notarize_app_staple_then_dmg_sign_notarize_staple(env):
    assert env.run().returncode == 0
    calls = env.calls()

    def at(prefix: str, start: int = 0) -> int:
        return next(i for i, c in enumerate(calls) if i >= start and c.startswith(prefix))

    history = at("xcrun notarytool history")
    app_submit = at("xcrun notarytool submit")
    app_staple = at("xcrun stapler staple", app_submit)
    create = at("hdiutil create")
    sign_dmg = at("codesign --force --sign", create)
    dmg_submit = at("xcrun notarytool submit", sign_dmg)
    dmg_staple = at("xcrun stapler staple", dmg_submit)
    attach = at("hdiutil attach", dmg_staple)
    assert history < app_submit < app_staple < create < sign_dmg < dmg_submit < dmg_staple < attach
    assert f"--sign {HASH} --timestamp" in calls[sign_dmg]
    assert "-format ULFO" in calls[create] and "-fs APFS" in calls[create]


def test_dmg_holds_the_app_and_an_applications_link(env):
    assert env.run().returncode == 0
    assert (env.stub_dir / "dmg-contents").read_text().split("\n")[:2] == ["Applications", APP]


def test_name_gets_the_short_sha_off_the_version_commit(env):
    (env.root / "README").write_text("x")
    _git(env.root, "add", "README")
    _git(env.root, "commit", "-q", "-m", "later")
    short = _git(env.root, "rev-parse", "--short=7", "HEAD")
    assert env.run().returncode == 0
    assert f"Pipecat-Service-Toolbar-1.2.3-b42-{short}-TESTBUNDLE.dmg" in env.published()


def test_rehearsal_skips_apple_and_says_so(env):
    r = env.run(DMG_SKIP_NOTARIZE="1")
    assert r.returncode == 0, r.stderr
    assert "Pipecat-Service-Toolbar-1.2.3-b42-UNNOTARIZED-TESTBUNDLE.dmg" in env.published()
    assert not [
        c for c in env.calls() if c.startswith(("xcrun notarytool", "xcrun stapler", "spctl"))
    ]
    manifest = json.loads(
        (
            env.out / "Pipecat-Service-Toolbar-1.2.3-b42-UNNOTARIZED-TESTBUNDLE.manifest.json"
        ).read_text()
    )
    assert manifest["notarized"] is False
    assert "Do not share" in r.stdout


def test_a_rebuild_never_overwrites_a_published_dmg(env):
    # The first DMG may already be shared; a rebuild would change its bytes under the same name.
    assert env.run().returncode == 0
    before = {p.name: p.read_bytes() for p in env.out.iterdir()}
    env.log.write_text("")
    r = env.run()
    assert r.returncode != 0
    assert "already exists" in r.stderr
    assert {p.name: p.read_bytes() for p in env.out.iterdir()} == before
    # Refused before any build or Apple call.
    assert not [c for c in env.calls() if c.startswith(("xcrun notarytool submit", "hdiutil"))]


def test_a_rehearsal_may_overwrite_its_own_earlier_rehearsal(env):
    assert env.run(DMG_SKIP_NOTARIZE="1").returncode == 0
    assert env.run(DMG_SKIP_NOTARIZE="1").returncode == 0


def test_a_prebuilt_bundle_is_named_as_a_test_artifact(env):
    # A leftover DMG_APP_BUNDLE must not produce a normally named DMG.
    assert env.run().returncode == 0
    assert all("-TESTBUNDLE" in n for n in env.published())


@pytest.mark.parametrize(
    ("extra", "label", "submission"),
    [
        pytest.param({"STUB_NOTARY_STATUS": "Invalid"}, "app", "sub-1", id="app-invalid"),
        pytest.param({"STUB_NOTARY_STATUS_2": "Invalid"}, "dmg", "sub-2", id="dmg-invalid"),
    ],
)
def test_rejected_notarization_saves_the_log_and_publishes_nothing(env, extra, label, submission):
    r = env.run(**extra)
    assert r.returncode == 1
    assert submission in r.stderr and "Invalid" in r.stderr
    assert env.published() == [
        f"Pipecat-Service-Toolbar-1.2.3-b42-TESTBUNDLE.{label}.notary-log.json"
    ]


def test_timeout_without_a_result_publishes_nothing(env):
    r = env.run(STUB_NOTARY_STATUS="In Progress")
    assert r.returncode == 1 and "In Progress" in r.stderr
    assert not [p for p in env.published() if p.endswith((".dmg", ".manifest.json"))]


def test_staple_failure_publishes_nothing(env):
    r = env.run(STUB_STAPLE_RC="65")
    assert r.returncode != 0
    assert env.published() == []


def test_gatekeeper_must_see_it_as_notarized(env):
    r = env.run(STUB_SPCTL_SOURCE="Developer ID")
    assert r.returncode == 1 and "Gatekeeper does not see" in r.stderr
    assert env.published() == []


@pytest.mark.parametrize(
    ("setup", "extra", "dotenv", "message"),
    [
        pytest.param("dirty", {}, f"APPLE_TEAM_ID={TEAM}\n", "not clean", id="dirty-tree"),
        pytest.param(None, {}, "", "APPLE_TEAM_ID is not set", id="no-team"),
        pytest.param(
            None,
            {"STUB_HASH": "B" * 40, "STUB_NAME": "Code Signing"},
            f"APPLE_TEAM_ID={TEAM}\n",
            "no valid Developer ID",
            id="no-identity",
        ),
        pytest.param(
            None,
            {"STUB_HISTORY_RC": "1"},
            f"APPLE_TEAM_ID={TEAM}\n",
            "notary profile",
            id="no-profile",
        ),
    ],
)
def test_preflight_refuses_before_any_work(env, setup, extra, dotenv, message):
    if setup == "dirty":
        (env.root / "app-version.json").write_text('{"version": "1.2.3", "build": 43}')
    r = env.run(dotenv=dotenv, **extra)
    assert r.returncode != 0
    assert message in r.stderr
    assert not [
        c for c in env.calls() if c.startswith(("hdiutil", "ditto", "xcrun notarytool submit"))
    ]
    assert env.published() == []


def test_doctor_reports_ready_and_builds_nothing(env):
    r = env.run("--doctor")
    assert r.returncode == 0, r.stderr
    assert "Ready: just app-dmg" in r.stdout and HASH in r.stdout
    assert not [
        c for c in env.calls() if c.startswith(("hdiutil", "ditto", "xcrun notarytool submit"))
    ]


def test_doctor_warns_of_a_certificate_expiring_soon(env, tmp_path):
    # The listing is larger than a pipe buffer and the identity's certificate comes first: an
    # early-exiting reader would SIGPIPE `security` and, under pipefail, skip the check silently.
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not available")
    cert = tmp_path / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "10"]
        + ["-subj", "/CN=stub", "-keyout", str(tmp_path / "key.pem"), "-out", str(cert)],
        check=True,
        capture_output=True,
    )
    other = f"SHA-256 hash: {'0' * 64}\nSHA-1 hash: {'B' * 40}\n"
    other += "-----BEGIN CERTIFICATE-----\n" + "A" * 64 + "\n-----END CERTIFICATE-----\n"
    (tmp_path / "certs").write_text(
        f"SHA-256 hash: {'0' * 64}\nSHA-1 hash: {HASH}\n{cert.read_text()}{other * 2000}"
    )
    r = env.run("--doctor", STUB_CERTS=str(tmp_path / "certs"))
    assert r.returncode == 0, r.stderr
    assert re.search(r"expires in (9|10) days", r.stdout), r.stdout


def test_doctor_names_the_missing_profile(env):
    r = env.run("--doctor", STUB_HISTORY_RC="1")
    assert r.returncode == 1 and "store-credentials" in r.stderr


def test_bad_arguments_are_a_usage_error(env):
    assert env.run("--bogus").returncode == 2


def test_never_writes_version_or_changelog(env):
    before = (env.root / "app-version.json").read_text()
    assert env.run().returncode == 0
    assert (env.root / "app-version.json").read_text() == before
    assert _git(env.root, "status", "--porcelain") == ""


def test_recipes_call_the_packager():
    justfile = (REPO_ROOT / "justfile").read_text()
    assert "app-dmg:\n    scripts/package_dmg.sh\n" in justfile
    assert "app-dmg-doctor:\n    scripts/package_dmg.sh --doctor\n" in justfile
