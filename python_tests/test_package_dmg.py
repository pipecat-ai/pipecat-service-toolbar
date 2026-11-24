"""scripts/package_dmg.sh with every Apple tool stubbed (security, codesign, xcrun, spctl, hdiutil,
ditto) and scripts/build_app.sh replaced by a recorder, in a throwaway git repository. No
certificate, network or macOS needed; runs under /bin/bash (3.2 on macOS)."""

from __future__ import annotations

import hashlib
import json
import plistlib
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
    # STUB_NOTARY_EMPTY=1: notarytool fails before printing any result.
    if [ -n "${STUB_NOTARY_EMPTY:-}" ]; then echo "network down" >&2; exit 1; fi
    status="${STUB_NOTARY_STATUS:-Accepted}"
    if [ "$n" = "2" ] && [ -n "${STUB_NOTARY_STATUS_2:-}" ]; then status="$STUB_NOTARY_STATUS_2"; fi
    printf '{"id":"sub-%s","status":"%s","message":"m"}\n' "$n" "$status"
    [ "$status" = "Accepted" ] || exit 1 ;;
  "notarytool log")
    # Apple has no log for an unfinished submission.
    case "${STUB_NOTARY_STATUS:-}${STUB_NOTARY_STATUS_2:-}" in
      *"In Progress"*) echo "Submission log is not yet available" >&2; exit 1 ;;
    esac
    echo '{"issues":[{"message":"stub issue"}]}' > "${@: -1}" ;;
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
    # STUB_DURING_DETACH: a shell command run once here, after the preflight and before publishing
    # (stands in for another run of the same commit publishing meanwhile).
    if [ -n "${STUB_DURING_DETACH:-}" ] && [ ! -e "$STUB_DIR/during" ]; then
      : > "$STUB_DIR/during"; /bin/bash -c "$STUB_DURING_DETACH"
    fi
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
# STUB_BEFORE_DITTO: a shell command run before staging the app (another run replacing build/
# after the packager's Git check).
if [ "$1" != "-c" ] && [ -n "${STUB_BEFORE_DITTO:-}" ]; then /bin/bash -c "$STUB_BEFORE_DITTO"; fi
if [ "$1" = "-c" ]; then echo zip > "${@: -1}"; else cp -R "$1" "$2"; fi
""",
    # The real ln, except for publishing hard links (two arguments, no -s) when STUB_LN asks:
    #   nolinks   fail like a file system without hard links;
    #   dir-race  a directory appears at the name just before ln (ln then links into it);
    #   term-<n>  after the n-th publishing link succeeds, TERM the packager (an interrupt).
    "ln": r"""
if [ "$1" = "-s" ] || [ "$#" -ne 2 ] || [ -z "${STUB_LN:-}" ]; then exec /bin/ln "$@"; fi
n=$(( $(cat "$STUB_DIR/ln" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STUB_DIR/ln"
case "$STUB_LN" in
  nolinks) echo "ln: $2: Operation not supported" >&2; exit 1 ;;
  dir-race) [ "$n" = 1 ] && mkdir "$2"; exec /bin/ln "$@" ;;
  term-*)
    /bin/ln "$@" || exit
    if [ "$n" = "${STUB_LN#term-}" ]; then
      # The outermost ancestor running the packager (ln may run in a command substitution).
      pid=$PPID; target=""
      while [ "${pid:-1}" -gt 1 ]; do
        case "$(ps -o command= -p "$pid")" in *package_dmg.sh*) target=$pid ;; *) break ;; esac
        pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
      done
      kill -TERM "$target"
    fi ;;
esac
""",
}


# Stands in for scripts/build_app.sh: records what package_dmg.sh hands it, optionally runs
# STUB_BUILD_HOOK (something changing the checkout mid-build), and leaves a bundle in build/
# whose Info.plist carries app-version.json's version and build, as the real script's does.
_BUILD_APP = r"""#!/bin/bash
set -e
printf 'SIGN_IDENTITY=%s\nSIGN_MODE=%s\nCONFIG=%s\n' "${SIGN_IDENTITY:-}" "${SIGN_MODE:-}" \
  "${CONFIG:-}" > "$STUB_DIR/build-env"
