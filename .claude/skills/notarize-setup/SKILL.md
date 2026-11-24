---
name: notarize-setup
description: Walk a maintainer through setting up their Mac to build the notarized DMG (just app-dmg) - import the Developer ID certificate, create .env, store the notarization credentials, verify with the doctor, and clean up. Use when someone asks to set up signing or notarization, to build a notarized app or DMG for the first time, or when `just app-dmg-doctor` fails.
---

# Set up notarized DMG builds on this Mac

Goal: this Mac can run `just app-dmg` (`scripts/package_dmg.sh`) and get a Developer ID signed,
notarized and stapled DMG. The human guide is `docs/development.md`, sections "Release signing" and
"Notarized DMG"; this skill does the same steps interactively, checking each one.

## Rules (this repository is public)

- Never write a team-specific value (Team ID, Key ID, Issuer ID, certificate hash, vault or item name,
  submission ID) into a tracked file, a commit, a PR or an issue. They belong only in the git-ignored
  `.env`, `.private/` and `.local_certificates/`. Before any commit, scan the staged diff for them.
- Never read, print or copy the contents of a `.p12`, `.p8` or password. Only hashes of public material
  (a certificate's or key's modulus) may be shown.
- Anything that needs a secret typed (the `.p12` password) is run by the user, not by you. Ask them to
  run it with the `!` prefix, or in their own terminal if the prompt does not appear.
- Never revoke or delete a certificate or API key in Apple's portals, and never remove keychain items
  you did not just add.

## 0. Gather context

- If `.private/signing-setup.md` exists, read it: it names the password-manager vaults and items and
  holds the team's values. If it does not, ask the user for the vault/item names and to get the private
  note from another maintainer. Do not guess values.
- Check the basics: Apple silicon (`uname -m` is `arm64`), Xcode (`xcodebuild -version`), `uv`,
  and `just` (if missing: `brew install just`; `scripts/package_dmg.sh` works without it).
- Run `scripts/package_dmg.sh --doctor` to see which of the steps below are already done, and skip those.

## 1. Developer ID identity

Done when `security find-identity -v -p codesigning` lists one
`"Developer ID Application: <Team> (<TEAMID>)"` for the team.

1. Ask the user to download the `.p12` (certificate and private key, one file) and copy its password
   from the password manager, saving the file into `.local_certificates/` (git-ignored; check with
   `git check-ignore -q .local_certificates/x`). With the 1Password CLI (`op`) installed and signed in,
   `op document get` can fetch it instead.
2. Have the user import it (prompts for the password):
   `! security import .local_certificates/<file>.p12 -k ~/Library/Keychains/login.keychain-db -T /usr/bin/codesign`
3. Re-check `find-identity`. If the identity is still missing:
   - The `.p12` may hold only a key or a different certificate. Have the user compare moduli:
     `openssl pkcs12 -in <p12> -nocerts -nodes | openssl rsa -noout -modulus | shasum` versus the
     certificate's (`openssl x509 -noout -modulus` on the `.cer`). Different hashes: wrong key; the user
     needs the `.p12` exported together with the certificate from the Mac that made the CSR.
   - It may be listed but invalid (`security find-identity -p codesigning` without `-v` shows it with a
     `CSSMERR_*` status): install Apple's Developer ID G2 intermediate from
     https://www.apple.com/certificateauthority/.
4. Prove the key signs: copy `/usr/bin/true` to the scratchpad, then
   `codesign --force --options runtime --timestamp --sign <hash> <copy>` and
   `codesign --verify --strict <copy>`. If macOS asks for key access, the user clicks **Always Allow**.

## 2. `.env`

Done when `.env` exists, is git-ignored, and has a valid `APPLE_TEAM_ID`.

1. If `.env` is missing: `cp .env.example .env && chmod 600 .env`.
2. Fill `APPLE_TEAM_ID` (10 characters, A-Z 0-9) from the private note or the identity's name
   (the value in parentheses). The notary Key ID and Issuer ID are not kept in `.env`; step 3 takes
   them once. Leave `SIGN_IDENTITY` empty unless the keychain holds several Developer ID identities for the team.
3. Confirm with `git check-ignore -q .env`. When reporting, say which keys are set, not their values.

## 3. Notarization credentials

Done when `xcrun notarytool history --keychain-profile pipecat-notary` prints
"Successfully received submission history" (it lists the whole team's submissions; that is expected).

1. Ask the user to download `AuthKey_<KEYID>.p8` into `.local_certificates/` and to have the Issuer ID
   ready (both from the password manager; the Key ID is in the file name).
2. Run, or have the user run:
   `xcrun notarytool store-credentials pipecat-notary --key .local_certificates/AuthKey_<KEYID>.p8 --key-id <KEYID> --issuer <ISSUER_ID>`
   Expect "Credentials validated" and "Credentials saved to Keychain". An authentication error means a
   wrong Issuer ID, or the key was revoked in App Store Connect.
3. If `.env` sets a different `NOTARY_PROFILE`, use that name instead of `pipecat-notary`.

## 4. Clean up

Only after the user confirms the `.p12`, its password and the `.p8` are safely in the password manager:
delete the downloaded `.p12` and `.p8` from `.local_certificates/`. The keychain now holds both.

## 5. Verify

1. `scripts/package_dmg.sh --doctor` must end with "Ready: just app-dmg". Mention if the certificate has
   fewer than 30 days left (only the Account Holder can issue a new one).
2. Offer a real run. It needs a clean tree (`git status --porcelain` empty) and takes several minutes
   (Apple notarizes twice). Run it in the background: `scripts/package_dmg.sh`. Success prints the DMG
   path and its SHA-256 under `dist/dmg/`; a rejection saves Apple's log next to it — read its `issues`.
3. For an independent check, copy the DMG, mark the copy as downloaded
   (`xattr -w com.apple.quarantine "0081;$(printf %x $(date +%s));Safari;" <copy>`), then
   `spctl -a -vvv -t open --context context:primary-signature <copy>` must say
   `source=Notarized Developer ID`.

## 6. Report

Summarize what was done and what was already in place, as a short checklist (identity, `.env`, notary
profile, cleanup, doctor, optional real run), with no team-specific values. If a verification log is
kept in `.private/signing-setup.md`, offer to append the date and outcome there.
