import Foundation
#if os(iOS)
import UIKit
#endif

/// Setting up this computer (or phone) once, so it can connect and be reached: add it to the company,
/// enrol it against an existing record (Mixel team), or register it as a personal device.
@MainActor
public final class EnrollmentModel: ObservableObject {
    // The app model lives as long as the app; its parts keep it (no reference cycle matters there).
    let app: AppModel
    /// The engine found no device identity, or the service does not know this device yet.
    @Published public private(set) var needsSetup = false
    @Published public private(set) var current: Registration?
    @Published public var name = ""
    @Published public var error = ""
    @Published public private(set) var working = false
    // Mixel team: enrol against an existing ISM record.
    @Published public private(set) var companies: [EnrollmentCompany] = []
    @Published public private(set) var records: [InventoryDevice] = []
    @Published public var recordSearch = ""

    public struct Registration: Codable, Equatable, Sendable {
        public var device: String
        public var connectionId: UInt32
        public var generation: UInt64
        public var tenant: String?
    }
    public struct EnrollmentCompany: Codable, Equatable, Sendable, Identifiable, Hashable {
        public var clientId: String
        public var clientName: String
        public var id: String { clientId }
    }
    public struct InventoryDevice: Codable, Equatable, Sendable, Identifiable, Hashable {
        public var id: String
        public var name: String
        public var os: String
    }
    struct InventoryPage: Codable { var devices: [InventoryDevice]; var next: String? }
    struct CurrentEnrollment: Codable { var device: String; var connectionId: UInt32; var name: String }

    private static let key = "mixel.remote.this-computer.v1"

    init(app: AppModel) {
        self.app = app
        if let data = UserDefaults.standard.data(forKey: Self.key) {
            current = try? JSONDecoder().decode(Registration.self, from: data)
        }
    }

    /// How this account sets up this computer.
    public enum Path: Equatable { case company, companyMember, team, personal, none }

    public var path: Path {
        guard let account = app.account.account else { return .none }
        if account.staff { return .team }
        if let tenant = app.company.tenant { return ["owner", "admin", "technician"].contains(tenant.role) ? .company : .companyMember }
        if account.personal || account.hasPersonal { return .personal }
        return .none
    }

    func reset() {
        needsSetup = false
        error = ""
        companies = []
        records = []
    }

    /// Reads the state after sign-in: suggested name, the team's enrolment record.
    func check() async {
        if name.isEmpty { name = (try? await app.engine.run("this_computer_name", AnyArgs(["device_name": deviceName()]), as: String.self)) ?? "" }
        if app.account.account?.staff == true {
            if let enrolled = try? await app.engine.run("enrollment_current", as: CurrentEnrollment?.self) ?? nil {
                remember(device: enrolled.device, connection: enrolled.connectionId, generation: current?.generation ?? 0, tenant: nil)
                needsSetup = false
            }
        }
    }

    func deviceName() -> String {
        #if os(iOS)
        return "Mixel Remote (\(UIDevice.current.model))"
        #else
        return Host.current().localizedName ?? ""
        #endif
    }

    func registerFailed(_ error: Error) {
        let text = (error as? EngineFailure)?.message ?? error.localizedDescription
        if text.contains("No saved device identity") || text.contains("not enrolled") || text.contains("No saved controller identity") {
            needsSetup = true
        } else {
            app.error = AppModel.message(error)
        }
    }

    func remember(device: String, connection: UInt32, generation: UInt64, tenant: String?) {
        let value = Registration(device: device, connectionId: connection, generation: generation, tenant: tenant)
        current = value
        needsSetup = false
        if let data = try? JSONEncoder().encode(value) { UserDefaults.standard.set(data, forKey: Self.key) }
    }

    func forget() {
        current = nil
        UserDefaults.standard.removeObject(forKey: Self.key)
    }

    // MARK: Paths

    public func addToCompany(group: String? = nil) {
        let name = self.name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.count <= 80 else { error = L("Enter a computer name of up to 60 characters"); return }
        run { _ = await self.app.devices.addThisComputer(name: name, group: group); if !self.app.devices.error.isEmpty { throw EngineFailure(self.app.devices.error) } }
    }

