# Tester install (interim)

This is an interim way to try Pipecat Service Toolbar before the Apple-signed release exists. It is for
**known testers**: people who have been sent an install command directly by the owner. If you were not sent one,
please wait for the Apple-signed release.

## What you are agreeing to

These builds are **not notarized** and are signed with the owner's self-signed certificate, not an Apple
Developer ID. macOS Gatekeeper would normally reject such an app. The installer below fetches the app with
`curl`, which does not mark it as downloaded from the internet, so macOS does not run its first-launch Gatekeeper
check. That is a deliberate bypass of Gatekeeper. It is acceptable only because you know the person who sent
the command.

Instead, the installer checks the app against a certificate fingerprint that is committed in
[`scripts/install_tester.sh`](../scripts/install_tester.sh), and refuses anything signed by another certificate. The
command you were sent fetches that script at one exact commit. Only run a command that came to you directly from
the owner.

## Requirements

- A Mac with Apple silicon.
- macOS 15 or newer. (Apple Speech needs macOS 26 or newer, as in the [README](../README.md).)

## Install

Paste the command you were sent into Terminal. It has this shape; `<INSTALLER_COMMIT_SHA>` and `<TAG>` are filled
in for you in the message:

```bash
curl -q -fsSL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/pipecat-ai/pipecat-service-toolbar/<INSTALLER_COMMIT_SHA>/scripts/install_tester.sh | bash -s -- <TAG>
```

`-q` comes first so that `curl` ignores any `~/.curlrc` (which could turn off certificate checks), and
`--proto '=https'` keeps the download on HTTPS.

On success it prints a line starting `installed tester-` with the tag, version and build it installed; only that
line means the install succeeded. If you do not see it, the install did not complete, even when Terminal shows no
error (`curl ... | bash` reports success when the download itself fails), and an app may still be in place: your
previous copy, or, if the installer said so, an unverified build you must not open (see
[If the installer refuses](#if-the-installer-refuses)). After the success line, open **Pipecat Service Toolbar**
from `~/Applications`. macOS asks for
Microphone (and, where a feature uses it, Speech Recognition) the first time you use them: allow them.

The installer:

- stops with a message if the app is running; quit it and run the command again;
- stops if another install is running in the same folder. An install that was interrupted can leave its lock,
  the folder `~/Applications/.tester-install.lock`; the message names it, and once no install is running you
  remove that folder and run the command again;
- puts the app at `~/Applications/Pipecat Service Toolbar.app` and, on an update, moves the build it
  replaces to `~/Applications/.tester-previous` (hidden). That copy stays after a successful update as a
  rollback copy. If the swap fails, the installer tries to restore it. If the app in place fails
  verification and a backup exists, the installer prints how to restore it by hand;
- keeps one rollback copy: a later successful update replaces `.tester-previous` with the build it
  replaced and removes the older one. If it cannot put a copy back, the installer keeps it in a
  `~/Applications/.tester-leftover.*` folder and prints the path. It also lists any staging or leftover
  folders from earlier runs;
- finishes the swap even if you press Control-C or close the window during it, and at any other point stops
  cleanly, cleaning up only once the step it was on has ended;
- touches nothing else: not your app data, caches, models or preferences, and it never uses `sudo`.

## Update

Run the command from the owner's newest message. Your Microphone and Speech permissions carry over, because every
tester build is signed by the same certificate.

After a successful update the build it replaced stays at `~/Applications/.tester-previous` as a rollback copy
(it takes disk space as large as the app). To go back to it, quit the app, move
`~/Applications/Pipecat Service Toolbar.app` to the Trash and rename `.tester-previous` to
`Pipecat Service Toolbar.app`. The next successful update replaces the rollback copy.

## Uninstall

1. In the app, turn off **Open at Login**, then choose **Quit**.
2. Remove `~/Applications/Pipecat Service Toolbar.app`.
3. Remove the rollback copy and any folders beside it that an install left, if present:
   `~/Applications/.tester-previous` (the build the last update replaced), any
   `~/Applications/.tester-install.*` folders (staging folders and the lock) and any
   `~/Applications/.tester-leftover.*` folders (app copies an install kept). They are hidden in Finder; in
   Terminal, `ls -a ~/Applications` shows them.

Your data stays: `~/Library/Application Support/Pipecat Service Toolbar`, the caches and the logs. Remove those
yourself if you want them gone.

## Feedback

Open a GitHub issue in this repository. Put the tag the installer printed in the title, and say the version and
build it showed. If two drops share a version and build, the tag is how we tell them apart.

## If the installer refuses

Unless a message below says otherwise, stop and tell the owner what it printed. Do not download the ZIP in a
browser and do not use Privacy & Security > **Open Anyway**: a browser download is flagged by Gatekeeper, and
Open Anyway skips the certificate check that this channel relies on, so neither is supported. Some messages need
one more step:

- `... not signed by the pinned tester certificate`: the download is not intact and signed with the owner's
  certificate. Stop: do not run the command again or try another way to install it, and tell the owner what
  it printed. The installer refuses before it replaces anything.
- `the download is corrupt (SHA-256 mismatch)`: the download was damaged on the way. Run the command once more;
  if it says this again, stop and tell the owner.
- `could not tell whether the app is running`: quit Pipecat Service Toolbar by hand (its menu bar icon, then
  **Quit**, or **Stop All & Quit** while servers are running), then run the command again.
- `... is an unverified build, do not open it`: the new copy failed the certificate check after it was put in
  place and could not be moved away. Do not open the app until the owner replies; the message also says where
  your previous copy is, if there was one.
- `another tester install is running ..., or one was interrupted`: if no other install is running (no other
  Terminal window is installing), remove the folder it names, `~/Applications/.tester-install.lock`, and run the
  command again. The message also lists any staging or kept-copy folders beside the app.
- `another program changed ... during the install`: another program created, moved or replaced the app
  during the swap. Check the paths in the message for the installed app and its backup.
- A `note:` naming a `.tester-install.*` or `.tester-leftover.*` folder: an earlier install left it. Once the
  app works, you can remove it as in [Uninstall](#uninstall), step 3; tell the owner if one says a copy failed
  the certificate check.

## Moving to the Apple-signed release

The Apple-signed build uses a different signing identity, so macOS asks for Microphone and Speech permission
again once. The Apple installer cannot remove a tester copy, which has no install receipt, so first remove the
app by hand: turn off **Open at Login**, quit the app, remove `~/Applications/Pipecat Service Toolbar.app`, then
follow the install steps in the [README](../README.md) when they are published. Your data stays.

Plan: [self-signed tester channel](plans/20261121-feature-selfsigned-tester-channel.md).