if [ -n "${STUB_BUILD_HOOK:-}" ]; then /bin/bash -c "$STUB_BUILD_HOOK"; fi
root="$(cd "$(dirname "$0")/.." && pwd)"
app="$root/build/Pipecat Service Toolbar.app"
mkdir -p "$app/Contents/Resources/bin"
: > "$app/Contents/Resources/bin/uv"
chmod 755 "$app/Contents/Resources/bin/uv"
python3 -c 'import json,plistlib,sys
v = json.load(open(sys.argv[1]))
plistlib.dump({"CFBundleShortVersionString": str(v["version"]), "CFBundleVersion": str(v["build"])},
              open(sys.argv[2], "wb"))' "$root/app-version.json" "$app/Contents/Info.plist"
"""


def _write_plist(app: Path, version: object, build: object) -> None:
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": str(version), "CFBundleVersion": str(build)})
    )


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
        (self.root / "scripts/build_app.sh").write_text(_BUILD_APP)
        (self.root / "scripts/build_app.sh").chmod(0o755)
        for name, body in _STUBS.items():
            (self.bin / name).write_text("#!/bin/bash\n" + body)
            (self.bin / name).chmod(0o755)
        (self.tmp / "dv").write_text(_DV)
        bundle = tmp_path / "prebuilt" / APP
        (bundle / "Contents/Resources/bin").mkdir(parents=True)
        (bundle / "Contents/Resources/bin/uv").write_text("")
        (bundle / "Contents/Resources/bin/uv").chmod(0o755)
        _write_plist(bundle, "1.2.3", 42)
        self.bundle = bundle
        (self.root / "app-version.json").write_text(json.dumps({"version": "1.2.3", "build": 42}))
        (self.root / ".gitignore").write_text("dist/\n.env\nbuild/\n")
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "init")

    def run(
        self,
        *args: str,
        dotenv: str | None = f"APPLE_TEAM_ID={TEAM}\n",
        bundle: bool = True,
        script: str | None = None,
        **extra: str,
    ):
        """bundle=True packages the prebuilt test bundle (DMG_APP_BUNDLE); False runs build_app.sh.
        script: how to name the packager on the command line (default: its absolute path)."""
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
            **({"DMG_APP_BUNDLE": str(self.bundle)} if bundle else {}),
            **extra,
        }
        return subprocess.run(
            ["/bin/bash", script or str(self.root / "scripts/package_dmg.sh"), *args],
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

    def set_version(self, version: object, build: object) -> None:
        (self.root / "app-version.json").write_text(
            json.dumps({"version": version, "build": build})
        )
        _git(self.root, "commit", "-q", "-am", "version")
        _write_plist(self.bundle, version, build)


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
    # The checksum and manifest both describe the published bytes.
    digest = hashlib.sha256((env.out / DMG).read_bytes()).hexdigest()
    assert (env.out / f"{DMG}.sha256").read_text() == f"{digest}  {DMG}\n"
    assert manifest["sha256"] == digest
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
    # A --wait timeout leaves the submission In Progress: Apple has no log yet, so the error must
    # not name a log file; it says how to poll the submission instead.
    r = env.run(STUB_NOTARY_STATUS="In Progress")
    assert r.returncode == 1 and "In Progress" in r.stderr
    assert "xcrun notarytool info sub-1 --keychain-profile pipecat-notary" in r.stderr
    assert "notary-log.json" not in r.stderr
    assert env.published() == []


def test_notarytool_failing_without_a_result_publishes_nothing(env):
    r = env.run(STUB_NOTARY_EMPTY="1")
    assert r.returncode == 1
    assert "notarization of the app failed (exit 1)" in r.stderr and "network down" in r.stderr
    assert "notarytool log" not in "\n".join(env.calls())
    assert env.published() == []


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


# --- the real build handoff (no DMG_APP_BUNDLE) -------------------------------------------

PLAIN = "Pipecat-Service-Toolbar-1.2.3-b42"


def test_build_app_gets_the_resolved_identity_in_release_mode(env):
    r = env.run(bundle=False)
    assert r.returncode == 0, r.stderr
    env_lines = (env.stub_dir / "build-env").read_text().splitlines()
    assert env_lines == [f"SIGN_IDENTITY={HASH}", "SIGN_MODE=release", "CONFIG=release"]
    # The bundle build_app.sh left in build/ is what was staged and packaged.
    assert any(c.startswith("ditto ") and f"/build/{APP}" in c for c in env.calls())
    assert env.published() == [f"{PLAIN}.dmg", f"{PLAIN}.dmg.sha256", f"{PLAIN}.manifest.json"]
    manifest = json.loads((env.out / f"{PLAIN}.manifest.json").read_text())
    assert manifest["test_bundle"] is False and manifest["notarized"] is True
    assert manifest["notary_submissions"] == {"app": "sub-1", "dmg": "sub-2"}


# Naming and the closing message over the whole (rehearsal, test bundle) space: only a notarized
# build of the commit gets a plain name and is called shareable.
@pytest.mark.parametrize(
    ("skip", "bundle", "suffix", "message"),
    [
        pytest.param(False, False, "", "Share the .dmg", id="notarized-build"),
        pytest.param(False, True, "-TESTBUNDLE", "TEST BUNDLE", id="notarized-test-bundle"),
        pytest.param(True, False, "-UNNOTARIZED", "REHEARSAL", id="rehearsal-build"),
        pytest.param(
            True, True, "-UNNOTARIZED-TESTBUNDLE", "REHEARSAL", id="rehearsal-test-bundle"
        ),
    ],
)
def test_name_and_message_say_whether_it_may_be_shared(env, skip, bundle, suffix, message):
    r = env.run(bundle=bundle, **({"DMG_SKIP_NOTARIZE": "1"} if skip else {}))
    assert r.returncode == 0, r.stderr
    assert f"{PLAIN}{suffix}.dmg" in env.published()
    assert message in r.stdout
    shareable = not skip and not bundle
    assert ("Share the" in r.stdout) is shareable
    assert ("Do not share" in r.stdout) is not shareable


# --- provenance: the build must see the commit the name records ---------------------------


@pytest.mark.parametrize(
    "hook",
    [
        pytest.param("git commit -q --allow-empty -m moved", id="new-commit"),
        pytest.param("echo x > README", id="untracked-file"),
        pytest.param(
            """echo '{"version": "9.9.9", "build": 1}' > app-version.json""", id="edited-version"
        ),
        pytest.param(
            "git config status.showUntrackedFiles no && mkdir -p Sources && echo x > Sources/New.swift",
            id="untracked-file-hidden-by-config",
        ),
    ],
)
def test_a_checkout_changed_during_the_build_is_refused(env, hook):
    r = env.run(bundle=False, STUB_BUILD_HOOK=hook)
    assert r.returncode == 1
    assert "during the build" in r.stderr and "Nothing was published" in r.stderr
    # Refused before spending a notarization.
    assert not [c for c in env.calls() if c.startswith(("xcrun notarytool submit", "hdiutil"))]
    assert env.published() == []


def test_an_untracked_file_hidden_by_config_is_refused_before_any_work(env):
    _git(env.root, "config", "status.showUntrackedFiles", "no")
    (env.root / "Sources").mkdir()
    (env.root / "Sources/New.swift").write_text("x")
    r = env.run()
    assert r.returncode == 1 and "not clean" in r.stderr
    assert not [c for c in env.calls() if c.startswith(("hdiutil", "ditto"))]


def test_an_ignored_source_file_is_refused_before_any_work(env):
    # SwiftPM compiles every .swift under the target directory, ignored by git or not.
    extra = env.root / "Sources/Target/build/Extra.swift"
    extra.parent.mkdir(parents=True)
    extra.write_text("x")
    r = env.run()
    assert r.returncode == 1 and "git-ignored Swift files" in r.stderr
    assert "Sources/Target/build/Extra.swift" in r.stderr
    assert not [
        c for c in env.calls() if c.startswith(("hdiutil", "ditto", "xcrun notarytool submit"))
    ]
    assert env.published() == []


def test_the_ignored_source_check_holds_when_git_hides_untracked_files(env):
    _git(env.root, "config", "status.showUntrackedFiles", "no")
    extra = env.root / "Sources/Target/build/Extra.swift"
    extra.parent.mkdir(parents=True)
    extra.write_text("x")
    r = env.run()
    assert r.returncode == 1 and "git-ignored Swift files" in r.stderr


def test_an_ignored_ds_store_under_sources_is_harmless(env):
    (env.root / ".gitignore").write_text("dist/\n.env\nbuild/\n.DS_Store\n")
    _git(env.root, "commit", "-q", "-am", "ignore .DS_Store")
    (env.root / "Sources/Target").mkdir(parents=True)
    (env.root / "Sources/Target/.DS_Store").write_text("x")
    r = env.run()
    assert r.returncode == 0, r.stderr


def test_ignored_build_output_outside_sources_is_allowed(env):
    (env.root / "dist").mkdir(exist_ok=True)
    (env.root / "build/Extra.swift").parent.mkdir(exist_ok=True)
    (env.root / "build/Extra.swift").write_text("x")
    r = env.run()
    assert r.returncode == 0, r.stderr


def test_version_and_commit_come_from_the_same_checkout(env):
    # A version-bump commit lands just before the packager reads HEAD: the name and manifest must
    # carry the version of the commit they record.
    real = shutil.which("git")
    (env.bin / "git").write_text(
        "#!/bin/bash\n"
        f'if [ "$#" = 2 ] && [ "$1 $2" = "rev-parse HEAD" ] && [ ! -e "$STUB_DIR/bumped" ]; then\n'
        '  : > "$STUB_DIR/bumped"\n'
        """  echo '{"version": "9.9.9", "build": 1}' > app-version.json\n"""
        f"  '{real}' commit -q -am bump\n"
        "fi\n"
        f"exec '{real}' \"$@\"\n"
    )
    (env.bin / "git").chmod(0o755)
    r = env.run(bundle=False)
    assert r.returncode == 0, r.stderr
    (manifest_path,) = env.out.glob("*.manifest.json")
    manifest = json.loads(manifest_path.read_text())
    committed = json.loads(_git(env.root, "show", f"{manifest['commit']}:app-version.json"))
    assert (manifest["version"], manifest["build"]) == (committed["version"], committed["build"])
    assert manifest_path.name == "Pipecat-Service-Toolbar-9.9.9-b1.manifest.json"


_APP_PLIST = f"build/{APP}/Contents/Info.plist"


@pytest.mark.parametrize(
    ("bundle", "replace"),
    [
        pytest.param(
            False,
            f"""python3 -c 'import plistlib; plistlib.dump({{"CFBundleShortVersionString": "9.9.9",
 "CFBundleVersion": "7"}}, open("{_APP_PLIST}", "wb"))'""",
            id="build-replaced",
        ),
        pytest.param(False, f"rm '{_APP_PLIST}'", id="no-plist"),
        pytest.param(True, None, id="test-bundle-of-another-version"),
    ],
)
def test_the_staged_app_must_carry_the_commits_version(env, bundle, replace):
    # build/ is shared: another run may replace the app after the Git check.
    if bundle:
        _write_plist(env.bundle, "1.2.3", 41)
    r = env.run(bundle=bundle, **({"STUB_BEFORE_DITTO": replace} if replace else {}))
    assert r.returncode == 1
    assert "not 1.2.3 build 42" in r.stderr and "Nothing was published" in r.stderr
    assert not [c for c in env.calls() if c.startswith(("xcrun notarytool submit", "hdiutil"))]
    assert env.published() == []


