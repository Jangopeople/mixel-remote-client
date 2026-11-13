import Foundation

/// What the app needs from the Mixel Remote engine. The real engine (MixelCore, Rust) and the test
/// engine (`MockEngine`) both provide it, so every screen and view model runs against either.
///
/// Commands use the names and arguments of the desktop app (`remote_status`, `saas_members`, …);
/// arguments and results are JSON. The picture and input have their own calls.
public protocol EngineAPI: AnyObject, Sendable {
    var info: EngineInfo { get }
    /// Runs one command; `args` is a JSON object, the result is JSON. Throws `EngineFailure`.
    func call(_ command: String, json args: String) async throws -> String
    /// The next session picture ("MRF2" RGBA or "MRH1" H.264), empty when none is waiting.
    func nextVideo(session: String, compressed: Bool) async throws -> Data
    /// One input event (JSON as the desktop app sends it). `false`: the computer could not apply it.
    func input(session: String, json event: String) async throws -> Bool
    /// Before the app quits.
    func shutdown() async throws
}

public struct EngineInfo: Equatable, Sendable {
    public var version: String
    public var platform: String
    public var controllerOnly: Bool
    public init(version: String, platform: String, controllerOnly: Bool) {
        self.version = version
        self.platform = platform
        self.controllerOnly = controllerOnly
    }
}

/// A command that failed; the message comes from the engine (English) and is shown translated.
public struct EngineFailure: Error, LocalizedError, Equatable, Sendable {
    public var message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { Translator.shared.translate(message) }
}

/// Arguments without fields.
public struct NoArgs: Encodable, Sendable { public init() {} }

private let encoder: JSONEncoder = {
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    return encoder
}()
private let decoder: JSONDecoder = {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return decoder
}()

public extension EngineAPI {
    /// Runs a command with `Encodable` arguments (property names become snake_case) and decodes
    /// the result.
    func run<Result: Decodable>(_ command: String, _ args: some Encodable = NoArgs(), as type: Result.Type = Result.self) async throws -> Result {
        let json = String(decoding: try encoder.encode(args), as: UTF8.self)
        let reply = try await call(command, json: json)
        do {
            return try decoder.decode(Result.self, from: Data(reply.utf8))
        } catch {
            throw EngineFailure("The app could not read the engine's answer to \(command)")
        }
    }

    /// Runs a command whose result is not needed.
    func perform(_ command: String, _ args: some Encodable = NoArgs()) async throws {
        let json = String(decoding: try encoder.encode(args), as: UTF8.self)
        _ = try await call(command, json: json)
    }

    func send(input event: InputEvent, session: String) async throws -> Bool {
        let json = String(decoding: try encoder.encode(event), as: UTF8.self)
        return try await input(session: session, json: json)
    }
}

/// Untyped JSON for results the app only partly reads (company overview, features).
public enum JSONValue: Codable, Equatable, Sendable {
    case null, bool(Bool), number(Double), string(String), array([JSONValue]), object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([JSONValue].self) { self = .array(value) }
        else { self = .object(try container.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    public subscript(key: String) -> JSONValue? {
        if case .object(let object) = self { return object[key] }
        return nil
    }
    public var string: String? { if case .string(let value) = self { return value }; return nil }
    public var number: Double? { if case .number(let value) = self { return value }; return nil }
    public var bool: Bool? { if case .bool(let value) = self { return value }; return nil }
}
