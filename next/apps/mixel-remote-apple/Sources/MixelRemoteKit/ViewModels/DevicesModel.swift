import Foundation

/// The devices of the signed-in account: a company's devices (with groups, the Manage menu and the
/// connection service's last record), or the devices a Mixel team member or a personal account may
/// reach. Says for every device whether it can be reached, and why not.
@MainActor
public final class DevicesModel: ObservableObject {
    // The app model lives as long as the app; its parts keep it (no reference cycle matters there).
    let app: AppModel
    @Published public private(set) var devices: [Device] = []
    @Published public private(set) var loading = false
    @Published public var search = ""
    @Published public var groupFilter: String?
    @Published public private(set) var groups: [SaasGroup] = []
    @Published public private(set) var page = 0
    @Published public private(set) var hasNext = false
    @Published public private(set) var message = ""
    @Published public var error = ""
    @Published public private(set) var serverPresence: [UInt32: SaasPresence] = [:]
    /// The deploy link just created (shown once).
    @Published public var deployLink: DeployLinkCreated?
    private var checked: Set<UInt32> = []
    private var cursors: [UInt32?] = [nil]
    private var nextCursor: UInt32?
    private var companyAll: [Device] = []
    private var lastPresence = Date.distantPast
    static let pageSize = 50

    init(app: AppModel) { self.app = app }

    public var tenant: Tenant? { app.company.tenant }
    public var role: String { app.company.overview?.role ?? tenant?.role ?? "" }
    public var manages: Bool { tenant != nil && ["owner", "admin", "technician"].contains(role) }
    public var deploys: Bool { tenant != nil && ["owner", "admin"].contains(role) }
    public var groupsOn: Bool { app.company.overview?.features?["groups"] ?? false }
    public var writable: Bool { ["active", "trial", "past_due"].contains(app.company.overview?.status ?? tenant?.status ?? "") }
    public var room: Int {
        guard let overview = app.company.overview, let limit = overview.limits?.devices, let usage = overview.usage else { return .max }
        return limit - usage.devices
    }
    /// Why adding devices is not possible right now ("" when it is).
    public var addBlocked: String {
        if !writable { return L("This company is read-only") }
        if room <= 0 { return L("Device limit of your plan reached") }
        return ""
    }
    /// This computer is already one of the listed devices.
    public var registeredHere: Bool {
        if let host = app.remote.host, host > 0 { return true }
        return app.enrollment.current != nil
    }

    func reset() {
        devices = []
        companyAll = []
        groups = []
        groupFilter = nil
        page = 0
        hasNext = false
        cursors = [nil]
        nextCursor = nil
        checked = []
        serverPresence = [:]
        message = ""
        error = ""
        deployLink = nil
    }

    public func load(page wanted: Int = 0) async {
        guard app.signedIn else { reset(); return }
        loading = true
        error = ""
        defer { loading = false }
        do {
            if let tenant {
                if wanted == 0 || companyAll.isEmpty {
                    struct Args: Encodable { var tenant: String; var group: String?; var search: String? }
                    let rows: [SaasDevice] = try await app.engine.run("saas_devices", Args(tenant: tenant.tenantId, group: groupFilter, search: search.isEmpty ? nil : search))
                    companyAll = rows.compactMap(\.device)
                    if manages, groupsOn { groups = (try? await app.engine.run("saas_groups", ["tenant": tenant.tenantId])) ?? [] }
                    await loadServerPresence()
                }
                page = max(0, min(wanted, max(0, (companyAll.count - 1) / Self.pageSize)))
                devices = Array(companyAll.dropFirst(page * Self.pageSize).prefix(Self.pageSize))
                hasNext = (page + 1) * Self.pageSize < companyAll.count
            } else {
                if wanted == 0 { cursors = [nil] }
                let cursor = wanted < cursors.count ? cursors[wanted] : nextCursor
                struct Args: Encodable { var after: UInt32?; var search: String }
                let result: DevicePage = try await app.engine.run("remote_devices", Args(after: cursor, search: search))
                devices = result.devices
                page = wanted
                nextCursor = result.next
                hasNext = result.next != nil
                if cursors.count <= wanted + 1 { cursors.append(result.next) } else { cursors[wanted + 1] = result.next }
            }
            message = devices.isEmpty ? "" : L("Page %d · %d devices.", page + 1, devices.count)
            await checkPresence(force: true)
        } catch {
            self.error = AppModel.message(error)
        }
    }