def test_a_relative_invocation_works_with_cdpath_set(env):
    r = env.run("--doctor", script="scripts/package_dmg.sh", CDPATH=".")
    assert r.returncode == 0, r.stderr
    assert "Ready: just app-dmg" in r.stdout


# --- publish contract: exclusive, all or nothing, retry works ------------------------------

_SET = [f"{DMG}", f"{DMG}.sha256", "Pipecat-Service-Toolbar-1.2.3-b42-TESTBUNDLE.manifest.json"]


def _snapshot(path: Path) -> object:
    """What is at path, without following a final symlink."""
    if path.is_symlink():
        return ("link", str(path.readlink()))
    if path.is_dir():
        return ("dir", sorted(p.name for p in path.iterdir()))
    return ("file", path.read_bytes()) if path.exists() else None


# Destination states another party can create after this run's preflight check. Each is refused,
# left exactly as it was, and none of this run's files remain; once moved away, a retry publishes.
_FOREIGN = {
    "regular-file": "echo other-run > '{p}'",
    "directory": "mkdir '{p}'",
    "dangling-symlink": "ln -s nowhere '{p}'",
    "symlink-to-directory": "mkdir -p '{elsewhere}' && ln -s '{elsewhere}' '{p}'",
}


@pytest.mark.parametrize("taken", _SET, ids=["dmg", "sha256", "manifest"])
@pytest.mark.parametrize("state", list(_FOREIGN))
def test_publish_refuses_any_existing_name_and_leaves_nothing_partial(env, state, taken):
    planted = env.out / taken
    elsewhere = env.tmp / "elsewhere"
    plant = _FOREIGN[state].format(p=planted, elsewhere=elsewhere)
    r = env.run(STUB_DURING_DETACH=plant)
    assert r.returncode == 1
    assert f"cannot publish dist/dmg/{taken}" in r.stderr
    # The other party's entry is untouched (nothing linked into a directory), and none of this
    # run's files remain.
    assert env.published() == [taken]
    reference = env.tmp / "reference"
    subprocess.run(["/bin/bash", "-c", _FOREIGN[state].format(p=reference, elsewhere=elsewhere)])
    assert _snapshot(planted) == _snapshot(reference)
    assert not elsewhere.exists() or not list(elsewhere.iterdir())
    # Once it is moved away, a retry publishes the full set.
    if planted.is_dir() and not planted.is_symlink():
        planted.rmdir()
    else:
        planted.unlink()
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert env.published() == sorted(_SET)


