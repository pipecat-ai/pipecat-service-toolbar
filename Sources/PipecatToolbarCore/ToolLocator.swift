import Foundation

/// What the app needs to build and run environments: `uv`, the TTS server wheel
/// (with its `uv.lock` pins) and the hash-pinned STT constraints.
public struct Tools: Sendable, Equatable {
    public enum Source: String, Sendable { case bundle, override, repository }

    public let uv: URL
    public let wheel: URL
    /// Pins exported from `uv.lock` so TTS environments match what CI tests.
    public let constraints: URL?
    /// The folder of hash-pinned `stt-<engine>.txt` constraints for the STT environments.
    public let sttConstraints: URL?
    public let source: Source

    public init(uv: URL, wheel: URL, constraints: URL?, sttConstraints: URL? = nil, source: Source) {
        self.uv = uv.absoluteURL
        self.wheel = wheel.absoluteURL
        self.constraints = constraints?.absoluteURL
        self.sttConstraints = sttConstraints?.absoluteURL
        self.source = source
    }

    /// The constraints file `uv pip install` gets for a service: the `uv.lock`
    /// export for TTS, the engine's hash-pinned file for STT. A missing STT file
    /// is optional for repository and override runs (nil) but fails closed for a
    /// bundled build, whose hash pins are what it shipped.
    public func constraints(for info: ServiceInfo) throws -> URL? {
        switch info.runtime {
        case .ttsServer: return constraints
        case .sttHelper(let engine, _):
            guard let file = sttConstraints?.appending(path: "stt-\(engine).txt"),
                FileManager.default.fileExists(atPath: file.path)
            else {
                if source == .bundle { throw ToolLocatorError.sttConstraintsMissing("stt-\(engine).txt") }
                return nil
            }
            return file
        case .appleSpeech: return nil
        }
    }

    /// e.g. `pipecat_local_tts_server-0.7.0-py3-none-any.whl`
    public var wheelName: String { wheel.lastPathComponent }

    /// What a service's environment is built from; a change means "reinstall packages".
    /// Constraints files are deliberately not part of it (TTS and STT alike), so
    /// a pin refresh never marks an existing environment outdated.
    public func fingerprint(for info: ServiceInfo) -> String {
        switch info.runtime {
        case .ttsServer: wheelName
        case .sttHelper(_, let requirements): requirements.joined(separator: " ")
        case .appleSpeech: "system"
        }
    }
}

public enum ToolLocatorError: LocalizedError, Sendable {
    case uvNotFound
    case wheelNotFound
    case buildFailed(String)
    case sttConstraintsMissing(String)
    case repositoryOverrideNotFound(String)

    public var errorDescription: String? {
        switch self {
        case .uvNotFound:
            "uv was not found. Install it (https://docs.astral.sh/uv/) or set PIPECAT_TOOLBAR_UV."
        case .wheelNotFound:
            "The server wheel was not found. Build the app with `just app-build`, or set PIPECAT_TOOLBAR_WHEEL."
        case .buildFailed(let detail):
            "Building the server wheel failed: \(detail)"
        case .sttConstraintsMissing(let file):
            "This build is missing its hash pins (\(file)), so the speech engine can't be installed safely. Reinstall the app."
        case .repositoryOverrideNotFound(let path):
            "PIPECAT_TOOLBAR_REPO (\(path)) is not a TTS server or toolbar checkout. Fix or unset it."
        }
    }
}

/// Finds the tools. Order: the app bundle, then environment overrides
/// (`PIPECAT_TOOLBAR_UV`, `_WHEEL`, `_CONSTRAINTS`), then, when running
/// unbundled from a checkout (`swift run`), the checksum-pinned wheel `scripts/prepare_tools.py`
/// fetches for this repository, or a wheel built from the TTS checkout `PIPECAT_TOOLBAR_REPO` names
/// (a `PIPECAT_TOOLBAR_REPO` that names no checkout throws rather than falling back).
public enum ToolLocator {
    private static let wheelManifest = "server-pins/tts/wheel.json"

    private static func isToolbarCheckout(_ root: URL) -> Bool {
        let files = FileManager.default
        // A repository override can point to a toolbar checkout from before the pin move.
        return files.fileExists(atPath: root.appending(path: wheelManifest).path)
            || files.fileExists(atPath: root.appending(path: "artifacts/tts.json").path)
    }

