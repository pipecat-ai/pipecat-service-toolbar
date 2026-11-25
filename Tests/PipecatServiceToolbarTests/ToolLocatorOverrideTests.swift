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

        let migratedPin = legacy.appending(path: "server-pins/stt/constraints/stt-whisper.txt")
        try fm.createDirectory(at: migratedPin.deletingLastPathComponent(), withIntermediateDirectories: true)
        try fm.createDirectory(at: legacy.appending(path: "server-pins/tts"), withIntermediateDirectories: true)
        try Data().write(to: migratedPin)
        try Data().write(to: legacy.appending(path: "server-pins/tts/wheel.json"))
        try fm.removeItem(at: legacy.appending(path: "artifacts/tts.json"))
        try fm.removeItem(at: pin)
        #expect(try tools.constraints(for: Service.whisper.info)?.path == migratedPin.path)
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

        // Only paths in a toolbar checkout participate in the pin migration.
        let child = tts.appending(path: "child")
        let link = toolbar.appending(path: "linked-child")
        try fm.createDirectory(at: child, withIntermediateDirectories: false)
        try fm.createSymbolicLink(at: link, withDestinationURL: child)
        let traversedDirectory = toolbar.appending(path: "linked-child/../constraints")
        let traversedTTSPin = traversedDirectory.appending(path: "tts.txt")
        let outside = try await locate(ttsPin: traversedTTSPin, sttDirectory: traversedDirectory)
        #expect(outside.constraints?.path == newTTSPin.path)
        #expect(try outside.constraints(for: Service.whisper.info) == nil)
        try fm.createDirectory(at: oldDirectory, withIntermediateDirectories: true)
        try Data().write(to: oldTTSPin)
        try Data().write(to: oldSTTPin)
        let cwdDepth = fm.currentDirectoryPath.split(separator: "/").count
        let relativeDirectory = String(repeating: "../", count: cwdDepth) + traversedDirectory.path.dropFirst()
        let relativeOutside = try await ToolLocator.locate(bundle: noTools, environment: [
            "PIPECAT_TOOLBAR_UV": uv.path, "PIPECAT_TOOLBAR_WHEEL": wheel.path,
            "PIPECAT_TOOLBAR_CONSTRAINTS": relativeDirectory + "/tts.txt",
            "PIPECAT_TOOLBAR_STT_CONSTRAINTS": relativeDirectory,
        ], paths: paths)
        #expect(relativeOutside.constraints?.path == URL(filePath: relativeDirectory + "/tts.txt").absoluteURL.path)
        #expect(try relativeOutside.constraints(for: Service.whisper.info)?.path == oldSTTPin.path)
        let originalProcess = EnvironmentManager(paths: paths, tools: outside)
        try await VariantTests.fakeEnvironment(.tone, originalProcess, paths)
        try await originalProcess.install(.tone, mode: .packages).consume { _ in }
        let originalLog = try String(contentsOf: uvArgs, encoding: .utf8)
        let originalWorkingDirectory = try #require(originalLog.split(separator: "\n").first)
        #expect(URL(filePath: String(originalWorkingDirectory)).resolvingSymlinksInPath().path
            == oldDirectory.resolvingSymlinksInPath().path)
        try Data("{}".utf8).write(to: tts.appending(path: "server-pins/tts/wheel.json"))
        let withOriginalPin = try await locate(ttsPin: traversedTTSPin, sttDirectory: traversedDirectory)
        #expect(try withOriginalPin.constraints(for: Service.tone.info)?.path == traversedTTSPin.path)
        try fm.removeItem(at: oldDirectory)

        // STT's original file probe follows the symlink. TTS's Process working directory is lexical.
        try Data().write(to: tts.appending(path: "server-pins/stt/constraints/stt-whisper.txt"))
        let traversed = try await locate(ttsPin: traversedTTSPin, sttDirectory: traversedDirectory)
        #expect(try traversed.constraints(for: Service.tone.info)?.resolvingSymlinksInPath().path
            == newTTSPin.resolvingSymlinksInPath().path)
        #expect(try traversed.constraints(for: Service.whisper.info)?.resolvingSymlinksInPath().path
            == tts.appending(path: "server-pins/stt/constraints/stt-whisper.txt").resolvingSymlinksInPath().path)
        let manager = EnvironmentManager(paths: paths, tools: traversed)
        for (service, pin): (Service, URL) in [
            (.tone, newTTSPin),
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

        // A relative override uses Foundation's original path, including its lexical parent traversal.
        let relative = try await ToolLocator.locate(bundle: noTools, environment: [
            "PIPECAT_TOOLBAR_UV": uv.path,
            "PIPECAT_TOOLBAR_WHEEL": wheel.path,
            "PIPECAT_TOOLBAR_CONSTRAINTS": relativeDirectory + "/tts.txt",
            "PIPECAT_TOOLBAR_STT_CONSTRAINTS": relativeDirectory,
        ], paths: paths)
        #expect(try relative.constraints(for: Service.tone.info)?.resolvingSymlinksInPath().path
            == newTTSPin.resolvingSymlinksInPath().path)
        #expect(try relative.constraints(for: Service.whisper.info)?.resolvingSymlinksInPath().path
            == newSTTPin.resolvingSymlinksInPath().path)

        try fm.createDirectory(at: oldDirectory, withIntermediateDirectories: true)
        try Data().write(to: oldTTSPin)
        try Data().write(to: oldSTTPin)
        let relativeManager = EnvironmentManager(paths: paths, tools: relative)
        for (service, pin): (Service, URL) in [(.tone, oldTTSPin), (.whisper, oldSTTPin)] {
            #expect(try relative.constraints(for: service.info)?.path == pin.path)
            try await VariantTests.fakeEnvironment(service, relativeManager, paths)
            try await relativeManager.install(service, mode: .packages).consume { _ in }
            let args = try String(contentsOf: uvArgs, encoding: .utf8).split(separator: "\n")
            #expect(args.contains("--require-hashes") == (service == .whisper))
            #expect(args.contains(Substring(pin.lastPathComponent)))
            let cwd = try #require(args.first)
            #expect(URL(filePath: String(cwd)).resolvingSymlinksInPath().path
                == oldDirectory.resolvingSymlinksInPath().path)
        }
        try fm.removeItem(at: oldDirectory)

        // The absolute spelling follows the live symlink when a relocated pin is needed.
        let toolbarChild = toolbar.appending(path: "child")
        try fm.createDirectory(at: toolbarChild, withIntermediateDirectories: false)
        try fm.removeItem(at: link)
        try fm.createSymbolicLink(at: link, withDestinationURL: toolbarChild)
        #expect(try traversed.constraints(for: Service.tone.info)?.resolvingSymlinksInPath().path
            == newTTSPin.resolvingSymlinksInPath().path)
        #expect(try traversed.constraints(for: Service.whisper.info)?.resolvingSymlinksInPath().path
            == newSTTPin.resolvingSymlinksInPath().path)

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

    @Test func explicitPinsKeepTheirMeaningAcrossPackageInstalls() async throws {
        let (toolbar, _, cleanup) = try Self.tree()
        defer { cleanup() }
        let root = toolbar.deletingLastPathComponent()
        let fm = FileManager.default
        let wheel = root.appending(path: "custom.whl")
        try Data().write(to: wheel)
        let uv = root.appending(path: "uv")
        let log = root.appending(path: "uv-log")
        try Data("""
            #!/bin/sh
            printf '%s\\n' "$(/bin/pwd -P)" "$@" > '\(log.path)'
            while [ "$#" -gt 0 ]; do
                if [ "$1" = --constraint ]; then
                    test -f "$2"
                    exit $?
                fi
                shift
            done
            """.utf8).write(to: uv)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: uv.path)
        let paths = AppPaths(
            supportDir: root.appending(path: "support"), cacheDir: root.appending(path: "cache"),
            logsDir: root.appending(path: "logs"), hubDir: root.appending(path: "hub"),
            derivedModelsDir: root.appending(path: "derived"))
        let bundle = try #require(Bundle(path: root.path))
        let old = toolbar.appending(path: "constraints")
        let oldTTS = old.appending(path: "tts.txt")
        let oldSTT = old.appending(path: "stt-whisper.txt")
        let manifest = toolbar.appending(path: "server-pins/tts/wheel.json")
        let legacyManifest = toolbar.appending(path: "artifacts/tts.json")
        let newTTS = toolbar.appending(path: "server-pins/tts/constraints.txt")
        let newSTT = toolbar.appending(path: "server-pins/stt/constraints/stt-whisper.txt")
        try fm.createDirectory(at: newSTT.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data().write(to: newTTS)
        try Data().write(to: newSTT)

        func locate(tts: String? = nil, stt: String? = nil, repo: URL? = nil) async throws -> Tools {
            var environment = ["PIPECAT_TOOLBAR_UV": uv.path, "PIPECAT_TOOLBAR_WHEEL": wheel.path]
            environment["PIPECAT_TOOLBAR_CONSTRAINTS"] = tts
            environment["PIPECAT_TOOLBAR_STT_CONSTRAINTS"] = stt
            environment["PIPECAT_TOOLBAR_REPO"] = repo?.path
            return try await ToolLocator.locate(bundle: bundle, environment: environment, paths: paths)
        }

        func checkInstall(_ service: Service, _ tools: Tools, pin: URL?) async throws {
            let manager = EnvironmentManager(paths: paths, tools: tools)
            try await VariantTests.fakeEnvironment(service, manager, paths)
            try? fm.removeItem(at: log)
            do {
                try await manager.install(service, mode: .packages).consume { _ in }
            } catch {
                Issue.record("Package install for \(service) with \(pin?.path ?? "no pin") failed: \(error)")
                return
            }
            let args = try String(contentsOf: log, encoding: .utf8).split(separator: "\n").map(String.init)
            #expect(args.contains("--require-hashes") == (service == .whisper && pin != nil))
            if let pin {
                let actualName = args.firstIndex(of: "--constraint").flatMap { args.dropFirst($0 + 1).first }
                #expect(actualName == pin.lastPathComponent)
                let workingDirectory = try #require(args.first)
                #expect(URL(filePath: workingDirectory).resolvingSymlinksInPath().path
                    == pin.deletingLastPathComponent().resolvingSymlinksInPath().path)
            } else {
                #expect(!args.contains("--constraint"))
            }
        }

        let launchedAfterMove = try await locate(tts: oldTTS.path, stt: old.path)
        let defaultAfterMove = try await locate(repo: toolbar)
        #expect(launchedAfterMove.sttConstraints?.path == old.path)
        try await checkInstall(.whisper, launchedAfterMove, pin: newSTT)
        try await checkInstall(.tone, launchedAfterMove, pin: newTTS)
        try await checkInstall(.whisper, defaultAfterMove, pin: newSTT)
        let throughRemovedDirectory = old.path + "/../constraints"
        let throughMissingDirectory = toolbar.path + "/never-created/../constraints"
        for directory in [throughRemovedDirectory, throughMissingDirectory] {
            let tools = try await locate(tts: directory + "/tts.txt", stt: directory)
            try await checkInstall(.tone, tools, pin: newTTS)
            try await checkInstall(.whisper, tools, pin: directory == throughRemovedDirectory ? newSTT : nil)
        }
        let repeatedLegacyTraversal = try await locate(stt: old.path + "/../constraints/../constraints")
        try await checkInstall(.whisper, repeatedLegacyTraversal, pin: newSTT)
        let child = old.appending(path: "local")
        try fm.createDirectory(at: child, withIntermediateDirectories: true)
        let traversed = try await locate(tts: child.path + "/../tts.txt", stt: child.path + "/..")
        try await checkInstall(.whisper, traversed, pin: newSTT)
        try await checkInstall(.tone, traversed, pin: newTTS)
        let launchedBeforeMove = try await locate(tts: oldTTS.path, stt: old.path)
        try Data().write(to: oldSTT)
        try Data().write(to: oldTTS)
        for tools in [launchedBeforeMove, launchedAfterMove, traversed] {
            try await checkInstall(.whisper, tools, pin: oldSTT)
            try await checkInstall(.tone, tools, pin: oldTTS)
        }

        try fm.removeItem(at: newSTT)
        try fm.removeItem(at: newTTS)
        try fm.removeItem(at: manifest)
        try fm.createDirectory(at: legacyManifest.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data().write(to: legacyManifest)
        let launchedOnLegacy = try await locate(tts: oldTTS.path, stt: old.path)
        let defaultOnLegacy = try await locate(repo: toolbar)
        for tools in [defaultAfterMove, defaultOnLegacy] {
            try await checkInstall(.whisper, tools, pin: oldSTT)
        }
        for tools in [launchedOnLegacy, launchedAfterMove, traversed] {
            try await checkInstall(.whisper, tools, pin: oldSTT)
            try await checkInstall(.tone, tools, pin: oldTTS)
        }
        try fm.removeItem(at: old)
        try fm.removeItem(at: legacyManifest)
        try Data().write(to: manifest)
        try Data().write(to: newSTT)
        try Data().write(to: newTTS)
        for tools in [launchedOnLegacy, launchedAfterMove] {
            try await checkInstall(.whisper, tools, pin: newSTT)
            try await checkInstall(.tone, tools, pin: newTTS)
        }
        for tools in [defaultAfterMove, defaultOnLegacy] {
            try await checkInstall(.whisper, tools, pin: newSTT)
        }

        let custom = root.appending(path: "custom pins")
        try fm.createDirectory(at: custom, withIntermediateDirectories: true)
        let customTTS = custom.appending(path: "tts.txt")
        let customSTT = custom.appending(path: "stt-whisper.txt")
        try Data().write(to: customTTS)
        try Data().write(to: customSTT)
        let customWithMissingPrefix = root.appending(path: "never-created/../custom pins").path
        let cwdDepth = fm.currentDirectoryPath.split(separator: "/").count
        let relative = String(repeating: "../", count: cwdDepth) + customWithMissingPrefix.dropFirst()
        let customTools = try await locate(tts: customWithMissingPrefix + "/tts.txt", stt: relative)
        try await checkInstall(.whisper, customTools, pin: customSTT)
        try await checkInstall(.tone, customTools, pin: customTTS)
        let direct = Tools(
            uv: uv, wheel: wheel, constraints: URL(filePath: customWithMissingPrefix + "/tts.txt"), source: .override)
        try await checkInstall(.tone, direct, pin: customTTS)

        try fm.createDirectory(at: old, withIntermediateDirectories: true)
        try fm.createSymbolicLink(at: oldTTS, withDestinationURL: old.appending(path: "missing.txt"))
        for spelling in [oldTTS.path, oldTTS.path + "/.", oldTTS.path + "/"] {
            let tools = try await locate(tts: spelling)
            #expect(tools.constraints?.path != newTTS.path)
            #expect(tools.constraints?.path == URL(filePath: spelling).absoluteURL.path)
            let manager = EnvironmentManager(paths: paths, tools: tools)
            try await VariantTests.fakeEnvironment(.tone, manager, paths)
            await #expect(throws: (any Error).self) {
                try await manager.install(.tone, mode: .packages).consume { _ in }
            }
        }
        try fm.removeItem(at: oldTTS)
        for suffix in ["/.", "/"] {
            #expect(try await locate(tts: oldTTS.path + suffix).constraints?.path != newTTS.path)
        }

        try fm.createSymbolicLink(at: oldSTT, withDestinationURL: old.appending(path: "missing-stt.txt"))
        try await checkInstall(.whisper, launchedAfterMove, pin: newSTT)
        try fm.removeItem(at: newSTT)
        try await checkInstall(.whisper, launchedAfterMove, pin: nil)
        try Data().write(to: newSTT)

        try fm.removeItem(at: old)
        try Data().write(to: old)
        try await checkInstall(.whisper, launchedAfterMove, pin: nil)
        let nonDirectory = try await locate(tts: oldTTS.path)
        #expect(nonDirectory.constraints?.path == oldTTS.path)

        try fm.removeItem(at: old)
        try fm.createSymbolicLink(at: old, withDestinationURL: custom)
        try await checkInstall(.whisper, launchedAfterMove, pin: customSTT)
        try fm.removeItem(at: customSTT)
        try await checkInstall(.whisper, launchedAfterMove, pin: nil)

        let otherToolbar = root.appending(path: "other-toolbar")
        let otherOld = otherToolbar.appending(path: "constraints")
        let otherNew = otherToolbar.appending(path: "server-pins/stt/constraints")
        let otherManifest = otherToolbar.appending(path: "server-pins/tts/wheel.json")
        try fm.createDirectory(at: otherOld.appending(path: "local"), withIntermediateDirectories: true)
        try fm.createDirectory(at: otherNew, withIntermediateDirectories: true)
        try fm.createDirectory(at: otherManifest.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data().write(to: otherManifest)
        try Data().write(to: otherToolbar.appending(path: "server-pins/tts/constraints.txt"))
        try Data().write(to: otherNew.appending(path: "stt-whisper.txt"))
        let otherChild = otherToolbar.appending(path: "child")
        try fm.createDirectory(at: otherChild, withIntermediateDirectories: false)
        try fm.removeItem(at: old)
        try fm.createSymbolicLink(at: old, withDestinationURL: otherChild)
        let liveTraversal = try await locate(stt: old.path + "/../constraints")
        try await checkInstall(.whisper, liveTraversal, pin: otherNew.appending(path: "stt-whisper.txt"))
        try fm.removeItem(at: old)
        try fm.createSymbolicLink(at: old, withDestinationURL: otherOld)
        let throughCustom = try await locate(stt: old.path + "/local/..")
        try await checkInstall(.whisper, throughCustom, pin: nil)
        try fm.removeItem(at: old)
        try fm.createSymbolicLink(at: old, withDestinationURL: otherOld.appending(path: "local"))
        let throughCustomChild = try await locate(tts: old.path + "/../tts.txt", stt: old.path + "/..")
        try await checkInstall(.whisper, throughCustomChild, pin: nil)
        #expect(throughCustomChild.constraints?.path == URL(filePath: old.path + "/../tts.txt").path)
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