def test_publish_to_an_absent_name_publishes_the_full_set(env):
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert env.published() == sorted(_SET)


def test_a_name_holding_this_runs_own_file_is_refused_and_removed(env):
    # Same inode as this run's scratch DMG: refused (it was not this run's link), and since it is
    # this run's bytes, cleanup removes it, so a plain retry works.
    planted = env.out / DMG
    r = env.run(STUB_DURING_DETACH=f"/bin/ln '{env.out}'/.dmg-package.*/'{DMG}' '{planted}'")
    assert r.returncode == 1
    assert f"cannot publish dist/dmg/{DMG}" in r.stderr
    assert env.published() == []
    assert env.run().returncode == 0
    assert env.published() == sorted(_SET)


def test_a_directory_appearing_just_before_ln_is_not_published_into(env):
    # ln links INTO a directory and succeeds: the packager must notice and take the link back.
    r = env.run(STUB_LN="dir-race")
    assert r.returncode == 1
    assert f"cannot publish dist/dmg/{DMG}" in r.stderr
    assert env.published() == [DMG]
    assert (env.out / DMG).is_dir() and not list((env.out / DMG).iterdir())


@pytest.mark.parametrize(
    ("at", "kept"),
    [
        pytest.param("1", [], id="after-dmg"),
        pytest.param("2", [], id="after-sha256"),
        pytest.param("3", sorted(_SET), id="after-manifest"),
    ],
)
def test_an_interrupt_while_publishing_leaves_all_or_nothing(env, at, kept):
    r = env.run(STUB_LN=f"term-{at}")
    assert r.returncode != 0
    assert env.published() == kept
    # No scratch is left, and a retry is never blocked by this run.
    if not kept:
        assert env.run().returncode == 0
        assert env.published() == sorted(_SET)


