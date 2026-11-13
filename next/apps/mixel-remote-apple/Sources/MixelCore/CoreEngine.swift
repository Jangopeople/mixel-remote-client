import Foundation
import MixelRemoteKit

/// The real engine: the Rust Mixel Remote engine behind the app's `EngineAPI`.
public final class CoreEngine: EngineAPI, @unchecked Sendable {
    private let engine: MixelEngine
    public let info: EngineInfo

    /// `dataDirectory`: the app's private folder (created if needed).
    public init(dataDirectory: URL) throws {
        try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        do {
            engine = try MixelEngine(dataDir: dataDirectory.path)
        } catch {
            throw Self.failure(error)
        }
        let core = engine.info()
        info = EngineInfo(version: core.version, platform: core.platform, controllerOnly: core.controllerOnly)
    }

    /// The default folder: Application Support/<bundle identifier>, the folder the desktop app 0.1.x
    /// used, so a queued removal of this computer survives the update to 2.0.
    public static func defaultDirectory() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return base.appendingPathComponent(Bundle.main.bundleIdentifier ?? "ch.mixel.remote", isDirectory: true)
    }

    /// The app's modes without a window (launch agents, the rollout script, the sign-in screen host):
    /// `nil` to start the app normally, otherwise the exit code.
    public static func runCommandLine(_ arguments: [String]) -> Int32? {
        MixelCore.runCommandLine(arguments: arguments)
    }

    static func failure(_ error: Error) -> EngineFailure {
        if case EngineError.Failed(let message) = error { return EngineFailure(message) }
        return EngineFailure(error.localizedDescription)
    }

    public func call(_ command: String, json args: String) async throws -> String {
        do { return try await engine.call(command: command, args: args) } catch { throw Self.failure(error) }
    }

    public func nextVideo(session: String, compressed: Bool) async throws -> Data {
        do { return try await engine.nextVideo(session: session, compressed: compressed) } catch { throw Self.failure(error) }
    }

    public func input(session: String, json event: String) async throws -> Bool {
        do { return try await engine.input(session: session, event: event) } catch { throw Self.failure(error) }
    }

    public func shutdown() async throws {
        do { try await engine.shutdown() } catch { throw Self.failure(error) }
    }
}
