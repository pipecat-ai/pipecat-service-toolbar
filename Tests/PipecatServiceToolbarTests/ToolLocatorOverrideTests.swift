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
        try fm.createDirectory(at: toolbar.appending(path: "artifacts"), withIntermediateDirectories: true)
        try fm.createDirectory(at: toolbar.appending(path: "scripts"), withIntermediateDirectories: true)
        try fm.createDirectory(at: tts, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: toolbar.appending(path: "artifacts/tts.json"))
        try Data("[project]\nname = \"pipecat-local-tts-server\"\n".utf8).write(to: tts.appending(path: "pyproject.toml"))
        return (toolbar, tts, { try? fm.removeItem(at: root) })
    }

    @Test func aTTSCheckoutOverrideStillUsesTheToolbarsSTTConstraints() throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let found = ToolLocator.toolbarRoot(from: [toolbar.appending(path: "scripts")])
        #expect(found?.standardizedFileURL == toolbar.standardizedFileURL)
        let chosen = ToolLocator.defaultSTTConstraints(repo: tts, toolbarRoot: found)
        #expect(chosen?.standardizedFileURL == toolbar.appending(path: "constraints").standardizedFileURL)
    }

    @Test func aToolbarCheckoutOverrideBringsItsOwnSTTConstraints() throws {
        let (toolbar, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let other = toolbar.deletingLastPathComponent().appending(path: "other-toolbar")
        try FileManager.default.createDirectory(at: other.appending(path: "artifacts"), withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: other.appending(path: "artifacts/tts.json"))
        // `repo` is a toolbar checkout that is not the one the executable runs from.
        let chosen = ToolLocator.defaultSTTConstraints(repo: other, toolbarRoot: toolbar)
        #expect(chosen?.standardizedFileURL == other.appending(path: "constraints").standardizedFileURL)
        // A TTS source checkout carries none of the pins, so the executable's toolbar root still wins.
        let fromTTS = ToolLocator.defaultSTTConstraints(repo: tts, toolbarRoot: toolbar)
        #expect(fromTTS?.standardizedFileURL == toolbar.appending(path: "constraints").standardizedFileURL)
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