    public func registerPersonal() {
        let name = self.name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { error = L("Enter a device name"); return }
        run {
            if !self.app.remote.disconnected { try await self.app.engine.perform("remote_disconnect") }
            let id: UInt32 = try await self.app.engine.run("register_personal_device", ["name": name])
            self.remember(device: "", connection: id, generation: 0, tenant: nil)
            self.app.goOnline()
        }
    }

    public func loadTeamRecords() {
        run {
            self.companies = try await self.app.engine.run("enrollment_companies")
            struct Args: Encodable { var after: String?; var search: String }
            let page: InventoryPage = try await self.app.engine.run("enrollment_devices", Args(after: nil, search: self.recordSearch))
            self.records = page.devices
        }
    }

    /// Mixel team: enrol this computer against an existing record.
    public func enroll(record: InventoryDevice) {
        run {
            if !self.app.remote.disconnected { try await self.app.engine.perform("remote_disconnect") }
            let id: UInt32 = try await self.app.engine.run("enroll_this_device", ["device": record.id, "confirmed": true])
            self.remember(device: record.id, connection: id, generation: 0, tenant: nil)
            self.app.goOnline()
        }
    }

    /// Mixel team: add a new record for this computer in a company, then enrol it.
    public func addAndEnroll(company: String) {
        let name = self.name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { error = L("Enter a device name"); return }
        run {
            if !self.app.remote.disconnected { try await self.app.engine.perform("remote_disconnect") }
            let device: String = try await self.app.engine.run("add_this_device", ["company": company, "name": name])
            let id: UInt32 = try await self.app.engine.run("enroll_this_device", ["device": device, "confirmed": true])
            self.remember(device: device, connection: id, generation: 0, tenant: nil)
            self.app.goOnline()
        }
    }

    // MARK: Rollout (Mixel team): a company token and the command for many computers

    public struct RolloutToken: Codable, Equatable, Sendable, Identifiable {
        public var id: String
        public var label: String
        public var expiresAt: String
        public var maxUses: Int
        public var uses: Int
        public var revokedAt: String?
    }
    @Published public private(set) var rollouts: [RolloutToken] = []
    @Published public var rolloutCreated: DeployLinkCreated?

    public func loadRollouts(company: String) {
        guard !company.isEmpty else { rollouts = []; return }
        run { self.rollouts = try await self.app.engine.run("rollout_list", ["company": company]) }
    }

    public func createRollout(company: String, label: String, days: Int, computers: Int) {
        let label = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !company.isEmpty, !label.isEmpty else { error = L("Choose a company and enter a name"); return }
        run {
            self.rolloutCreated = try await self.app.engine.run("rollout_create", ["company": company, "label": label, "days": days, "computers": computers])
            self.rollouts = try await self.app.engine.run("rollout_list", ["company": company])
        }
    }

    public func revokeRollout(_ token: RolloutToken, company: String) {
        run {
            try await self.app.engine.perform("rollout_revoke", ["id": token.id])
            self.rollouts = try await self.app.engine.run("rollout_list", ["company": company])
        }
    }

    /// Removes this computer from Mixel Remote (signed with its own key; queued while offline).
    public func removeThisComputer() async -> SelfRemovalStatus? {
        error = ""
        working = true
        defer { working = false }
        do {
            app.wentOffline = true
            if !app.remote.disconnected { try await app.engine.perform("remote_disconnect") }
            struct Ref: Encodable { var device: String; var connectionId: UInt32; var generation: UInt64 }
            struct Args: Encodable { var registration: Ref?; var confirmed: Bool }
            let saved = current.flatMap { $0.device.isEmpty || $0.generation == 0 ? nil : Ref(device: $0.device, connectionId: $0.connectionId, generation: $0.generation) }
            let status: SelfRemovalStatus = try await app.engine.run("device_self_remove", Args(registration: saved, confirmed: true))
            if status != .stale { forget(); needsSetup = true }
            app.wentOffline = false
            await app.devices.load()
            return status
        } catch {
            app.wentOffline = false
            self.error = AppModel.message(error)
            return nil
        }
    }

    private func run(_ work: @escaping () async throws -> Void) {
        guard !working else { return }
        working = true
        error = ""
        Task {
            do { try await work() } catch { self.error = AppModel.message(error) }
            self.working = false
        }
    }
}
