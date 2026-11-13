import Foundation
import SwiftUI

/// The sections of the app (sidebar on the Mac, tabs on the phone).
public enum AppSection: String, CaseIterable, Identifiable, Sendable {
    case remote, devices, account, settings
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .remote: L("Remote control")
        case .devices: L("Devices")
        case .account: L("Account")
        case .settings: L("Settings")
        }
    }
    public var symbol: String {
        switch self {
        case .remote: "arrow.left.arrow.right"
        case .devices: "laptopcomputer.and.iphone"
        case .account: "person.crop.circle"
        case .settings: "gearshape"
        }
    }
}

/// The app's state: the account, the connection to Mixel and the parts of the app. Reads the engine
/// a few times a second while something happens, rarely otherwise.
@MainActor
public final class AppModel: ObservableObject {
    public let engine: EngineAPI
    @Published public var section: AppSection = .remote
    @Published public private(set) var account = AccountStatus(phase: "signed_out", message: "Sign in with your Mixel account")
    @Published public private(set) var remote = RemoteStatus.offline
    /// The last failure of an action, shown near the action.
    @Published public var error = ""
    /// Progress of a connection being prepared ("Looking up this ID…").
    @Published public var progress = ""
    @Published public var busy = false
    /// The sign-in link to open in another browser.
    @Published public var signInLink: String?
    @Published public var verificationCode = ""

    public lazy var devices = DevicesModel(app: self)
    public lazy var session = SessionModel(app: self)
    public lazy var host = HostModel(app: self)
    public lazy var company = CompanyModel(app: self)
    public lazy var settings = SettingsModel(app: self)
    public lazy var enrollment = EnrollmentModel(app: self)

    /// The app opens web pages (sign-in, legal texts) through this.
    public var openURL: (URL) -> Void = { _ in }
    /// The app's Open panel: a file to send (absolute path), or nil.
    public var chooseFile: () async -> String? = { nil }
    /// The app's Save panel for a suggested name: where to save (absolute path), or nil.
    public var chooseSaveLocation: (_ suggested: String) async -> String? = { _ in nil }
    /// The person chose "Go offline": do not come back online by itself until they connect again.
    var wentOffline = false
    private var autoTries = 0
    private var nextAutoTry = Date.distantPast
    private var loop: Task<Void, Never>?
    private var lastAccountRead = Date.distantPast
    private var accountKey = ""

    public init(engine: EngineAPI) {
        self.engine = engine
    }

    public var info: EngineInfo { engine.info }
    public var signedIn: Bool { account.signedIn }
    public var isPhone: Bool { engine.info.platform == "ios" }

