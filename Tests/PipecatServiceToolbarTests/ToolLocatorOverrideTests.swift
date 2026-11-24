import Foundation
import Testing
@testable import PipecatToolbarCore

/// `PIPECAT_TOOLBAR_REPO` may name a TTS source checkout. The hash-pinned STT constraints are this
/// repository's, so that override must not make STT installs fall back to unpinned.
@Suite struct ToolLocatorOverrideTests {
    static func tree() throws -> (toolbar: URL, tts: URL, cleanup: () -> Void) {
        let root = FileManager.default.temporaryDirectory.appending(path: "toolbar-locator-\(UUID())")
        let toolbar = root.appending(path: "toolbar")
        let tts = root.appending(path: "tts-checkout")
        let fm = FileManager.default
        try fm.createDirectory(at: toolbar.appending(path: "server-pins/tts"), withIntermediateDirectories: true)
        try fm.createDirectory(at: toolbar.appending(path: "scripts"), withIntermediateDirectories: true)
        try fm.createDirectory(at: tts, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: toolbar.appending(path: "server-pins/tts/wheel.json"))
        try Data("[project]\nname = \"pipecat-local-tts-server\"\n".utf8).write(to: tts.appending(path: "pyproject.toml"))
        return (toolbar, tts, { try? fm.removeItem(at: root) })
    }

