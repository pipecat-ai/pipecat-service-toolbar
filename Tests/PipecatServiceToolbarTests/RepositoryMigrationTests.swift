import Foundation
import Testing
@testable import PipecatToolbarCore

@Suite struct RepositoryMigrationTests {
    struct Installed: Decodable {
        let service: String
        let fingerprint: String
        let python: String
        let createdAt: String
    }

    @Test func everyExistingEnvironmentRemainsReadyWithoutReinstallation() throws {
        let fixture = URL(filePath: #filePath).deletingLastPathComponent()
            .appending(path: "Fixtures/pre-extraction-environments.json")
        let rows = try JSONDecoder().decode([Installed].self, from: Data(contentsOf: fixture))
        #expect(Set(rows.map(\.service)) == Set(Service.allCases.filter { $0 != .appleSpeech }.map(\.rawValue)))
        for row in rows {
            let service = try #require(Service(rawValue: row.service))
            let root = FileManager.default.temporaryDirectory.appending(path: "toolbar-migration-\(UUID())")
            defer { try? FileManager.default.removeItem(at: root) }
            let paths = AppPaths(supportDir: root.appending(path: "support"), cacheDir: root.appending(path: "cache"),
                                 logsDir: root.appending(path: "logs"), hubDir: root.appending(path: "hub"),
                                 derivedModelsDir: root.appending(path: "derived"))
            try FileManager.default.createDirectory(at: paths.python(service).deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data("#!/bin/sh\nexit 99\n".utf8).write(to: paths.python(service))
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: paths.python(service).path)
            let oldMarker: [String: String] = ["service": row.service, "fingerprint": row.fingerprint,
                                               "python": row.python, "createdAt": row.createdAt]
            let bytes = try JSONSerialization.data(withJSONObject: oldMarker, options: [.sortedKeys])
            try bytes.write(to: paths.marker(service))
            let tools = Tools(uv: URL(filePath: "/usr/bin/false"),
                              wheel: root.appending(path: "pipecat_local_tts_server-0.9.1-py3-none-any.whl"), constraints: nil, source: .repository)
            let manager = EnvironmentManager(paths: paths, tools: tools)
            guard case .ready = manager.environmentStatus(service) else {
                Issue.record("Existing environment would refresh: \(row.service)")
                continue
            }
            // Both the pre-variant layout and current variant markers remain recognised.
            #expect(manager.isRequested(service))
            try FileManager.default.createDirectory(at: paths.variantsDir(service), withIntermediateDirectories: true)
            try Data().write(to: paths.variantMarker(service))
            #expect(manager.isRequested(service))
            #expect(try Data(contentsOf: paths.marker(service)) == bytes)
            #expect(!FileManager.default.fileExists(atPath: paths.hubDir.path))
            #expect(!FileManager.default.fileExists(atPath: paths.derivedModelsDir.path))
        }
    }

    @Test func existingPreferencesAndCacheOverridesSurvive() throws {
        let suite = "toolbar.migration.test.\(UUID())"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(["phonon", "moonshine"], forKey: "restore.wanted")
        defaults.set(false, forKey: "restoreOnLaunch")
        defaults.set("phonon", forKey: "lastUsed.tts")
        defaults.set("moonshine", forKey: "lastUsed.stt")
        #expect(UserDefaultsWantedServiceStore(defaults: defaults).read() == [.phonon, .moonshine])
        #expect(!RestorePreference.isOn(defaults))
        #expect(UserDefaultsLastUsedStore(defaults: defaults).lastUsed(.tts) == .phonon)
        #expect(UserDefaultsLastUsedStore(defaults: defaults).lastUsed(.stt) == .moonshine)
        let home = URL(filePath: "/test/home")
        let cases: [([String: String], String)] = [
            ([:], "/test/home/.cache/huggingface/hub"),
            (["XDG_CACHE_HOME": "/test/xdg"], "/test/xdg/huggingface/hub"),
            (["XDG_CACHE_HOME": "/test/xdg", "HF_HOME": "/test/hf"], "/test/hf/hub"),
            (["HF_HOME": "/test/hf", "HF_HUB_CACHE": "/test/hub"], "/test/hub"),
        ]
        for (environment, expected) in cases {
            #expect(AppPaths.huggingFaceHub(environment: environment, home: home).path == expected)
        }
        #expect(AppPaths.ttsServerCache(environment: [:], home: home).path == "/test/home/.cache/pipecat-tts-server")
        #expect(AppPaths.ttsServerCache(environment: ["XDG_CACHE_HOME": "/test/xdg"], home: home).path == "/test/xdg/pipecat-tts-server")
        #expect(AppPaths.layoutVersion == 1)
        #expect(AppPaths.folderName == "Pipecat Service Toolbar")
    }
}
