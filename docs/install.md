# Install the app

Install Pipecat Service Toolbar from a DMG someone on the team sent you, or one you downloaded from a
release. You do not need Xcode, Python or a terminal.

## Requirements

- A Mac with Apple silicon (M1 or later)
- macOS 15 or later; Apple Speech needs macOS 26 or later
- Disk space for the models you choose (from a few hundred MB to several GB each)

## Install

1. Open the `Pipecat-Service-Toolbar-<version>.dmg` file.
2. Drag **Pipecat Service Toolbar** onto the **Applications** folder in the window that opens.
3. Eject the disk image (the eject button next to it in Finder's sidebar).
4. Open **Pipecat Service Toolbar** from Applications. macOS asks once whether to open an app downloaded
   from the internet; click **Open**.

The app lives in the menu bar (a cat icon) and has no Dock icon. Continue with
[Using the toolbar](usage.md).

The DMG is signed and notarized by Apple. If macOS says the app "cannot be verified" or "is damaged",
the download is incomplete or was changed: download it again and do not bypass the warning.

## Check the download (optional)

If you were also given the `.dmg.sha256` file, compare it with the download:

```bash
shasum -a 256 ~/Downloads/Pipecat-Service-Toolbar-*.dmg
```

The long hexadecimal value must equal the one in the `.sha256` file.

## Update

Quit the running app first (menu bar icon → **Quit**), then repeat the install steps and choose
**Replace**. Your models, settings and logs stay where they are.

If you previously ran a self-signed tester build or a build made from source, macOS asks once again
for Microphone and Speech Recognition access, because the app is now signed by a different certificate.

## Uninstall

Quit the app and move it from Applications to the Bin. Models and settings stay on disk; see
[Using the toolbar](usage.md) for where they are and how to remove them.
