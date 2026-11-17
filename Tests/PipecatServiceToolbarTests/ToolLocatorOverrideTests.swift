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

    @Test func theRepositoryIsTheFallbackWhenNoToolbarRootIsFound() throws {
        let (_, tts, cleanup) = try Self.tree()
        defer { cleanup() }
        let chosen = ToolLocator.defaultSTTConstraints(repo: tts, toolbarRoot: nil)
        #expect(chosen?.standardizedFileURL == tts.appending(path: "constraints").standardizedFileURL)
        #expect(ToolLocator.defaultSTTConstraints(repo: nil, toolbarRoot: nil) == nil)
    }
}