    @Test func aTTSCheckoutOverrideStillUsesTheToolbarsSTTConstraints() throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let found = ToolLocator.toolbarRoot(from: [toolbar.appending(path: "scripts")])
        #expect(found?.standardizedFileURL == toolbar.standardizedFileURL)
        let chosen = ToolLocator.defaultSTTConstraints(repo: tts, toolbarRoot: found)
        #expect(chosen?.standardizedFileURL == toolbar.appending(path: "server-pins/stt/constraints").standardizedFileURL)
    }

    @Test func aToolbarCheckoutOverrideBringsItsOwnSTTConstraints() throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let other = toolbar.deletingLastPathComponent().appending(path: "other-toolbar")
        try FileManager.default.createDirectory(at: other.appending(path: "server-pins/tts"), withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: other.appending(path: "server-pins/tts/wheel.json"))
        // `repo` is a toolbar checkout that is not the one the executable runs from.
        let chosen = ToolLocator.defaultSTTConstraints(repo: other, toolbarRoot: toolbar)
        #expect(chosen?.standardizedFileURL == other.appending(path: "server-pins/stt/constraints").standardizedFileURL)
        // A TTS source checkout carries none of the pins, so the executable's toolbar root still wins.
        let fromTTS = ToolLocator.defaultSTTConstraints(repo: tts, toolbarRoot: toolbar)
        #expect(fromTTS?.standardizedFileURL == toolbar.appending(path: "server-pins/stt/constraints").standardizedFileURL)
    }

    @Test func aLegacyToolbarCheckoutOverrideUsesItsOwnToolsAndSTTConstraints() async throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let root = tts.deletingLastPathComponent()
        let legacy = root.appending(path: "legacy-toolbar")
        let pins = legacy.appending(path: "constraints")
        let fm = FileManager.default
        try fm.createDirectory(at: legacy.appending(path: "artifacts"), withIntermediateDirectories: true)
        try fm.createDirectory(at: pins, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: legacy.appending(path: "artifacts/tts.json"))
        let pin = pins.appending(path: "stt-whisper.txt")
        try Data("legacy whisper pins".utf8).write(to: pin)

        let uv = root.appending(path: "uv")
        let wheel = "pipecat_local_tts_server-0.9.1-py3-none-any.whl"
        // Only the legacy checkout has this marker. Accept its prepare_tools invocation,
        // and fail if ToolLocator sends an upstream wheel build or changes the working directory.
        let script = """
            #!/bin/sh
            set -eu
            test -f artifacts/tts.json
            test "$5" = scripts/prepare_tools.py
            printf '%s\\n' '\(wheel)' > "$7/MANIFEST"
            """
        try Data(script.utf8).write(to: uv)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: uv.path)
        let paths = AppPaths(
            supportDir: root.appending(path: "support"), cacheDir: root.appending(path: "cache"),
            logsDir: root.appending(path: "logs"), hubDir: root.appending(path: "hub"),
            derivedModelsDir: root.appending(path: "derived"))
        let noTools = try #require(Bundle(path: root.path))
        let selected = try #require(ToolLocator.explicitRepositoryRoot(legacy.path))
        #expect(ToolLocator.defaultSTTConstraints(repo: selected, toolbarRoot: toolbar)?.path == pins.path)
        let tools = try await ToolLocator.locate(
            bundle: noTools, environment: ["PIPECAT_TOOLBAR_UV": uv.path, "PIPECAT_TOOLBAR_REPO": legacy.path],
            paths: paths)
        #expect(tools.wheelName == wheel)
        #expect(try tools.constraints(for: Service.whisper.info)?.path == pin.path)

        let oldDirectory = toolbar.appending(path: "constraints")
        let currentDirectory = toolbar.appending(path: "server-pins/stt/constraints")
        try fm.createDirectory(at: oldDirectory, withIntermediateDirectories: true)
        try fm.createDirectory(at: currentDirectory, withIntermediateDirectories: true)
        try Data().write(to: oldDirectory.appending(path: ".DS_Store"))
        let currentPin = currentDirectory.appending(path: "stt-whisper.txt")
        try Data().write(to: currentPin)
        try Data().write(to: toolbar.appending(path: "server-pins/tts/constraints.txt"))
        for wheelPath: String? in [nil, root.appending(path: "missing.whl").path] {
            var environment = [
                "PIPECAT_TOOLBAR_UV": uv.path, "PIPECAT_TOOLBAR_REPO": legacy.path,
                "PIPECAT_TOOLBAR_CONSTRAINTS": oldDirectory.appending(path: "tts.txt").path,
                "PIPECAT_TOOLBAR_STT_CONSTRAINTS": oldDirectory.path,
            ]
            environment["PIPECAT_TOOLBAR_WHEEL"] = wheelPath
            let built = try await ToolLocator.locate(bundle: noTools, environment: environment, paths: paths)
            #expect(built.source == .repository)
            #expect(built.constraints?.path == paths.cacheDir.appending(path: "dev-wheel/constraints.txt").path)
            #expect(try built.constraints(for: Service.whisper.info)?.path == currentPin.path)
        }
    }

    @Test func checkoutLocalPinOverridesFollowTheMoveOnlyWhenMissing() async throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let root = toolbar.deletingLastPathComponent()
        let fm = FileManager.default
        let wheel = root.appending(path: "custom.whl")
        try Data().write(to: wheel)
        let uv = root.appending(path: "uv")
        let uvArgs = root.appending(path: "uv-args")
        let script = """
            #!/bin/sh
            printf '%s\\n' "$(/bin/pwd -P)" "$@" > '\(uvArgs.path)'
            """
        try Data(script.utf8).write(to: uv)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: uv.path)
        let newTTSPin = toolbar.appending(path: "server-pins/tts/constraints.txt")
        let newSTTPins = toolbar.appending(path: "server-pins/stt/constraints")
        let newSTTPin = newSTTPins.appending(path: "stt-whisper.txt")

        let oldDirectory = toolbar.appending(path: "constraints")
        let oldTTSPin = oldDirectory.appending(path: "tts.txt")
        let oldSTTPin = oldDirectory.appending(path: "stt-whisper.txt")
        let paths = AppPaths(
            supportDir: root.appending(path: "support"), cacheDir: root.appending(path: "cache"),
            logsDir: root.appending(path: "logs"), hubDir: root.appending(path: "hub"),
            derivedModelsDir: root.appending(path: "derived"))
        let noTools = try #require(Bundle(path: root.path))
        func locate(ttsPin: URL, sttDirectory: URL, bundle: Bundle? = nil) async throws -> Tools {
            try await ToolLocator.locate(bundle: bundle ?? noTools, environment: [
                "PIPECAT_TOOLBAR_UV": uv.path,
                "PIPECAT_TOOLBAR_REPO": tts.path,
                "PIPECAT_TOOLBAR_WHEEL": wheel.path,
                "PIPECAT_TOOLBAR_CONSTRAINTS": ttsPin.path,
                "PIPECAT_TOOLBAR_STT_CONSTRAINTS": sttDirectory.path,
            ], paths: paths)
        }

        let missingTargets = try await locate(ttsPin: oldTTSPin, sttDirectory: oldDirectory)
        #expect(missingTargets.constraints?.path == oldTTSPin.path)
        #expect(missingTargets.sttConstraints?.path == oldDirectory.path)

        try fm.createDirectory(at: newSTTPins, withIntermediateDirectories: true)
        try Data().write(to: newTTSPin)
        try Data().write(to: newSTTPin)
        let migrated = try await locate(ttsPin: oldTTSPin, sttDirectory: oldDirectory)
        #expect(try migrated.constraints(for: Service.tone.info)?.path == newTTSPin.path)
        #expect(try migrated.constraints(for: Service.whisper.info)?.path == newSTTPin.path)

        let mixed = try await locate(
            ttsPin: toolbar.appending(path: "Constraints/TTS.TXT"),
            sttDirectory: toolbar.appending(path: "Constraints"))
        let caseCapability = try toolbar.resourceValues(
            forKeys: [.volumeSupportsCaseSensitiveNamesKey]).volumeSupportsCaseSensitiveNames
        let caseSensitive = try #require(caseCapability as Bool?)
        #expect(mixed.constraints?.path == (caseSensitive ? toolbar.appending(path: "Constraints/TTS.TXT") : newTTSPin).path)
        #expect(try mixed.constraints(for: Service.whisper.info)?.path == (caseSensitive ? nil : newSTTPin.path))

        let custom = toolbar.appending(path: "my-pins")
        let customTTSPin = custom.appending(path: "tts.txt")
        let missingCustom = try await locate(ttsPin: customTTSPin, sttDirectory: custom)
        #expect(missingCustom.constraints?.path == customTTSPin.path)
        #expect(missingCustom.sttConstraints?.path == custom.path)

        let externalDirectory = tts.appending(path: "constraints")
        let externalTTSPin = externalDirectory.appending(path: "tts.txt")
        try fm.createDirectory(at: tts.appending(path: "server-pins/tts"), withIntermediateDirectories: true)
        try fm.createDirectory(at: tts.appending(path: "server-pins/stt/constraints"), withIntermediateDirectories: true)
        try Data().write(to: tts.appending(path: "server-pins/tts/constraints.txt"))
        let external = try await locate(ttsPin: externalTTSPin, sttDirectory: externalDirectory)
        #expect(external.constraints?.path == externalTTSPin.path)
        #expect(external.sttConstraints?.path == externalDirectory.path)

        // The symlink changes what .. refers to. Both override paths must follow it before choosing a checkout.
        let child = tts.appending(path: "child")
        let link = toolbar.appending(path: "linked-child")
        try fm.createDirectory(at: child, withIntermediateDirectories: false)
        try fm.createSymbolicLink(at: link, withDestinationURL: child)
        let traversedDirectory = toolbar.appending(path: "linked-child/../constraints")
        let traversedTTSPin = traversedDirectory.appending(path: "tts.txt")
        let outside = try await locate(ttsPin: traversedTTSPin, sttDirectory: traversedDirectory)
        #expect(outside.constraints?.path == traversedTTSPin.path)
        #expect(try outside.constraints(for: Service.whisper.info) == nil)
        try fm.createDirectory(at: oldDirectory, withIntermediateDirectories: true)
        try Data().write(to: oldTTSPin)
        let missingConstraints = EnvironmentManager(paths: paths, tools: outside)
        try await VariantTests.fakeEnvironment(.tone, missingConstraints, paths)
        await #expect(throws: InstallError.self) {
            try await missingConstraints.install(.tone, mode: .packages).consume { _ in }
        }
        #expect(!fm.fileExists(atPath: uvArgs.path))
        try fm.removeItem(at: oldDirectory)

        try Data("{}".utf8).write(to: tts.appending(path: "server-pins/tts/wheel.json"))
        try Data().write(to: tts.appending(path: "server-pins/stt/constraints/stt-whisper.txt"))
        let traversed = try await locate(ttsPin: traversedTTSPin, sttDirectory: traversedDirectory)
        #expect(try traversed.constraints(for: Service.tone.info)?.resolvingSymlinksInPath().path
            == tts.appending(path: "server-pins/tts/constraints.txt").resolvingSymlinksInPath().path)
        #expect(try traversed.constraints(for: Service.whisper.info)?.resolvingSymlinksInPath().path
            == tts.appending(path: "server-pins/stt/constraints/stt-whisper.txt").resolvingSymlinksInPath().path)
        let manager = EnvironmentManager(paths: paths, tools: traversed)
        for (service, pin): (Service, URL) in [
            (.tone, tts.appending(path: "server-pins/tts/constraints.txt")),
            (.whisper, tts.appending(path: "server-pins/stt/constraints/stt-whisper.txt")),
        ] {
            try await VariantTests.fakeEnvironment(service, manager, paths)
            try await manager.install(service, mode: .packages).consume { _ in }
            let args = try String(contentsOf: uvArgs, encoding: .utf8).split(separator: "\n").map(String.init)
            let workingDirectory = try #require(args.first)
            #expect(URL(filePath: workingDirectory).resolvingSymlinksInPath().path
                == pin.deletingLastPathComponent().resolvingSymlinksInPath().path)
            let flag = try #require(args.firstIndex(of: "--constraint"))
            #expect(args.dropFirst(flag + 1).first == pin.lastPathComponent)
            #expect(args.contains("--require-hashes") == (service == .whisper))
        }

        // Construct relative overrides without changing the process-wide working directory.
        let cwdDepth = fm.currentDirectoryPath.split(separator: "/").count
        let relativeDirectory = String(repeating: "../", count: cwdDepth) + traversedDirectory.path.dropFirst()
        let relative = try await ToolLocator.locate(bundle: noTools, environment: [
            "PIPECAT_TOOLBAR_UV": "/usr/bin/true",
            "PIPECAT_TOOLBAR_WHEEL": wheel.path,
            "PIPECAT_TOOLBAR_CONSTRAINTS": relativeDirectory + "/tts.txt",
            "PIPECAT_TOOLBAR_STT_CONSTRAINTS": relativeDirectory,
        ], paths: paths)
        #expect(try relative.constraints(for: Service.tone.info)?.resolvingSymlinksInPath().path
            == tts.appending(path: "server-pins/tts/constraints.txt").resolvingSymlinksInPath().path)
        #expect(try relative.constraints(for: Service.whisper.info)?.resolvingSymlinksInPath().path
            == tts.appending(path: "server-pins/stt/constraints/stt-whisper.txt").resolvingSymlinksInPath().path)

        try fm.createDirectory(at: oldDirectory, withIntermediateDirectories: true)
        try Data().write(to: oldDirectory.appending(path: ".DS_Store"))
        let leftover = try await locate(ttsPin: oldTTSPin, sttDirectory: oldDirectory)
        #expect(try leftover.constraints(for: Service.tone.info)?.path == newTTSPin.path)
        #expect(try leftover.constraints(for: Service.whisper.info)?.path == newSTTPin.path)

        try Data().write(to: oldTTSPin)
        try Data().write(to: oldSTTPin)
        try Data().write(to: oldDirectory.appending(path: "stt-kyutai.txt"))
        try Data().write(to: newSTTPins.appending(path: "stt-parakeet.txt"))
        let existing = try await locate(ttsPin: oldTTSPin, sttDirectory: oldDirectory)
        #expect(try existing.constraints(for: Service.tone.info)?.path == oldTTSPin.path)
        for (service, expected): (Service, URL?) in [
            (.whisper, oldSTTPin),
            (.kyutai, oldDirectory.appending(path: "stt-kyutai.txt")),
            (.parakeet, newSTTPins.appending(path: "stt-parakeet.txt")),
            (.nemotron, nil),
        ] {
            #expect(try existing.constraints(for: service.info)?.path == expected?.path, "\(service)")
        }

        let customDirectory = toolbar.appending(path: "custom-constraints")
        try fm.createDirectory(at: customDirectory, withIntermediateDirectories: true)
        let customPin = customDirectory.appending(path: "stt-kyutai.txt")
        try Data().write(to: customPin)
        try fm.moveItem(at: oldDirectory, to: root.appending(path: "former-constraints"))
        try fm.createSymbolicLink(at: oldDirectory, withDestinationURL: customDirectory)
        let symlinked = try await locate(ttsPin: oldTTSPin, sttDirectory: oldDirectory)
        #expect(symlinked.constraints?.path == oldTTSPin.path)
        #expect(try symlinked.constraints(for: Service.kyutai.info)?.path == oldDirectory.appending(path: "stt-kyutai.txt").path)
        #expect(try symlinked.constraints(for: Service.whisper.info) == nil)

        let bundleContents = root.appending(path: "WithTools.app/Contents")
        let resources = bundleContents.appending(path: "Resources")
        let bundleWheels = resources.appending(path: "wheels")
        let bundleUV = resources.appending(path: "bin/uv")
        try fm.createDirectory(at: bundleUV.deletingLastPathComponent(), withIntermediateDirectories: true)
        try fm.createDirectory(at: bundleWheels, withIntermediateDirectories: true)
        try fm.createSymbolicLink(at: bundleUV, withDestinationURL: URL(filePath: "/usr/bin/true"))
        let bundledWheel = "bundled.whl"
        try Data(bundledWheel.utf8).write(to: bundleWheels.appending(path: "MANIFEST"))
        try Data().write(to: bundleWheels.appending(path: bundledWheel))
        let bundledPin = bundleWheels.appending(path: "constraints.txt")
        try Data().write(to: bundledPin)
        let bundle = try #require(Bundle(url: bundleContents.deletingLastPathComponent()))
        let bundled = try await locate(ttsPin: oldTTSPin, sttDirectory: oldDirectory, bundle: bundle)
        #expect(bundled.source == .bundle)
        #expect(bundled.constraints?.path == bundledPin.path)
        #expect(throws: ToolLocatorError.self) { try bundled.constraints(for: Service.whisper.info) }
    }

    @Test(arguments: [true, false, nil] as [Bool?])
    func legacyPinComponentsFollowTheVolumeCasePolicy(caseSensitive: Bool?) {
        for (exact, mixed) in [
            ("constraints", "Constraints"), ("tts.txt", "TTS.TXT"),
            ("constraints", "conſtraints"), ("tts.txt", "ttſ.txt"),
        ] {
            #expect(ToolLocator.matchesLegacyPinName(Substring(exact), exact, caseSensitive: caseSensitive))
            #expect(ToolLocator.matchesLegacyPinName(Substring(mixed), exact, caseSensitive: caseSensitive)
                == (caseSensitive == false))
            #expect(!ToolLocator.matchesLegacyPinName(Substring(exact + ".bak"), exact, caseSensitive: caseSensitive))
        }
        #expect(!ToolLocator.matchesLegacyPinName("con\u{200D}straints", "constraints", caseSensitive: caseSensitive))
    }

    @Test func theToolbarRootIsNotTheTTSCheckout() throws {
        let (_, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        #expect(ToolLocator.toolbarRoot(from: [tts]) == nil)
    }

    @Test func theOverrideResolvesToTheCheckoutItNames() throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        // Compared as paths: `URL(filePath:)` of an existing directory carries a trailing slash.
        #expect(ToolLocator.explicitRepositoryRoot(tts.path)?.standardizedFileURL.path == tts.standardizedFileURL.path)
        #expect(
            ToolLocator.explicitRepositoryRoot(toolbar.appending(path: "scripts").path)?.standardizedFileURL.path
                == toolbar.standardizedFileURL.path)
        #expect(ToolLocator.explicitRepositoryRoot(tts.deletingLastPathComponent().appending(path: "tts-chekout").path) == nil)
    }

    /// A typo in `PIPECAT_TOOLBAR_REPO` must not silently fall back to this checkout's pinned wheel
    /// (found from the test runner's executable) in place of the source the developer named.
    @Test func anOverrideThatNamesNoCheckoutIsReportedNotIgnored() async throws {
        let (_, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let root = tts.deletingLastPathComponent()
        let typo = root.appending(path: "tts-chekout").path
        // A uv that always fails, so a fallback build (the bug) cannot prepare or fetch anything.
        let uv = root.appending(path: "uv")
        try Data("#!/bin/sh\nexit 1\n".utf8).write(to: uv)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: uv.path)
        let paths = AppPaths(
            supportDir: root.appending(path: "support"), cacheDir: root.appending(path: "cache"),
            logsDir: root.appending(path: "logs"), hubDir: root.appending(path: "hub"),
            derivedModelsDir: root.appending(path: "derived"))
        let noTools = try #require(Bundle(path: root.path))  // no bundled uv or wheel
        do {
            _ = try await ToolLocator.locate(
                bundle: noTools, environment: ["PIPECAT_TOOLBAR_UV": uv.path, "PIPECAT_TOOLBAR_REPO": typo],
                paths: paths)
            Issue.record("a PIPECAT_TOOLBAR_REPO naming no checkout must fail")
        } catch ToolLocatorError.repositoryOverrideNotFound(let path) {
            #expect(path == typo)
            #expect(ToolLocatorError.repositoryOverrideNotFound(path).errorDescription?.contains(typo) == true)
        } catch {
            Issue.record("expected repositoryOverrideNotFound, got \(error)")
        }
    }

    /// An empty or whitespace-only `PIPECAT_TOOLBAR_REPO` is unset: it must not become the working
    /// directory (`URL(filePath: "")`) nor an override that throws.
    @Test func anEmptyOrBlankOverrideIsUnset() async throws {
        for value in ["", "   ", "\t\n"] {
            #expect(ToolLocator.repositoryOverride(["PIPECAT_TOOLBAR_REPO": value]) == nil)
        }
        #expect(ToolLocator.repositoryOverride([:]) == nil)
        #expect(ToolLocator.repositoryOverride(["PIPECAT_TOOLBAR_REPO": " /x "]) == "/x")

        let (_, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let root = tts.deletingLastPathComponent()
        let uv = root.appending(path: "uv")
        try Data("#!/bin/sh\nexit 1\n".utf8).write(to: uv)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: uv.path)
        let paths = AppPaths(
            supportDir: root.appending(path: "support"), cacheDir: root.appending(path: "cache"),
            logsDir: root.appending(path: "logs"), hubDir: root.appending(path: "hub"),
            derivedModelsDir: root.appending(path: "derived"))
        let noTools = try #require(Bundle(path: root.path))
        for value in ["", "  "] {
            do {
                _ = try await ToolLocator.locate(
                    bundle: noTools, environment: ["PIPECAT_TOOLBAR_UV": uv.path, "PIPECAT_TOOLBAR_REPO": value],
                    paths: paths)
            } catch ToolLocatorError.repositoryOverrideNotFound {
                Issue.record("a blank PIPECAT_TOOLBAR_REPO must behave as unset")
            } catch {
                // Any other failure (no wheel, failing uv) is the unset path.
            }
        }
    }

    @Test func theRepositoryIsTheFallbackWhenNoToolbarRootIsFound() throws {
        let (_, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let chosen = ToolLocator.defaultSTTConstraints(repo: tts, toolbarRoot: nil)
        #expect(chosen?.standardizedFileURL == tts.appending(path: "constraints").standardizedFileURL)
        #expect(ToolLocator.defaultSTTConstraints(repo: nil, toolbarRoot: nil) == nil)
    }
}