    /// Starts reading the engine. Idempotent.
    public func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in
            await self?.refreshAccount()
            await self?.settings.flushPendingRemoval()
            while !Task.isCancelled {
                guard let self else { return }
                await self.tick()
                let fast = self.remote.inSession || ["connecting", "outgoing"].contains(self.remote.phase) || self.account.signingIn
                try? await Task.sleep(nanoseconds: fast ? 250_000_000 : 1_000_000_000)
            }
        }
    }

    public func stop() {
        loop?.cancel()
        loop = nil
    }

    func tick() async {
        let accountEvery: TimeInterval = account.signingIn || account.phase == "mfa_required" ? 1 : 10
        if Date().timeIntervalSince(lastAccountRead) >= accountEvery { await refreshAccount() }
        await refreshRemote()
    }

    public func refreshAccount() async {
        lastAccountRead = Date()
        guard let next: AccountStatus = try? await engine.run("account_status") else { return }
        if next != account { account = next }
        let key = next.signedIn ? "\(next.account?.profile ?? "")|\(next.account?.organization ?? "")|\(next.account?.personal ?? false)" : ""
        if key != accountKey {
            accountKey = key
            signInLink = nil
            accountChanged()
        }
    }

    public func refreshRemote() async {
        guard let next: RemoteStatus = try? await engine.run("remote_status") else { return }
        let previous = remote
        if next != previous { remote = next }
        if next.phase == "ready" { autoTries = 0; nextAutoTry = .distantPast }
        session.statusChanged(from: previous, to: next)
        host.statusChanged(from: previous, to: next)
        devices.statusChanged(from: previous, to: next)
        await goOnlineIfNeeded()
    }

    /// A set-up computer goes online by itself once someone is signed in, so the person who needs
    /// help never has to look for a button. Not again after "Go offline", with a growing pause.
    func goOnlineIfNeeded() async {
        guard signedIn, !wentOffline, remote.disconnected, !busy, Date() >= nextAutoTry, !enrollment.needsSetup else { return }
        let pauses: [TimeInterval] = [0, 5, 15, 30, 60]
        autoTries += 1
        nextAutoTry = Date().addingTimeInterval(pauses[min(autoTries, pauses.count - 1)])
        do {
            try await engine.perform("remote_register")
        } catch {
            enrollment.registerFailed(error)
        }
    }

    private func accountChanged() {
        error = ""
        devices.reset()
        company.reset()
        enrollment.reset()
        if signedIn {
            Task {
                await company.load()
                await enrollment.check()
                await devices.load()
                await settings.load()
            }
        }
    }

    /// Runs an action; its failure shows in `error`. The engine is read again afterwards.
    public func act(_ work: @escaping () async throws -> Void) {
        guard !busy else { return }
        busy = true
        error = ""
        Task {
            do { try await work() } catch { self.error = Self.message(error) }
            self.busy = false
            await self.refreshRemote()
        }
    }

    public static func message(_ error: Error) -> String {
        if let failure = error as? EngineFailure { return failure.errorDescription ?? failure.message }
        return error.localizedDescription
    }

    // MARK: Sign-in

    public func signIn(showLink: Bool = false) {
        act {
            let url: String = try await self.engine.run("account_sign_in", ["open_browser": false])
            if showLink { self.signInLink = url }
            else if let link = URL(string: url) { self.openURL(link) }
            await self.refreshAccount()
        }
    }

    public func cancelSignIn() {
        act {
            try await self.engine.perform("account_cancel")
            await self.refreshAccount()
        }
    }

    public func verify(factor: String) {
        let code = verificationCode.filter(\.isASCIIDigit)
        guard code.count == 6 else { error = L("Enter the 6-digit code"); return }
        verificationCode = ""
        act {
            try await self.engine.perform("account_verify", ["factor": factor, "code": code])
            await self.refreshAccount()
        }
    }

    public func signOut() {
        act {
            try? await self.engine.perform("remote_disconnect")
            try await self.engine.perform("set_stay_signed_in", ["enabled": false])
            try await self.engine.perform("account_cancel")
            await self.refreshAccount()
        }
    }

    // MARK: The connection

    public func goOnline() {
        wentOffline = false
        autoTries = 0
        nextAutoTry = .distantPast
        act { try await self.engine.perform("remote_register") }
    }

    public func goOffline() {
        wentOffline = true
        session.clear()
        act { try await self.engine.perform("remote_disconnect") }
    }

    /// Connects to a Remote ID: brings this computer online first if needed; for a phone with Mixel
    /// Quick Support it claims the phone and waits until it is online, so one click is enough.
    public func connect(to id: UInt32, password: String? = nil, unattended: Bool = false) {
        guard !busy else { progress = L("Please wait a moment, the previous step is still running."); return }
        busy = true
        error = ""
        Task {
            defer { busy = false }
            do {
                try await connectSteps(id: id, password: password, unattended: unattended)
            } catch {
                self.error = Self.message(error)
                progress = ""
            }
            await refreshRemote()
        }
    }

    private func status() async -> RemoteStatus? { try? await engine.run("remote_status") }

    private func wait(seconds: Int, until: (RemoteStatus) -> Bool, ask: (() async -> Void)? = nil) async -> Bool {
        for round in 0..<seconds {
            if let state = await status(), until(state) { return true }
            if let ask, round % 2 == 0 { await ask() }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
        return false
    }

    func connectSteps(id: UInt32, password: String?, unattended: Bool) async throws {
        wentOffline = false
        if let state = await status(), state.disconnected {
            progress = L("Getting this computer ready…")
            try await engine.perform("remote_register")
            guard await wait(seconds: 20, until: { $0.phase == "ready" }) else {
                throw EngineFailure("This computer could not go online. Check the internet connection and try again.")
            }
        }
        progress = L("Looking up this ID…")
        let phone: Bool
        do {
            phone = try await engine.run("quick_phone_claim", ["host": id])
        } catch let failure as EngineFailure {
            if failure.message.range(of: "MFA|staff|42501", options: .regularExpression) != nil {
                throw EngineFailure("Connecting to phones needs your Mixel team account with the second confirmation step. Sign in again under Account, then choose Connect.")
            }
            throw EngineFailure("This phone could not be prepared for the session: \(failure.message)")
        }
        if phone {
            progress = L("Phone found. Waiting for it to come online… On an iPhone the person taps “Start sharing my screen”.")
            let online = await wait(seconds: 120, until: { $0.online.contains(id) }, ask: {
                try? await self.engine.perform("remote_presence", ["hosts": [id]])
            })
            guard online else {
                throw EngineFailure("The phone did not come online. Ask the person to keep Mixel Quick Support open, then choose Connect again.")
            }
            progress = L("Connecting… The person allows the session on their phone.")
        }
        if let state = await status(), state.phase != "ready" {
            throw EngineFailure(["active", "incoming", "outgoing"].contains(state.phase)
                ? "End the current session first, then connect again."
                : "This computer is not ready to connect yet. Please try again in a moment.")
        }
        struct Request: Encodable { var host: UInt32; var password: String?; var unattended: Bool }
        try await engine.perform("remote_request", Request(host: id, password: password?.isEmpty == false ? password : nil, unattended: phone ? false : unattended))
        session.lastRequestUnattended = unattended && !phone
        progress = L("Waiting for the other person to accept your request.")
    }

    /// The status line of the connection, as the desktop app words it.
    public var connectionMessage: String {
        if session.stopping { return L("Ending the session…") }
        switch remote.phase {
        case "offline":
            if remote.controllerOnly {
                return signedIn ? L("Connect this controller to the Mixel network.") : L("Sign in to access managed devices.")
            }
            return signedIn ? L("Make this computer available to start or receive a connection.") : L("Sign in to get your connection ID.")
        case "ready": return remote.message.isEmpty ? L("Ready for a connection.") : Translator.shared.translate(remote.message)
        case "outgoing": return L("Waiting for the other person to accept your request.")
        case "connecting", "deciding", "negotiating": return L("Connecting securely…")
        case "active":
            if remote.sharing { return L("Sharing your selected screen. You can end the session at any time.") }
            return remote.control ? L("Connected. Keyboard and mouse control is enabled.") : L("Connected in view-only mode.")
        default: return Translator.shared.translate(remote.message)
        }
    }

    /// Before the app quits: end sessions, go offline.
    public func shutdown() async {
        stop()
        session.clear()
        try? await engine.shutdown()
    }
}