    public static func locate(
        bundle: Bundle = .main,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        paths: AppPaths,
        processes: ProcessRegistry = ProcessRegistry()
    ) async throws -> Tools {
        let fm = FileManager.default

        if let resources = bundle.resourceURL {
            let uv = resources.appending(path: "bin/uv")
            let wheels = resources.appending(path: "wheels")
            if fm.isExecutableFile(atPath: uv.path),
                let name = try? String(contentsOf: wheels.appending(path: "MANIFEST"), encoding: .utf8)
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                !name.isEmpty, fm.fileExists(atPath: wheels.appending(path: name).path)
            {
                let constraints = wheels.appending(path: "constraints.txt")
                let sttConstraints = resources.appending(path: "constraints")
                return Tools(
                    uv: uv, wheel: wheels.appending(path: name),
                    constraints: fm.fileExists(atPath: constraints.path) ? constraints : nil,
                    sttConstraints: fm.fileExists(atPath: sttConstraints.path) ? sttConstraints : nil,
                    source: .bundle)
            }
        }

        guard let uv = findUV(environment: environment) else { throw ToolLocatorError.uvNotFound }

        let repo = findRepositoryRoot(environment: environment)
        let sttConstraints = environment["PIPECAT_TOOLBAR_STT_CONSTRAINTS"].map { URL(filePath: $0) }
            ?? defaultSTTConstraints(repo: repo, toolbarRoot: findToolbarRoot())

        if let wheelPath = environment["PIPECAT_TOOLBAR_WHEEL"], fm.fileExists(atPath: wheelPath) {
            let constraints = environment["PIPECAT_TOOLBAR_CONSTRAINTS"].map { URL(filePath: $0) }
            return Tools(
                uv: uv, wheel: URL(filePath: wheelPath), constraints: constraints,
                sttConstraints: sttConstraints, source: .override)
        }

        // An explicit checkout that matches nothing is a mistake, not a request for the pinned
        // release: without this, a typo would silently swap the intended source for the wheel
        // `findRepositoryRoot` falls back to from the executable or working directory.
        if let explicit = repositoryOverride(environment), explicitRepositoryRoot(explicit) == nil {
            throw ToolLocatorError.repositoryOverrideNotFound(explicit)
        }
        guard let repo else { throw ToolLocatorError.wheelNotFound }
        return try await buildFromRepository(
            repo, uv: uv, sttConstraints: sttConstraints, paths: paths, processes: processes)
    }

