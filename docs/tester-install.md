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

On success it prints a line starting `installed tester-` with the tag, version and build it installed. If you do
not see that line, nothing was installed, even when Terminal shows no error: `curl ... | bash` reports success
when the download itself fails. Then open **Pipecat Service Toolbar** from `~/Applications`. macOS asks for
Microphone (and, where a feature uses it, Speech Recognition) the first time you use them: allow them.

The installer:

- stops with a message if the app is running; quit it and run the command again;
- stops if another install is running in the same folder. An install that was interrupted can leave its lock,
  the folder `~/Applications/.tester-install.lock`; the message names it, and once no install is running you
  remove that folder and run the command again;
- puts the app at `~/Applications/Pipecat Service Toolbar.app` and keeps your previous copy (at
  `~/Applications/.tester-previous` while it swaps) until the new one verifies, restoring it if anything fails.
  If an earlier install was interrupted and the app in place fails the certificate check, it changes nothing and
  says how to go back to the previous copy;
- touches nothing else: not your app data, caches, models or preferences, and it never uses `sudo`.

## Update

Run the command from the owner's newest message. Your Microphone and Speech permissions carry over, because every
tester build is signed by the same certificate.

## Uninstall

1. In the app, turn off **Open at Login**, then choose **Quit**.
2. Remove `~/Applications/Pipecat Service Toolbar.app`.
3. Remove what an interrupted install can leave beside it, if present: `~/Applications/.tester-previous` and any
   `~/Applications/.tester-install.*` folders (staging folders and the lock). They are hidden in Finder; in
   Terminal, `ls -a ~/Applications` shows them.

Your data stays: `~/Library/Application Support/Pipecat Service Toolbar`, the caches and the logs. Remove those
yourself if you want them gone.

## Feedback

Open a GitHub issue in this repository. Put the tag the installer printed in the title, and say the version and
build it showed. If two drops share a version and build, the tag is how we tell them apart.

## If the installer refuses

Stop and tell the owner what it printed. Do not download the ZIP in a browser and do not use Privacy & Security >
**Open Anyway**: a browser download is flagged by Gatekeeper, and Open Anyway skips the certificate check that
this channel relies on, so neither is supported.

## Moving to the Apple-signed release

The Apple-signed build uses a different signing identity, so macOS asks for Microphone and Speech permission
again once. The Apple installer cannot remove a tester copy, which has no install receipt, so first remove the
app by hand: turn off **Open at Login**, quit the app, remove `~/Applications/Pipecat Service Toolbar.app`, then
follow the install steps in the [README](../README.md) when they are published. Your data stays.

Plan: [self-signed tester channel](plans/20261121-feature-selfsigned-tester-channel.md).
