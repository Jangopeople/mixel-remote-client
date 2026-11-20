import Foundation

/// A device as the administration API lists it (`remote_admin_devices`, section 2.1 of
/// docs/mixel-remote-device-admin-api.md). Everything but the identity is optional: older servers and
/// apps report less, and the console shows "—" or nothing rather than a guess.
public struct AdminDevice: Codable, Equatable, Sendable, Identifiable {
    public var deviceId: String
    public var connectionId: UInt32
    public var name: String
    public var os: String
    public var groupName: String?
    public var note: String?
    public var favorite: Bool?
    /// `default`, `on` or `off` (what the administrator set).
    public var unattendedSetting: String?
    /// What a new app turns on by itself.
    public var unattendedDefault: Bool?
    /// False when the setting is `off`: the broker refuses unattended access to this device.
    public var unattendedAllowed: Bool?
    /// `online`, `offline`, `unknown` or `not_enrolled`.
    public var status: String?
    public var lastSeen: String?
    public var appVersion: String?
    public var platform: String?
    public var ownerName: String?
    /// Send back as `expected_update` for a rename; a stale value answers 409 `changed`.
    public var updatedAt: String?
    /// The caller may rename, move, note, switch unattended access and remove this device.
    public var canAdmin: Bool?
    /// The ISM company (or SaaS tenant client) the device belongs to.
    public var companyName: String? = nil
    public var id: String { deviceId }

    public init(deviceId: String, connectionId: UInt32, name: String, os: String, groupName: String? = nil, note: String? = nil, favorite: Bool? = nil,
                unattendedSetting: String? = nil, unattendedDefault: Bool? = nil, unattendedAllowed: Bool? = nil, status: String? = nil, lastSeen: String? = nil,
                appVersion: String? = nil, platform: String? = nil, ownerName: String? = nil, updatedAt: String? = nil, canAdmin: Bool? = nil) {
        self.deviceId = deviceId
        self.connectionId = connectionId
        self.name = name
        self.os = os
        self.groupName = groupName
        self.note = note
        self.favorite = favorite
        self.unattendedSetting = unattendedSetting
        self.unattendedDefault = unattendedDefault
        self.unattendedAllowed = unattendedAllowed
        self.status = status
        self.lastSeen = lastSeen
        self.appVersion = appVersion
        self.platform = platform
        self.ownerName = ownerName
        self.updatedAt = updatedAt
        self.canAdmin = canAdmin
    }

    /// Unattended access is on for this device: switched on, or the company default and allowed.
    public var unattendedOn: Bool? {
        guard let setting = unattendedSetting else { return nil }
        switch setting {
        case "on": return true
        case "off": return false
        default: return unattendedDefault == true && unattendedAllowed != false
        }
    }
}

/// Why an administration call did not happen. `unavailable` means this engine has no
/// `remote_admin_*` commands yet (the console then hides what needs them).
public enum DeviceAdminError: Error, Equatable, LocalizedError {
    case unavailable
    /// The server said no; `hint` is its stable key (`not_admin`, `changed`, `confirm_mismatch`, …).
    case refused(hint: String, message: String)

    public var errorDescription: String? {
        switch self {
        case .unavailable: L("Device administration is not available in this version yet.")
        case .refused(let hint, let message):
            switch hint {
            case "not_admin": L("Only an owner or administrator can change devices.")
            case "changed": L("The device was changed by someone else. Refresh the list and try again.")
            case "confirm_mismatch": L("The ID does not match this device.")
            case "mfa_required": L("This change needs the second confirmation step. Sign in again.")
            case "read_only": L("This company is read-only. Adding devices and other changes need an active plan.")
            case "device_not_found": L("This device is no longer in your company.")
            default: Translator.shared.translate(message)
            }
        }
    }
}

/// The device administration contract (docs/mixel-remote-device-admin-api.md, section 2). One
/// protocol, so the console does not care whether the engine already has the commands: today the
/// app uses `UnavailableDeviceAdminService`, which answers "not available" and never invents data.
public protocol DeviceAdminService: Sendable {
    /// True when the commands exist, so the console may show what needs them.
    var available: Bool { get }
    func devices(company: String?, search: String?) async throws -> [AdminDevice]
    func rename(device: String, name: String, expectedUpdate: String?) async throws -> AdminDevice
    func setNote(device: String, note: String?) async throws -> AdminDevice
    func setFavorite(device: String, favorite: Bool) async throws -> Bool
    /// `setting`: `default`, `on` or `off`.
    func setUnattended(device: String, setting: String) async throws -> AdminDevice
    /// `confirmConnectionId` must equal the device's 9-digit ID.
    func remove(device: String, confirmConnectionId: UInt32) async throws
    /// Who may connect to the device without anyone there being asked (`remote_admin_unattended_grants`).
    func unattendedGrants(device: String) async throws -> [UnattendedGrant]
    /// Gives or takes away one person's unattended access (`remote_admin_set_unattended_grant`).
    func setUnattendedGrant(device: String, user: String, granted: Bool) async throws
}

/// One row of `remote_admin_unattended_grants`: a per-user grant, or automatic access from the company setting.
public struct UnattendedGrant: Codable, Equatable, Sendable {
    public var userId: String
    public var name: String?
    public var role: String?
    /// `company_setting` for automatic access (read-only); `grant` (or absent) for a per-user grant.
    public var source: String?
    public init(userId: String, name: String? = nil, role: String? = nil, source: String? = nil) {
        self.userId = userId; self.name = name; self.role = role; self.source = source
    }
}