    static func findUV(environment: [String: String]) -> URL? {
        let fm = FileManager.default
        var candidates: [String] = []
        if let explicit = environment["PIPECAT_TOOLBAR_UV"] { candidates.append(explicit) }
        let home = fm.homeDirectoryForCurrentUser.path
        candidates += ["\(home)/.local/bin/uv", "\(home)/.cargo/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
        for dir in (environment["PATH"] ?? "").split(separator: ":") {
            candidates.append("\(dir)/uv")
        }
        return candidates.first(where: fm.isExecutableFile(atPath:)).map { URL(filePath: $0) }
    }

    /// Walks up from `PIPECAT_TOOLBAR_REPO` (when set and non-empty), the executable and the working directory to
    /// this repo's root, or (only with `PIPECAT_TOOLBAR_REPO` set) a TTS source checkout.
    static func findRepositoryRoot(environment: [String: String]) -> URL? {
        var starts = [Bundle.main.executableURL?.deletingLastPathComponent()]
        starts.append(URL(filePath: FileManager.default.currentDirectoryPath))
        let explicit = repositoryOverride(environment)
        if let explicit { starts.insert(URL(filePath: explicit), at: 0) }
        return repositoryRoot(from: starts.compactMap { $0 }, acceptingTTSCheckout: explicit != nil)
    }

    /// `PIPECAT_TOOLBAR_REPO` when set to something: an empty or whitespace-only value is unset
    /// (`URL(filePath: "")` would otherwise resolve to the working directory).
    static func repositoryOverride(_ environment: [String: String]) -> String? {
        guard let value = environment["PIPECAT_TOOLBAR_REPO"] else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// The checkout `PIPECAT_TOOLBAR_REPO` names (a toolbar or TTS source checkout at or above
    /// `path`), or nil when it names neither and `locate` must report it.
    static func explicitRepositoryRoot(_ path: String) -> URL? {
        repositoryRoot(from: [URL(filePath: path)], acceptingTTSCheckout: true)
    }

    static func repositoryRoot(from starts: [URL], acceptingTTSCheckout: Bool) -> URL? {
        for start in starts {
            var dir = start.standardizedFileURL
            while dir.path != "/" {
                if isToolbarCheckout(dir) {
                    return dir
                }
                let pyproject = dir.appending(path: "pyproject.toml")
                if acceptingTTSCheckout,
                    let text = try? String(contentsOf: pyproject, encoding: .utf8),
                    text.contains("name = \"pipecat-local-tts-server\"")
                {
                    return dir
                }
                dir.deleteLastPathComponent()
            }
        }
        return nil
    }

    /// The checkout whose wheel manifest marks it as this toolbar repository, found from the
    /// executable and the working directory only. `PIPECAT_TOOLBAR_REPO` is deliberately not
    /// consulted: it may name a TTS source checkout, which carries none of the toolbar's pins.
    static func findToolbarRoot() -> URL? {
        var starts = [Bundle.main.executableURL?.deletingLastPathComponent()]
        starts.append(URL(filePath: FileManager.default.currentDirectoryPath))
        return toolbarRoot(from: starts.compactMap { $0 })
    }

    static func toolbarRoot(from starts: [URL]) -> URL? {
        repositoryRoot(from: starts, acceptingTTSCheckout: false)
    }

    /// Where the hash-pinned STT constraints come from when `PIPECAT_TOOLBAR_STT_CONSTRAINTS` is
    /// unset: this toolbar repository's pins, even when `repo` is an explicit TTS source
    /// checkout (`PIPECAT_TOOLBAR_REPO`), so that override never installs STT environments unpinned.
    /// `repo` is used only when no toolbar root is found (an unbundled binary run elsewhere). A `repo`
    /// that is itself a toolbar checkout (`PIPECAT_TOOLBAR_REPO` naming another clone) wins over the
    /// root found from the executable, so the wheel `buildFromRepository` prepares and the pins it
    /// is installed with come from the same checkout.
    static func defaultSTTConstraints(repo: URL?, toolbarRoot: URL?) -> URL? {
        let selected: URL?
        if let repo, isToolbarCheckout(repo) {
            selected = repo
        } else {
            selected = toolbarRoot ?? repo
        }
        guard let selected else { return nil }
        let pinDirectory = FileManager.default.fileExists(atPath: selected.appending(path: wheelManifest).path)
            ? "server-pins/stt/constraints" : "constraints"
        return selected.appending(path: pinDirectory)
    }

    static func buildFromRepository(
        _ repo: URL, uv: URL, sttConstraints: URL? = nil, paths: AppPaths,
        processes: ProcessRegistry = ProcessRegistry()
    ) async throws -> Tools {
        let out = paths.cacheDir.appending(path: "dev-wheel")
        try? FileManager.default.removeItem(at: out)
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let env = ProcessEnvironment.base()

        // Standalone checkout: acquire the verified release without a server checkout.
        if isToolbarCheckout(repo) {
            let prepare = Subprocess(.init(
                executable: uv,
                arguments: ["run", "--no-project", "--python", "3.12", "scripts/prepare_tools.py", "--output", out.path],
                environment: env, currentDirectory: repo))
            let result = try await processes.collect(prepare)
            guard result.status == 0 else {
                throw ToolLocatorError.buildFailed(result.stderr.suffix(3).joined(separator: "\n"))
            }
            let name = try String(contentsOf: out.appending(path: "MANIFEST"), encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return Tools(uv: uv, wheel: out.appending(path: name),
                         constraints: out.appending(path: "constraints.txt"),
                         sttConstraints: sttConstraints, source: .repository)
        }

        let build = Subprocess(
            .init(
                executable: uv, arguments: ["build", "--wheel", "--out-dir", out.path],
                environment: env, currentDirectory: repo))
        // Registered so app quit stops it (no manager exists yet to own it).
        let built = try await processes.collect(build)
        guard built.status == 0 else {
            throw ToolLocatorError.buildFailed((built.stderr.suffix(3)).joined(separator: "\n"))
        }
        let wheel = try FileManager.default.contentsOfDirectory(at: out, includingPropertiesForKeys: nil)
            .first { $0.lastPathComponent.hasPrefix("pipecat_local_tts_server-") && $0.pathExtension == "whl" }
        guard let wheel else { throw ToolLocatorError.wheelNotFound }

        let constraints = out.appending(path: "constraints.txt")
        let export = Subprocess(
            .init(
                executable: uv,
                arguments: [
                    "export", "--all-extras", "--no-dev", "--no-hashes", "--no-emit-project",
                    "--format", "requirements.txt", "--output-file", constraints.path,
                ],
                environment: env, currentDirectory: repo))
        let exported = try await processes.collect(export)
        return Tools(
            uv: uv, wheel: wheel, constraints: exported.status == 0 ? constraints : nil,
            sttConstraints: sttConstraints, source: .repository)
    }
}

/// The minimal, explicit environment every child process starts from.
///
/// GUI apps inherit almost nothing from launchd, and `swift run` inherits a whole
/// developer shell; spelling it out gives both the same behaviour.
public enum ProcessEnvironment {
    public static func base(inherited: [String: String] = ProcessInfo.processInfo.environment) -> [String: String] {
        var env: [String: String] = [
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
        ]
        env["TMPDIR"] = inherited["TMPDIR"] ?? NSTemporaryDirectory()
        // Honour a user's own cache locations: Hugging Face's, and Moonshine's
        // (`MOONSHINE_VOICE_CACHE`, read by moonshine-voice's `get_cache_dir`), so the app's
        // `prefetch --check` and `serve` find the same weights a command-line server does.
        // The token is not here: see `huggingFace()`.
        for key in ["HF_HOME", "HF_HUB_CACHE", "XDG_CACHE_HOME", "MOONSHINE_VOICE_CACHE", "PIPECAT_TTS_PHONON_VOICES"] {
            if let value = inherited[key] { env[key] = value }
        }
        return env
    }

    /// `base()` plus the user's `HF_TOKEN`, for the processes that download
    /// models (`prefetch`, `serve`) and nothing else: `uv` runs package build
    /// scripts, which must never see the credential.
    public static func huggingFace() -> [String: String] {
        var env = base()
        if let token = ProcessInfo.processInfo.environment["HF_TOKEN"] { env["HF_TOKEN"] = token }
        return env
    }

    /// `huggingFace()` for a service whose weights come from the Hugging Face cache
    /// (`ServiceInfo.weightsInHubCache`), `base()` for one
    /// that never contacts the Hub (Moonshine downloads from its own CDN; tone and Apple Speech
    /// download nothing), so the credential reaches only a process that can use it.
    public static func huggingFace(for service: Service) -> [String: String] {
        service.info.weightsInHubCache ? huggingFace() : base()
    }

    /// The environment a service's `serve` child starts from: `huggingFace(for:)`, plus the
    /// Homebrew prefixes on `PATH` for a service whose catalog entry says its server runs a
    /// Homebrew tool (`ServiceInfo.serveRunsHomebrewTools`: Parakeet's `ffmpeg`). A GUI app's
    /// minimal `PATH` cannot see a Homebrew install.
    public static func serve(_ service: Service) -> [String: String] {
        var env = huggingFace(for: service)
        if service.info.serveRunsHomebrewTools {
            env["PATH"] = (env["PATH"] ?? "") + ":/opt/homebrew/bin:/usr/local/bin"
        }
        return env
    }

    /// `base()` plus the uv settings that keep the app's Python fully self-contained.
    public static func uv(paths: AppPaths) -> [String: String] {
        var env = base()
        env["UV_PYTHON_INSTALL_DIR"] = paths.pythonDir.path
        env["UV_CACHE_DIR"] = paths.uvCacheDir.path
        env["UV_PYTHON_PREFERENCE"] = "only-managed"
        env["UV_NO_CONFIG"] = "1"
        env["UV_NO_PROGRESS"] = "1"
        return env
    }
}
