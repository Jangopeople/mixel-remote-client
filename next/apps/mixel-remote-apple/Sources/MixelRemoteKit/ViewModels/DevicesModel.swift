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
    /// A short confirmation (the wake signal was sent).
    @Published public var notice = ""
    @Published public private(set) var serverPresence: [UInt32: SaasPresence] = [:]
    /// The deploy link just created (shown once).
    @Published public var deployLink: DeployLinkCreated?
    /// Favorites (device ids) and the devices of the person's own recent sessions, newest first.
    @Published public private(set) var favorites: Set<String> = []
    @Published public private(set) var recent: [String] = []
    private var checked: Set<UInt32> = []
    private var cursors: [UInt32?] = [nil]
    private var nextCursor: UInt32?
    private var companyAll: [Device] = []
    private var lastPresence = Date.distantPast
    static let pageSize = 50

    init(app: AppModel) { self.app = app }

    // MARK: Device administration (docs/mixel-remote-device-admin-api.md)

    /// The administration commands. The engine has none yet, so this answers "not available" until the
    /// engine's `remote_admin_*` commands are wired (`EngineDeviceAdminService`).
    public var admin: DeviceAdminService = UnavailableDeviceAdminService()
    /// What the administration API reports per device (note, unattended access, version, last seen), by Remote ID.
    @Published public private(set) var adminRows: [UInt32: AdminDevice] = [:]
    /// Only the devices with an older version than the newest one in the company ("Update" filter).
    @Published public var updateOnly = false

    public var adminAvailable: Bool { admin.available }
    public func adminRow(_ device: Device) -> AdminDevice? { adminRows[device.connectionId] }
    public func note(of device: Device) -> String { adminRow(device)?.note?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
    /// Versions are known: show the Version column and the "Update" filter.
    public var versionsKnown: Bool { adminRows.values.contains { $0.appVersion != nil } }
    /// The newest app version any of the company's devices reports.
    public var newestVersion: String? { AppVersion.newest(adminRows.values.map(\.appVersion)) }
    public func isOutdated(_ device: Device) -> Bool { AppVersion.isOlder(adminRow(device)?.appVersion, than: newestVersion) }
    public var outdatedCount: Int { allDevices.filter(isOutdated).count }
    /// Whether this person may change the device (the server decides; `nil` answers fall back to the role).
    public func canAdmin(_ device: Device) -> Bool { adminRow(device)?.canAdmin ?? manages }

    func loadAdmin() async {
        guard admin.available, app.signedIn else { adminRows = [:]; return }
        do {
            // `company` of the API is the ISM client id, not the tenant id; every row is matched by its 9-digit ID anyway.
            let rows = try await admin.devices(company: nil, search: nil)
            adminRows = Dictionary(rows.map { ($0.connectionId, $0) }, uniquingKeysWith: { first, _ in first })
        } catch DeviceAdminError.unavailable {
            adminRows = [:]
        } catch {
            adminRows = [:]
            self.error = AppModel.message(error)
        }
    }

    public func setNote(_ device: Device, to note: String) async -> Bool {
        let text = note.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count <= 500 else { error = L("A note has at most 500 characters."); return false }
        return await adminChange { _ = try await self.admin.setNote(device: device.device, note: text.isEmpty ? nil : text) }
    }

    /// Switches unattended access for the device: `on`, or `default` (the company policy decides).
    public func setUnattended(_ device: Device, on: Bool) async -> Bool {
        await adminChange { _ = try await self.admin.setUnattended(device: device.device, setting: on ? "on" : "off") }
    }

    private func adminChange(_ work: @escaping () async throws -> Void) async -> Bool {
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
        favorites = []
        recent = []
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
        adminRows = [:]
        updateOnly = false
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
                    await loadFavorites()
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
            await loadAdmin()
            message = devices.isEmpty ? "" : L("Page %d · %d devices.", page + 1, devices.count)
            await checkPresence(force: true)
        } catch {
            self.error = AppModel.message(error)
        }
    }

    // MARK: The console's views, filters and groups

    /// The view chosen in the sidebar.
    public enum ListView: Hashable, Sendable {
        case all, online, favorites, recent
        case group(String)
    }

    @Published public var listView: ListView = .all
    /// Quick filters above the table.
    @Published public var onlineOnly = false
    @Published public var systems: Set<String> = []
    /// Typed into the console's search field: name, ID or group (filters at once, no round trip).
    @Published public var filterText = ""

    /// A group of rows in the table.
    public struct RowGroup: Identifiable, Equatable {
        public var id: String
        public var name: String
        public var devices: [Device]
        public var online: Int
    }

    /// Online, or this computer.
    public func isOnline(_ device: Device) -> Bool {
        let presence = presence(of: device)
        return presence == .online || presence == .this
    }

    public func devices(in view: ListView) -> [Device] {
        let all = allDevices
        switch view {
        case .all: return all
        case .online: return all.filter { isOnline($0) || app.workspace.state(of: $0.connectionId) != nil }
        case .favorites: return all.filter { favorites.contains($0.device) }
        case .recent: return recent.compactMap { id in all.first { $0.device == id } }
        case .group(let name): return all.filter { ($0.groupName ?? "") == name }
        }
    }

    /// The groups of the listed devices, by name ("" = no group), with their counts.
    public var groupNames: [String] {
        Array(Set(allDevices.compactMap(\.groupName))).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    public var filtered: [Device] {
        let text = filterText.trimmingCharacters(in: .whitespaces).lowercased()
        let digits = text.filter(\.isASCIIDigit)
        return devices(in: listView).filter { device in
            if onlineOnly, !isOnline(device), app.workspace.state(of: device.connectionId) == nil { return false }
            if !systems.isEmpty, !systems.contains(device.os.lowercased()) { return false }
            if updateOnly, !isOutdated(device) { return false }
            if text.isEmpty { return true }
            if note(of: device).lowercased().contains(text) { return true }
            if device.name.lowercased().contains(text) { return true }
            if let group = device.groupName, group.lowercased().contains(text) { return true }
            if !digits.isEmpty, digits.count == text.filter({ !$0.isWhitespace }).count, String(device.connectionId).contains(digits) { return true }
            return false
        }
    }

    /// Rank for "sorted by status": in session, online, checking, unknown, offline.
    func statusRank(_ device: Device) -> Int {
        if app.workspace.state(of: device.connectionId) != nil { return 0 }
        switch presence(of: device) {
        case .online: return 1
        case .this: return 2
        case .checking: return 3
        case .unknown: return 4
        case .offline: return 5
        }
    }

    /// The table: grouped by group (no group last), each sorted by status, then name.
    public var rowGroups: [RowGroup] {
        let rows = filtered
        let byGroup = Dictionary(grouping: rows) { $0.groupName ?? "" }
        let names = byGroup.keys.sorted { a, b in
            if a.isEmpty != b.isEmpty { return !a.isEmpty }
            return a.localizedStandardCompare(b) == .orderedAscending
        }
        return names.map { name in
            let sorted = (byGroup[name] ?? []).sorted { a, b in
                let ra = statusRank(a), rb = statusRank(b)
                if ra != rb { return ra < rb }
                return a.name.localizedStandardCompare(b.name) == .orderedAscending
            }
            let title = name.isEmpty ? (names.count == 1 ? L("All devices") : L("No group")) : name
            return RowGroup(id: name.isEmpty ? "-" : name, name: title, devices: sorted, online: sorted.filter(isOnline).count)
        }
    }

    // MARK: Favorites and recent connections (company devices)

    struct FavoriteRow: Codable { var deviceId: String }
    struct RecentRow: Codable { var deviceId: String; var lastConnectedAt: String }

    func loadFavorites() async {
        guard let tenant else { favorites = []; recent = []; return }
        if let rows: [FavoriteRow] = try? await app.engine.run("saas_favorites", ["tenant": tenant.tenantId]) { favorites = Set(rows.map(\.deviceId)) }
        if let rows: [RecentRow] = try? await app.engine.run("saas_recent_devices", ["tenant": tenant.tenantId, "max_count": 10]) { recent = rows.map(\.deviceId) }
    }

    /// Shown above all devices on the first page without a search: favorites, then recent ones.
    public var favoriteDevices: [Device] {
        guard tenant != nil, page == 0, search.isEmpty else { return [] }
        return companyAll.filter { favorites.contains($0.device) }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
    public var recentDevices: [Device] {
        guard tenant != nil, page == 0, search.isEmpty else { return [] }
        return recent.filter { !favorites.contains($0) }.compactMap { id in companyAll.first { $0.device == id } }.prefix(5).map { $0 }
    }

    public func isFavorite(_ device: Device) -> Bool { favorites.contains(device.device) }

    public func toggleFavorite(_ device: Device) {
        guard let tenant else { return }
        let next = !favorites.contains(device.device)
        if next { favorites.insert(device.device) } else { favorites.remove(device.device) }
        Task {
            do { try await app.engine.perform("saas_set_favorite", ["tenant": tenant.tenantId, "device": device.device, "favorite": next]) }
            catch {
                if next { favorites.remove(device.device) } else { favorites.insert(device.device) }
                self.error = AppModel.message(error)
            }
        }
    }

    /// The device's name, or its Remote ID.
    public func name(of connection: UInt32) -> String {
        (companyAll + devices).first { $0.connectionId == connection }?.name ?? Format.remoteId(connection)
    }

    /// The device's operating system ("" when it is not in the list).
    public func os(of connection: UInt32) -> String {
        (companyAll + devices).first { $0.connectionId == connection }?.os ?? ""
    }

    /// Every listed device: all of the company's devices, or this page of the directory.
    public var allDevices: [Device] { tenant != nil ? companyAll : devices }

    /// The time the connection service last saw the device ("" when unknown).
    public func lastSeenDate(_ device: Device) -> Date? {
        serverPresence[device.connectionId]?.lastSeen.flatMap(Format.parseDate)
    }

    /// One of the company's devices (any page).
    func isCompanyHost(_ connection: UInt32) -> Bool { companyAll.contains { $0.connectionId == connection } }

    public func nextPage() { Task { await load(page: page + 1) } }
    public func previousPage() { Task { await load(page: max(0, page - 1)) } }

    private func loadServerPresence() async {
        guard let tenant else { return }
        guard let rows: [SaasPresence] = try? await app.engine.run("saas_device_presence", ["tenant": tenant.tenantId]) else { return }
        serverPresence = Dictionary(rows.compactMap { row in row.connectionId.map { ($0, row) } }, uniquingKeysWith: { first, _ in first })
    }

    /// Asks the connection service which listed devices are online (every 15 s while connected).
    func checkPresence(force: Bool = false) async {
        guard app.remote.connected, !allDevices.isEmpty, force || Date().timeIntervalSince(lastPresence) > 15 else { return }
        lastPresence = Date()
        let hosts = allDevices.map(\.connectionId).filter { $0 != app.remote.host }
        guard !hosts.isEmpty else { return }
        for start in stride(from: 0, to: min(hosts.count, 2000), by: 100) {
            try? await app.engine.perform("remote_presence", ["hosts": Array(hosts[start..<min(hosts.count, start + 100)])])
        }
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
        if app.remote.inSession {
            // Several sessions at once need the engine's multi-session support (one today).
            return L("End the current session first.")
        }
        if presence(of: device) == .offline { return L("This device is offline.") }
        if app.busy { return L("Please wait…") }
        if !app.signedIn { return L("Sign in first.") }
        return ""
    }

    public func connect(_ device: Device, unattended: Bool = false) {
        guard connectBlocked(device).isEmpty else { return }
        message = L("Connecting to %@…", device.name)
        app.connect(to: device.connectionId, unattended: unattended, checkPhone: false)
    }

    // MARK: Manage (company devices)

    struct StaffSettings: Decodable {
        let name: String
        let updatedAt: String
        let currentCompany: String
        let companies: [EnrollmentModel.EnrollmentCompany]
    }

    var managesIsmDevices: Bool { app.signedIn && app.account.account?.staff == true && tenant == nil }

    func staffSettings(for device: Device) async throws -> StaffSettings {
        let account = app.account.account?.profile
        guard managesIsmDevices else { throw EngineFailure("Sign in first.") }
        let settings: StaffSettings = try await app.engine.run("staff_device_settings", ["device": device.device])
        guard managesIsmDevices, app.account.account?.profile == account else { throw EngineFailure("The enrollment account changed") }
        return settings
    }

    func saveStaffSettings(_ device: Device, settings: StaffSettings, name: String, company: String?) async -> Bool {
        guard managesIsmDevices else { error = L("Sign in first."); return false }
        return await change {
            let _: StaffSettings = try await self.app.engine.run("staff_device_settings", [
                "device": device.device, "expected_update": settings.updatedAt, "name": name, "company": company,
            ])
        }
    }

    public func rename(_ device: Device, to name: String) async -> Bool {
        if admin.available {
            let expected = adminRow(device)?.updatedAt
            return await adminChange { _ = try await self.admin.rename(device: device.device, name: name, expectedUpdate: expected) }
        }
        guard let tenant else { return false }
        return await change { _ = try await self.app.engine.run("saas_rename_device", ["tenant": tenant.tenantId, "device": device.device, "name": name], as: String.self) }
    }

    public func move(_ device: Device, to group: String?) async -> Bool {
        guard let tenant else { return false }
        return await change { try await self.app.engine.perform("saas_move_device", ["tenant": tenant.tenantId, "device": device.device, "group": group]) }
    }

    public func remove(_ device: Device) async -> Bool {
        if admin.available {
            return await adminChange { try await self.admin.remove(device: device.device, confirmConnectionId: device.connectionId) }
        }
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
            self.notice = self.message
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