    public func nextPage() { Task { await load(page: page + 1) } }
    public func previousPage() { Task { await load(page: max(0, page - 1)) } }

    private func loadServerPresence() async {
        guard let tenant else { return }
        guard let rows: [SaasPresence] = try? await app.engine.run("saas_device_presence", ["tenant": tenant.tenantId]) else { return }
        serverPresence = Dictionary(rows.compactMap { row in row.connectionId.map { ($0, row) } }, uniquingKeysWith: { first, _ in first })
    }

    /// Asks the connection service which listed devices are online (every 15 s while connected).
    func checkPresence(force: Bool = false) async {
        guard app.remote.connected, !devices.isEmpty, force || Date().timeIntervalSince(lastPresence) > 15 else { return }
        lastPresence = Date()
        let hosts = devices.map(\.connectionId).filter { $0 != app.remote.host }
        guard !hosts.isEmpty else { return }
        try? await app.engine.perform("remote_presence", ["hosts": Array(hosts.prefix(100))])
        // The answer arrives in remote_status.online; the next read shows it.
        try? await Task.sleep(nanoseconds: 600_000_000)
        checked.formUnion(hosts)
    }

    func statusChanged(from previous: RemoteStatus, to next: RemoteStatus) {
        if next.phase == "active", !next.sharing, let peer = next.peer, !next.hostMacs.isEmpty, next.hostMacs != previous.hostMacs || previous.phase != "active" {
            remember(macs: next.hostMacs, of: peer)
        }
        if next.connected, !previous.connected { lastPresence = .distantPast }
        if next.connected { Task { await checkPresence() } }
    }

    // MARK: What a row shows

    public enum Presence: Equatable { case this, online, offline, checking, unknown }

    public func presence(of device: Device) -> Presence {
        let connected = app.remote.connected
        if device.connectionId == app.remote.host { return .this }
        if !connected {
            if let recorded = serverPresence[device.connectionId] { return recorded.status == "online" ? .online : .offline }
            return .unknown
        }
        if app.remote.online.contains(device.connectionId) { return .online }
        return checked.contains(device.connectionId) ? .offline : .checking
    }

    public func label(_ presence: Presence) -> String {
        switch presence {
        case .this: app.remote.connected ? L("This computer") : L("This computer · not connected")
        case .online: L("Online")
        case .offline: L("Offline")
        case .checking: L("Checking…")
        case .unknown: L("Status unknown")
        }
    }

    public func reason(for device: Device) -> String {
        switch presence(of: device) {
        case .this: return app.remote.connected ? L("You are using this device.") : L("Reconnecting automatically.")
        case .offline:
            let seen = serverPresence[device.connectionId].map { Format.lastSeen($0.lastSeen) } ?? ""
            return (seen.isEmpty ? "" : seen + ". ") + L("Turned off, asleep or without internet. It must be online to connect.")
        case .unknown: return L("Shown once this computer is connected.")
        case .online: return L("Ready. The person there accepts your request, unless unattended access is set up.")
        case .checking: return ""
        }
    }

    /// Why Connect is not possible ("" when it is).
    public func connectBlocked(_ device: Device) -> String {
        if device.connectionId == app.remote.host { return L("This is the computer you are using.") }
        if app.remote.inSession { return L("End the current session first.") }
        if presence(of: device) == .offline { return L("This device is offline.") }
        if app.busy { return L("Please wait…") }
        if !app.signedIn { return L("Sign in first.") }
        return ""
    }