/// Lets `["key": value]` literals of mixed values be used as command arguments.
public struct AnyArgs: Encodable {
    let values: [String: AnyEncodable]
    public init(_ values: [String: Any?]) {
        self.values = values.mapValues { AnyEncodable($0) }
    }
    public func encode(to encoder: Encoder) throws { try values.encode(to: encoder) }
}

struct AnyEncodable: Encodable {
    let value: Any?
    init(_ value: Any?) { self.value = value }
    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch value {
        case nil: try container.encodeNil()
        case let value as Bool: try container.encode(value)
        case let value as String: try container.encode(value)
        case let value as Int: try container.encode(value)
        case let value as UInt32: try container.encode(value)
        case let value as UInt16: try container.encode(value)
        case let value as UInt64: try container.encode(value)
        case let value as Double: try container.encode(value)
        case let value as [UInt32]: try container.encode(value)
        case let value as [String]: try container.encode(value)
        case let value as Decision: try container.encode(value)
        case let value as any Encodable: try container.encode(AnyBox(value))
        default: try container.encodeNil()
        }
    }
}

private struct AnyBox: Encodable {
    let value: any Encodable
    init(_ value: any Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}

public extension EngineAPI {
    /// A command with dictionary arguments: `run("remote_close", ["session": id])`.
    func run<Result: Decodable>(_ command: String, _ args: [String: Any?], as type: Result.Type = Result.self) async throws -> Result {
        try await run(command, AnyArgs(args), as: type)
    }
    func perform(_ command: String, _ args: [String: Any?]) async throws {
        try await perform(command, AnyArgs(args))
    }
}
