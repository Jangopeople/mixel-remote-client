import Foundation

/// A scripted engine for tests, previews and the screenshots of every screen. Each command answers
/// with the JSON set for it (or `null`); every call is recorded.
public final class MockEngine: EngineAPI, @unchecked Sendable {
    public struct Call: Equatable, Sendable {
        public var command: String
        public var args: String
        /// The arguments as a dictionary (for checks in tests).
        public var object: [String: Any] {
            (try? JSONSerialization.jsonObject(with: Data(args.utf8))) as? [String: Any] ?? [:]
        }
        public static func == (lhs: Call, rhs: Call) -> Bool { lhs.command == rhs.command && lhs.args == rhs.args }
    }

    private let lock = NSLock()
    private var replies: [String: Result<String, EngineFailure>] = [:]
    private var handlers: [String: @Sendable (String) -> Result<String, EngineFailure>] = [:]
    private var recorded: [Call] = []
    private var frames: [Data] = []
    private var inputs: [String] = []
    public var info: EngineInfo

    public init(platform: String = "macos", controllerOnly: Bool = false) {
        info = EngineInfo(version: "1.1.0", platform: platform, controllerOnly: controllerOnly)
    }

    /// Answer `command` with this JSON.
    public func reply(_ command: String, json: String) {
        lock.withLock { replies[command] = .success(json) }
    }
    /// Answer `command` with this value, encoded as the engine would (snake_case keys).
    public func reply<T: Encodable>(_ command: String, _ value: T) {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let json = (try? encoder.encode(value)).map { String(decoding: $0, as: UTF8.self) } ?? "null"
        reply(command, json: json)
    }
    public func fail(_ command: String, _ message: String) {
        lock.withLock { replies[command] = .failure(EngineFailure(message)) }
    }
    /// A reply computed from the arguments.
    public func handle(_ command: String, _ handler: @escaping @Sendable (String) -> Result<String, EngineFailure>) {
        lock.withLock { handlers[command] = handler }
    }
    public func queue(frame: Data) { lock.withLock { frames.append(frame) } }

    public var calls: [Call] { lock.withLock { recorded } }
    public func calls(_ command: String) -> [Call] { calls.filter { $0.command == command } }
    public var sentInput: [String] { lock.withLock { inputs } }

    public func call(_ command: String, json args: String) async throws -> String {
        let (reply, handler): (Result<String, EngineFailure>?, (@Sendable (String) -> Result<String, EngineFailure>)?) = lock.withLock {
            recorded.append(Call(command: command, args: args))
            return (replies[command], handlers[command])
        }
        if let handler { return try handler(args).get() }
        return try (reply ?? .success("null")).get()
    }

    public func nextVideo(session: String, compressed: Bool) async throws -> Data {
        lock.withLock { frames.isEmpty ? Data() : frames.removeFirst() }
    }

    public func input(session: String, json event: String) async throws -> Bool {
        lock.withLock { inputs.append(event) }
        return true
    }

    public func shutdown() async throws {
        lock.withLock { recorded.append(Call(command: "shutdown", args: "{}")) }
    }
}
