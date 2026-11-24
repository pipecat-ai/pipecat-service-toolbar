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
    }

    @Test func checkoutLocalPinOverridesFollowTheMoveOnlyWhenMissing() async throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let root = toolbar.deletingLastPathComponent()
        let fm = FileManager.default
        let wheel = root.appending(path: "custom.whl")
        try Data().write(to: wheel)
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
        func locate(ttsPin: URL, sttDirectory: URL) async throws -> Tools {
            try await ToolLocator.locate(bundle: noTools, environment: [
                "PIPECAT_TOOLBAR_UV": "/usr/bin/true",
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

        try fm.createDirectory(at: oldDirectory, withIntermediateDirectories: true)
        try Data().write(to: oldTTSPin)
        try Data().write(to: oldSTTPin)
        let existing = try await locate(ttsPin: oldTTSPin, sttDirectory: oldDirectory)
        #expect(try existing.constraints(for: Service.tone.info)?.path == oldTTSPin.path)
        #expect(try existing.constraints(for: Service.whisper.info)?.path == oldSTTPin.path)
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
