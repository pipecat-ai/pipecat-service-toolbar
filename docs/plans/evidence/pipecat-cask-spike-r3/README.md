# Homebrew lifecycle spike, revision 3

Run this in a disposable macOS guest or dedicated test Homebrew prefix. The runner
requires zero installed formulae and casks before it writes any Brew state.

```sh
/usr/bin/ruby run-spike.rb --check
```

The check reports the Brew version, prefix and package counts. Once it passes in
the disposable environment, take a guest snapshot and run:

```sh
/usr/bin/ruby run-spike.rb --run
```

The runner sets `HOMEBREW_NO_AUTO_UPDATE=1`, `HOMEBREW_NO_AUTOREMOVE=1` and
`HOMEBREW_NO_INSTALL_CLEANUP=1` for every Brew command. It creates a local
`toolbar-spike/retry` tap, uses individual trust for the fixture, and installs a
dummy app into a private temporary directory. It embeds that directory in the
fixture so Homebrew can reload the installed definition without custom shell
variables. These fixtures use legacy hooks to diagnose the prior run. A pass on
this version would leave supported hook design and real quit acceptance open.

The runner checks installation, upgrade refusal, direct uninstall refusal,
rollback after an injected postflight failure, a normal upgrade and normal
uninstall. After the refused uninstall, it restores trust for the individual
fixture before querying the installed version. It stops at the first unexpected result and prints the evidence
directory. The logs capture the Brew output, cask source path and hash, refusal
marker state, lock holder PID and descriptor, Caskroom metadata hashes, and the
result from an independent lock contender at each hook. Matching lock evidence
at the hooks still needs to be assessed against Brew's full lifecycle ordering.
Keep the evidence directory until the results are reviewed.