def test_without_hard_links_the_notarized_dmg_is_kept_unpublished(env):
    # Two notarizations must not be lost on a file system that cannot hard-link.
    r = env.run(STUB_LN="nolinks")
    assert r.returncode == 1
    assert "Operation not supported" in r.stderr and "does not support hard links" in r.stderr
    kept = "Pipecat-Service-Toolbar-1.2.3-b42-TESTBUNDLE.unpublished.dmg"
    assert env.published() == [kept]
    digest = hashlib.sha256((env.out / kept).read_bytes()).hexdigest()
    assert f"sha256 {digest}" in r.stderr


def test_a_rehearsal_publish_may_replace_files(env):
    name = "Pipecat-Service-Toolbar-1.2.3-b42-UNNOTARIZED-TESTBUNDLE"
    planted = env.out / f"{name}.dmg.sha256"
    r = env.run(DMG_SKIP_NOTARIZE="1", STUB_DURING_DETACH=f"echo old > '{planted}'")
    assert r.returncode == 0, r.stderr
    assert env.published() == [f"{name}.dmg", f"{name}.dmg.sha256", f"{name}.manifest.json"]
    assert planted.read_text().split()[1] == f"{name}.dmg"


# --- manifest inputs: values are data, never code or paths --------------------------------


@pytest.mark.parametrize(
    ("version", "build"),
    [
        pytest.param("3.0", 5, id="two-part"),
        pytest.param("0.4.1", 474, id="current"),
        pytest.param("1.2.3-beta.1", 7, id="prerelease"),
        pytest.param("2.0.0+meta.5", 1, id="build-metadata"),
        pytest.param("10", "42", id="string-build"),
    ],
)
def test_plain_versions_reach_name_and_manifest_unchanged(env, version, build):
    env.set_version(version, build)
    r = env.run()
    assert r.returncode == 0, r.stderr
    name = f"Pipecat-Service-Toolbar-{version}-b{build}-TESTBUNDLE"
    assert env.published() == [f"{name}.dmg", f"{name}.dmg.sha256", f"{name}.manifest.json"]
    manifest = json.loads((env.out / f"{name}.manifest.json").read_text())
    assert manifest["version"] == version and manifest["build"] == int(build)