/// One person in the "Who may connect" list.
public struct GrantRow: Equatable, Sendable, Identifiable {
    public var userId: String
    public var name: String
    public var email: String
    public var role: String
    public var granted: Bool
    public var automatic: Bool
    public var id: String { userId }
}

/// No administration commands in the engine: every call says so.
public struct UnavailableDeviceAdminService: DeviceAdminService {
    public init() {}
    public var available: Bool { false }
    public func devices(company: String?, search: String?) async throws -> [AdminDevice] { throw DeviceAdminError.unavailable }
    public func rename(device: String, name: String, expectedUpdate: String?) async throws -> AdminDevice { throw DeviceAdminError.unavailable }
    public func setNote(device: String, note: String?) async throws -> AdminDevice { throw DeviceAdminError.unavailable }
    public func setFavorite(device: String, favorite: Bool) async throws -> Bool { throw DeviceAdminError.unavailable }
    public func setUnattended(device: String, setting: String) async throws -> AdminDevice { throw DeviceAdminError.unavailable }
    public func remove(device: String, confirmConnectionId: UInt32) async throws { throw DeviceAdminError.unavailable }
    public func unattendedGrants(device: String) async throws -> [UnattendedGrant] { throw DeviceAdminError.unavailable }
    public func setUnattendedGrant(device: String, user: String, granted: Bool) async throws { throw DeviceAdminError.unavailable }
}

/// The engine commands asked for in docs/mixel-remote-ui-engine-requests.md (item 1): same names and
/// arguments as the RPC bodies. Not used by the app until the engine has them (`UnavailableDeviceAdminService`
/// is the default); a test runs it against the mock engine so only the switch is left to do.
public struct EngineDeviceAdminService: DeviceAdminService {
    let engine: EngineAPI
    public init(engine: EngineAPI) { self.engine = engine }
    public var available: Bool { true }

    public func devices(company: String?, search: String?) async throws -> [AdminDevice] {
        try await call { try await engine.run("remote_admin_devices", ["company": company, "search": search]) }
    }
    public func rename(device: String, name: String, expectedUpdate: String?) async throws -> AdminDevice {
        try await call { try await engine.run("remote_admin_rename_device", ["device": device, "device_name": name, "expected_update": expectedUpdate]) }
    }
    public func setNote(device: String, note: String?) async throws -> AdminDevice {
        try await call { try await engine.run("remote_admin_set_device_note", ["device": device, "note": note]) }
    }
    public func setFavorite(device: String, favorite: Bool) async throws -> Bool {
        try await call { try await engine.run("remote_admin_set_device_favorite", ["device": device, "favorite": favorite]) }
    }
    public func setUnattended(device: String, setting: String) async throws -> AdminDevice {
        try await call { try await engine.run("remote_admin_set_device_unattended", ["device": device, "setting": setting]) }
    }
    public func remove(device: String, confirmConnectionId: UInt32) async throws {
        try await call { try await engine.perform("remote_admin_remove_device", ["device": device, "confirm_connection_id": confirmConnectionId]) }
    }

    public func unattendedGrants(device: String) async throws -> [UnattendedGrant] {
        try await call { try await engine.run("remote_admin_unattended_grants", ["device": device]) }
    }
    public func setUnattendedGrant(device: String, user: String, granted: Bool) async throws {
        try await call { try await engine.perform("remote_admin_set_unattended_grant", ["device": device, "user": user, "granted": granted]) }
    }

    /// An engine that does not know the command answers with an "unknown command" failure: that is
    /// "not available". Server answers come as `{"hint": "...", "message": "..."}` text and keep their hint.
    private func call<T>(_ work: () async throws -> T) async throws -> T {
        do { return try await work() } catch let failure as EngineFailure {
            let text = failure.message.lowercased()
            if text.contains("unknown command") || text.contains("unsupported command") || text.contains("no such command") { throw DeviceAdminError.unavailable }
            if let hint = Self.hint(in: failure.message) { throw DeviceAdminError.refused(hint: hint, message: failure.message) }
            throw failure
        }
    }

    static func hint(in message: String) -> String? {
        guard let start = message.firstIndex(of: "{"), let data = String(message[start...]).data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let hint = object["hint"] as? String, !hint.isEmpty else { return nil }
        return hint
    }
}

/// Which version is "the newest": the highest one the company's devices (or this app) report.
public enum AppVersion {
    /// `2.0.19` -> [2, 0, 19]; nil when it is not a dotted number.
    public static func parts(_ text: String?) -> [Int]? {
        guard let text else { return nil }
        let pieces = text.split(separator: ".").map { Int($0.prefix { $0.isNumber }) }
        guard !pieces.isEmpty, !pieces.contains(nil) else { return nil }
        return pieces.compactMap { $0 }
    }

    public static func isOlder(_ version: String?, than newest: String?) -> Bool {
        guard let a = parts(version), let b = parts(newest) else { return false }
        for index in 0..<max(a.count, b.count) {
            let x = index < a.count ? a[index] : 0, y = index < b.count ? b[index] : 0
            if x != y { return x < y }
        }
        return false
    }

    public static func newest(_ versions: [String?]) -> String? {
        var best: String?
        for version in versions.compactMap({ $0 }) where parts(version) != nil {
            if best == nil || isOlder(best, than: version) { best = version }
        }
        return best
    }
}