    public func connect(_ device: Device) {
        message = L("Connecting to %@…", device.name)
        app.section = .remote
        app.connect(to: device.connectionId)
    }

    // MARK: Manage (company devices)

    public func rename(_ device: Device, to name: String) async -> Bool {
        guard let tenant else { return false }
        return await change { _ = try await self.app.engine.run("saas_rename_device", ["tenant": tenant.tenantId, "device": device.device, "name": name], as: String.self) }
    }

    public func move(_ device: Device, to group: String?) async -> Bool {
        guard let tenant else { return false }
        return await change { try await self.app.engine.perform("saas_move_device", ["tenant": tenant.tenantId, "device": device.device, "group": group]) }
    }

    public func remove(_ device: Device) async -> Bool {
        guard let tenant else { return false }
        return await change { try await self.app.engine.perform("saas_remove_device", ["tenant": tenant.tenantId, "device": device.device]) }
    }

    public func createGroup(_ name: String) async -> Bool {
        guard let tenant else { return false }
        return await change { try await self.app.engine.perform("saas_create_group", ["tenant": tenant.tenantId, "name": name]) }
    }

    /// "Add this computer": this computer's device key becomes one of the company's devices.
    public func addThisComputer(name: String, group: String?) async -> Bool {
        guard let tenant else { return false }
        let wasOnline = !app.remote.disconnected
        let done = await change {
            if wasOnline { try await self.app.engine.perform("remote_disconnect") }
            let enrolled: SaasEnrollment = try await self.app.engine.run("saas_add_this_device", ["tenant": tenant.tenantId, "name": name, "group": group])
            self.app.enrollment.remember(device: enrolled.deviceId, connection: enrolled.connectionId, generation: enrolled.generation, tenant: tenant.tenantId)
        }
        if done { app.goOnline(); await app.company.loadOverview() }
        return done
    }

    /// "Add other computers": a deploy link with the command for each system (shown once).
    public func createDeployLink(label: String, days: Int, computers: Int, group: String?) async -> Bool {
        guard let tenant else { return false }
        return await change {
            self.deployLink = try await self.app.engine.run("saas_create_deploy_link", ["tenant": tenant.tenantId, "label": label, "days": days, "computers": computers, "group": group])
        }
    }

    // MARK: Wake-on-LAN

    /// Network addresses learned from earlier sessions, per Remote ID (as the desktop app keeps them).
    static let macsKey = "mixel.remote.macs.v1"

    public func knownMacs(_ device: Device) -> [String] {
        let known = UserDefaults.standard.dictionary(forKey: Self.macsKey) as? [String: [String]] ?? [:]
        return Array((known[String(device.connectionId)] ?? []).prefix(4))
    }

    /// During a session as technician: remember the other computer's network addresses for Wake.
    func remember(macs: [String], of host: UInt32) {
        let valid = macs.filter { $0.range(of: "^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$", options: .regularExpression) != nil }
        guard !valid.isEmpty else { return }
        var known = UserDefaults.standard.dictionary(forKey: Self.macsKey) as? [String: [String]] ?? [:]
        guard known[String(host)] != Array(valid.prefix(4)) else { return }
        known[String(host)] = Array(valid.prefix(4))
        UserDefaults.standard.set(known, forKey: Self.macsKey)
    }

    public func wake(_ device: Device) {
        let macs = knownMacs(device)
        guard !macs.isEmpty else { error = L("No network address is known for this computer yet"); return }
        wake(device, macs: macs)
    }

    public func wake(_ device: Device, macs: [String]) {
        app.act {
            try await self.app.engine.perform("wake_on_lan", ["macs": macs])
            self.message = L("Wake signal sent to %@. It shows Online when it has started.", device.name)
        }
    }

    private func change(_ work: @escaping () async throws -> Void) async -> Bool {
        error = ""
        do {
            try await work()
            await load(page: 0)
            return true
        } catch {
            self.error = AppModel.message(error)
            return false
        }
    }
}