@pytest.mark.parametrize(
    ("version", "build"),
    [
        pytest.param('1.2"3', 42, id="quote"),
        pytest.param('1"+open("PWNED","w").name+"', 42, id="python-injection"),
        pytest.param("../../escape", 42, id="dot-dot"),
        pytest.param("1.2/3", 42, id="slash"),
        pytest.param("1.2 3", 42, id="space"),
        pytest.param("", 42, id="empty"),
        pytest.param("v1.2", 42, id="prefix"),
        pytest.param("1.2.3", "4a", id="build-letters"),
        pytest.param("1.2.3", -1, id="build-negative"),
        pytest.param("1.2.3", 1.5, id="build-float"),
        pytest.param("1.2.3", '42")+open("PWNED","w").name+("', id="build-injection"),
    ],
)
def test_odd_versions_are_refused_before_any_work(env, version, build):
    env.set_version(version, build)
    r = env.run()
    assert r.returncode == 1
    assert "app-version.json" in r.stderr
    assert not [
        c for c in env.calls() if c.startswith(("hdiutil", "ditto", "xcrun notarytool submit"))
    ]
    assert env.published() == []
    assert not list(env.tmp.rglob("PWNED")) and not list(env.tmp.rglob("*.dmg"))


def test_a_bundle_path_with_quotes_is_recorded_as_data(env, tmp_path):
    odd = tmp_path / 'a") and open("PWNED","w") and ("$(touch PWNED2)\\' / APP
    shutil.copytree(env.bundle, odd)
    env.bundle = odd
    r = env.run()
    assert r.returncode == 0, r.stderr
    manifest = json.loads((env.out / f"{PLAIN}-TESTBUNDLE.manifest.json").read_text())
    assert manifest["test_bundle"] is True
    assert not list(tmp_path.rglob("PWNED*"))
