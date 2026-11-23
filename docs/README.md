# Documentation

Start with the [project README](../README.md) to build the app and connect Pipecat.

| Guide | Use it to… |
|---|---|
| [Using the toolbar](usage.md) | Manage models, test speech, work with external servers, find caches and logs |
| [Services and models](services.md) | Choose a model and find its port, licence and runtime requirements |
| [Development](development.md) | Build and sign the app, run tests, update constraints and verify fixtures |
| [Architecture](architecture.md) | Understand lifecycle state, supervision, protocols and packaging |
| [Tester install (interim)](tester-install.md) | Install a self-signed build, for known testers who were sent a command by the owner |
| [Migration and compatibility](migration.md) | Preserve an existing installation or move a server pin |
| [Loopback authentication](loopback-auth.md) | Understand local access and the decision not to require tokens |

## Reference and history

- [Supervised contract](supervised-contract.json): JSON events and error codes shared with the servers.
- [Test ownership](test-ownership.json): coverage retained when the toolbar moved into this repository.
- [Release history](../CHANGELOG.md): app releases, independent of server package versions.
- [Development plans](plans/): implementation decisions and verification records. Older plans retain
  their original paths and release numbers, with repository-migration notes.
- [App distribution plan](plans/20261117-feature-app-distribution.md): standalone installation and manual updates first as a GitHub alpha; Homebrew follows later using a stable release's notarized archive.
- [Self-signed tester channel plan](plans/20261121-feature-selfsigned-tester-channel.md): interim owner-built, self-signed builds for known testers until the Apple-signed alpha is available; Homebrew stays with the Apple-signed plan.
- [App Store decision](plans/20261117-design-app-store-distribution.md): retained decision record explaining why this route is not planned.
