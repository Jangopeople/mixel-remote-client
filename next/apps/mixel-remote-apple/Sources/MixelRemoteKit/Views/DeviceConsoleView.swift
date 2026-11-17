#if os(macOS)
import SwiftUI

/// Devices in the "Pro Console" look: the header with Connect by ID, search and quick filters, and a
/// dense table grouped by group (favourite, system, name and note, ID, status, last seen, Connect,
/// row menu). Connecting shows its progress on the row; the person stays here.
public struct DeviceConsoleView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var model: DevicesModel
    @ObservedObject var workspace: WorkspaceModel
    @State private var renaming: Device?
    @State private var editing: Device?
    @State private var moving: Device?
    @State private var removing: Device?
    @State private var noting: Device?
    @State private var addingOthers = false
    @State private var addingThis = false
    @State private var byId = ""
    @FocusState private var focus: Field?

    enum Field { case search, byId }

    public init(app: AppModel) {
        self.app = app
        model = app.devices
        workspace = app.workspace
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header.padding(.horizontal, 28).padding(.top, 22).padding(.bottom, 14)
            notices.padding(.horizontal, 28)
            table
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.page)
        .onReceive(NotificationCenter.default.publisher(for: .mixelFocusConnectById)) { _ in focus = .byId }
        .background {
            // "/" puts the cursor into the search field, as in the mockup.
            Button("") { focus = .search }.keyboardShortcut("/", modifiers: []).hidden().accessibilityHidden(true)
        }
        .sheet(item: $renaming) { device in RenameDeviceDialog(model: model, device: device, company: model.tenant?.name ?? "") }
        .sheet(item: $noting) { device in DeviceNotesDialog(model: model, device: device) }
        .sheet(item: $removing) { device in RemoveDeviceDialog(model: model, device: device, inSession: workspace.state(of: device.connectionId) != nil) }
        .sheet(item: $moving) { device in MoveSheet(model: model, device: device) }
        .sheet(item: $editing) { device in StaffDeviceSheet(model: model, device: device) }
        .sheet(isPresented: $addingOthers) { DeployLinkSheet(model: model) }
        .sheet(isPresented: $addingThis) {
            AddThisComputerSheet(app: app)
        }
    }

    // MARK: Header

    private var title: String {
        switch model.listView {
        case .all: L("All devices")
        case .online: L("Online now")
        case .favorites: L("Favourites")
        case .recent: L("Recent")
        case .group(let name): name
        }
    }

    private var summary: String {
        guard app.signedIn else { return L("Sign in to see the devices available to your account.") }
        let shown = model.filtered
        let total = model.devices(in: model.listView).count
        let online = shown.filter(model.isOnline).count
        return L("%d of %d devices · %d online", shown.count, total, online)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: title).font(.system(size: 26, weight: .bold)).accessibilityAddTraits(.isHeader)
                    Text(verbatim: summary).foregroundStyle(.secondary)
                }
                Spacer()
                connectById
            }
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField(text: $model.filterText, prompt: Text(verbatim: L("Search name, ID or group"))) { Text(verbatim: L("Search")) }
                        .textFieldStyle(.plain)
                        .focused($focus, equals: .search)
                        .onSubmit {
                            // The directory of a team member is paged on the server: search there too.
                            if model.tenant == nil { model.search = model.filterText; Task { await model.load() } }
                        }
                    Text(verbatim: "/").font(.caption.monospaced()).foregroundStyle(.secondary)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .overlay(RoundedRectangle(cornerRadius: 4).stroke(Theme.hairline))
                        .accessibilityHidden(true)
                }
                .padding(.horizontal, 10).padding(.vertical, 7)
                .frame(maxWidth: 340)
                .background(Theme.page, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(focus == .search ? Theme.brand : Theme.hairline, lineWidth: 1))
                Chip(L("Online"), dot: Theme.success, on: model.onlineOnly) { model.onlineOnly.toggle() }
                Rectangle().fill(Theme.hairline).frame(width: 1, height: 22).padding(.horizontal, 4)
                ForEach([("macos", "macOS"), ("windows", "Windows"), ("linux", "Linux")], id: \.0) { system in
                    Chip(system.1, on: model.systems.contains(system.0)) {
                        if model.systems.contains(system.0) { model.systems.remove(system.0) } else { model.systems.insert(system.0) }
                    }
                }
                if model.versionsKnown {
                    Chip(L("Update"), dot: Theme.updateText, on: model.updateOnly) { model.updateOnly.toggle() }
                }
                Spacer()
                if model.tenant != nil, app.signedIn {
                    if model.manages, !model.registeredHere {
                        Button(L("Add this computer")) { addingThis = true }.disabled(!model.addBlocked.isEmpty).help(model.addBlocked)
                    }
                    if model.deploys {
                        Button(L("Add other computers")) { addingOthers = true }.disabled(!model.addBlocked.isEmpty).help(model.addBlocked)
                    }
                }
                Button { Task { await model.load() } } label: { Image(systemName: "arrow.clockwise") }
                    .help(L("Refresh devices"))
                    .accessibilityLabel(Text(verbatim: L("Refresh devices")))
                    .disabled(!app.signedIn || model.loading)
                    .keyboardShortcut("r", modifiers: .command)
                Text(verbatim: L("Sorted by status")).font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    private var connectById: some View {
        HStack(spacing: 8) {
            Text(verbatim: L("Connect by ID")).font(.callout).foregroundStyle(.secondary).padding(.leading, 12)
            TextField(text: $byId, prompt: Text(verbatim: "000 000 000")) { Text(verbatim: L("Connect by ID")) }
                .textFieldStyle(.plain)
                .font(.system(size: 15, design: .monospaced))
                .frame(width: 118)
                .focused($focus, equals: .byId)
                .onChange(of: byId) { value in
                    let digits = String(value.filter(\.isASCIIDigit).prefix(9))
                    var grouped = ""
                    for (index, digit) in digits.enumerated() {
                        if index > 0, index % 3 == 0 { grouped.append(" ") }
                        grouped.append(digit)
                    }
                    if grouped != value { byId = grouped }
                }
                .onSubmit(connectTyped)
            Button(action: connectTyped) {
                Label(L("Connect"), systemImage: "bolt.fill").font(.system(size: 13, weight: .semibold))
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(!app.signedIn || Format.parseRemoteId(byId) == nil || app.remote.connectBlockedBySession || workspace.attempt?.failure.isEmpty == true)
        }
        .padding(4)
        .background(Theme.page, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(focus == .byId ? Theme.brand : Theme.hairline, lineWidth: 1))
    }

    private func connectTyped() {
        guard let id = Format.parseRemoteId(byId) else { app.error = L("Enter a nine-digit connection ID"); return }
        byId = ""
        let known = model.allDevices.first { $0.connectionId == id }
        workspace.connect(peer: id, name: known?.name, os: known?.os ?? "", unattended: true, checkPhone: known == nil)
    }

    // MARK: Notices

    @ViewBuilder private var notices: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !model.addBlocked.isEmpty, model.manages {
                MessageLine(model.writable ? L("Device limit of your plan reached. Remove a device or upgrade the plan to add more.")
                                           : L("This company is read-only. You can still remove devices; adding devices and other changes need an active plan."), error: false)
            }
            if app.signedIn, !app.remote.connected, app.remote.phase != "closing", !app.enrollment.needsSetup { connectionBanner }
            MessageLine(model.error)
            if workspace.attempt == nil { MessageLine(app.error) }
            MessageLine(model.notice, error: false)
        }
        .padding(.bottom, 6)
    }

    private var connectionBanner: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "wifi.exclamationmark").foregroundStyle(Theme.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: app.remote.phase == "error" ? L("This computer lost its connection") : L("This computer is not connected yet")).font(.callout.weight(.semibold))
                Text(verbatim: app.remote.message.isEmpty
                     ? L("Mixel Remote reconnects by itself. Device status appears once it is connected; you can still choose Connect.")
                     : Translator.shared.translate(app.remote.message)).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            Button(L("Reconnect now")) { app.goOnline() }
        }
        .padding(12)
        .background(Theme.warning.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: Table

    @ViewBuilder private var table: some View {
        let groups = model.rowGroups
        if groups.isEmpty {
            VStack(spacing: 10) {
                Spacer()
                Image(systemName: "laptopcomputer.and.iphone").font(.system(size: 40)).foregroundStyle(.secondary)
                Text(verbatim: emptyTitle).font(.title3.weight(.semibold))
                Text(verbatim: emptyDetail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
                if !app.signedIn { Button(L("Sign in to Mixel →")) { app.section = .account }.linkButton() }
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(24)
        } else {
            VStack(spacing: 0) {
                ColumnHeader(versions: model.versionsKnown)
                ScrollView {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        ForEach(groups) { group in
                            Section {
                                ForEach(group.devices) { device in
                                    DeviceTableRow(app: app, model: model, workspace: workspace, device: device,
                                                   rename: { renaming = $0 }, move: { moving = $0 }, remove: { removing = $0 }, edit: { editing = $0 }, notes: { noting = $0 })
                                }
                            } header: {
                                GroupHeader(name: group.name, color: group.id == "-" ? Color.secondary : Theme.groupColor(group.name), count: group.devices.count, online: group.online)
                            }
                        }
                        if model.tenant == nil, model.page > 0 || model.hasNext {
                            HStack {
                                Button(L("Previous")) { model.previousPage() }.disabled(model.page == 0 || model.loading)
                                Spacer()
                                Text(verbatim: model.message).font(.callout).foregroundStyle(.secondary)
                                Spacer()
                                Button(L("Next")) { model.nextPage() }.disabled(!model.hasNext || model.loading)
                            }
                            .padding(16)
                        }
                    }
                    .padding(.bottom, 120)
                }
            }
            .padding(.horizontal, 18)
        }
    }

    private var emptyTitle: String {
        if model.loading { return L("Loading your devices…") }
        if !app.signedIn { return L("Your devices belong here") }
        if !model.filterText.isEmpty || model.onlineOnly || model.updateOnly || !model.systems.isEmpty { return L("No matching devices") }
        return L("No devices to display")
    }

    private var emptyDetail: String {
        if !app.signedIn { return L("Sign in to see the devices available to your account.") }
        if !model.filterText.isEmpty || model.onlineOnly || model.updateOnly || !model.systems.isEmpty { return L("Try another device name or clear the search.") }
        if model.tenant != nil {
            return model.manages ? L("Add this computer, or add other computers with a deploy link.") : L("Devices you add appear here. Ask an administrator of your company for access to other devices.")
        }
        return L("Devices appear here after a Mixel team member enrolls them.")
    }
}

/// A quick filter.
struct Chip: View {
    let title: String
    let dot: Color?
    let on: Bool
    let action: () -> Void
    init(_ title: String, dot: Color? = nil, on: Bool, action: @escaping () -> Void) {
        self.title = title
        self.dot = dot
        self.on = on
        self.action = action
    }
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let dot { Circle().fill(dot).frame(width: 7, height: 7) }
                Text(verbatim: title)
            }
            .font(.system(size: 13, weight: on ? .semibold : .regular))
            .foregroundStyle(on ? Theme.brand : Color.primary)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(on ? Theme.brandSoft : Theme.page, in: Capsule())
            .overlay(Capsule().stroke(on ? Theme.brand.opacity(0.5) : Theme.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(verbatim: title))
        .accessibilityValue(Text(verbatim: on ? L("on") : L("off")))
    }
}

/// The table's columns (shared widths).
enum Columns {
    static let star: CGFloat = 24
    static let tile: CGFloat = 40
    static let id: CGFloat = 104
    static let status: CGFloat = 150
    static let seen: CGFloat = 84
    static let version: CGFloat = 112
    static let actions: CGFloat = 112
}

struct ColumnHeader: View {
    var versions = false
    var body: some View {
        HStack(spacing: 8) {
            Color.clear.frame(width: Columns.star + Columns.tile, height: 1)
            label(L("Device")).frame(maxWidth: .infinity, alignment: .leading)
            label(L("ID")).frame(width: Columns.id, alignment: .leading)
            label(L("Status")).frame(width: Columns.status, alignment: .leading)
            label(L("Last seen")).frame(width: Columns.seen, alignment: .leading)
            if versions { label(L("Version")).frame(width: Columns.version, alignment: .leading) }
            label(L("Actions")).frame(width: Columns.actions, alignment: .trailing)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.hairline).frame(height: 1) }
    }
    func label(_ text: String) -> some View {
        Text(verbatim: text.uppercased()).font(.system(size: 11, weight: .semibold)).tracking(0.8).foregroundStyle(.secondary)
    }
}

struct GroupHeader: View {
    let name: String
    let color: Color
    let count: Int
    let online: Int
    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(verbatim: name).font(.system(size: 13, weight: .semibold))
            Text(verbatim: L("%d devices · %d online", count, online)).font(.system(size: 13)).foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(Theme.groupRow)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.hairline).frame(height: 1) }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

struct DeviceTableRow: View {
    @ObservedObject var app: AppModel
    @ObservedObject var model: DevicesModel
    @ObservedObject var workspace: WorkspaceModel
    let device: Device
    let rename: (Device) -> Void
    let move: (Device) -> Void
    let remove: (Device) -> Void
    let edit: (Device) -> Void
    let notes: (Device) -> Void
    @State private var hovering = false

    var body: some View {
        let presence = model.presence(of: device)
        let state = workspace.state(of: device.connectionId)
        let attempt = workspace.attempt?.peer == device.connectionId ? workspace.attempt : nil
        HStack(spacing: 8) {
            star.frame(width: Columns.star)
            OSTile(os: device.os, dot: dot(presence, state)).frame(width: Columns.tile, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: device.name).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Text(verbatim: subline(presence)).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(verbatim: Format.remoteId(device.connectionId)).font(.system(size: 13, design: .monospaced))
                .textSelection(.enabled)
                .frame(width: Columns.id, alignment: .leading)
                .accessibilityLabel(Text(verbatim: L("Connection ID") + " " + Format.remoteId(device.connectionId)))
            status(presence, state, attempt).frame(width: Columns.status, alignment: .leading)
            Text(verbatim: lastSeen(presence, state)).font(.system(size: 13)).foregroundStyle(.secondary).frame(width: Columns.seen, alignment: .leading)
            if model.versionsKnown { version.frame(width: Columns.version, alignment: .leading) }
            actions(presence, state).frame(width: Columns.actions, alignment: .trailing)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(state == "ready" || state == "starting" || state == "reconnecting" || state == "reconnect_failed" ? Theme.activeRow : hovering ? Theme.hoverRow : Theme.page)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.hairline.opacity(0.7)).frame(height: 1) }
        .onHover { hovering = $0 }
        .contextMenu { menuItems(presence) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(verbatim: "\(device.name), \(Format.os(device.os)), \(model.label(presence))"))
    }

    @ViewBuilder private var star: some View {
        if model.tenant != nil {
            let on = model.isFavorite(device)
            Button { model.toggleFavorite(device) } label: {
                Image(systemName: on ? "star.fill" : "star").foregroundStyle(on ? Color(light: 0xD99A00, dark: 0xF5C24A) : .secondary)
            }
            .buttonStyle(.plain)
            .help(on ? L("Remove from favorites") : L("Add to favorites"))
            .accessibilityLabel(Text(verbatim: on ? L("Remove from favorites") : L("Add to favorites")))
        } else {
            Color.clear.frame(height: 1)
        }
    }

    private func dot(_ presence: DevicesModel.Presence, _ state: String?) -> Color? {
        if state != nil { return Theme.warning }
        switch presence {
        case .online, .this: return Theme.success
        case .offline: return Color.secondary
        default: return nil
        }
    }

    private func subline(_ presence: DevicesModel.Presence) -> String {
        var parts = [Format.os(device.os)]
        if model.listView == .all || model.listView == .online || model.listView == .favorites || model.listView == .recent, let group = device.groupName { parts.append(group) }
        if presence == .this { parts.append(L("This computer")) }
        let note = model.note(of: device)
        if !note.isEmpty { parts.append(note) }
        return parts.joined(separator: " · ")
    }

    private func lastSeen(_ presence: DevicesModel.Presence, _ state: String?) -> String {
        if state != nil || presence == .online || presence == .this { return L("Now") }
        guard let date = model.lastSeenDate(device) else { return "—" }
        let minutes = max(0, Int(Date().timeIntervalSince(date) / 60))
        if minutes < 2 { return L("Just now") }
        if minutes < 60 { return L("%d min ago", minutes) }
        let hours = minutes / 60
        if hours < 24 { return L("%d h ago", hours) }
        let days = hours / 24
        return days == 1 ? L("Yesterday") : L("%d days ago", days)
    }

    /// "2.0.19" and an "Update" pill when the company has a newer version; "—" where the device does not report one.
    @ViewBuilder private var version: some View {
        if let value = model.adminRow(device)?.appVersion {
            HStack(spacing: 6) {
                Text(verbatim: value).font(.system(size: 13)).monospacedDigit()
                if model.isOutdated(device) { Pill(L("Update"), foreground: Theme.updateText, background: Theme.updateSoft) }
            }
        } else {
            Text(verbatim: "—").foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func status(_ presence: DevicesModel.Presence, _ state: String?, _ attempt: WorkspaceModel.Attempt?) -> some View {
        if let attempt, attempt.failure.isEmpty {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small).scaleEffect(0.7).frame(width: 14, height: 14)
                    Text(verbatim: ConnectSteps.title(attempt.step, unattended: attempt.unattended) + "…").font(.system(size: 13, weight: .medium)).lineLimit(1)
                }
                ProgressView(value: Double(attempt.step + 1), total: 5).tint(Theme.brand).frame(maxWidth: 200)
            }
            .accessibilityElement(children: .combine)
        } else if state == "ready" || state == "starting" || state == "ending" || state == "reconnecting" || state == "reconnect_failed" {
            Pill(state == "starting" ? L("Starting video") : state == "reconnecting" ? L("Reconnecting…") : state == "reconnect_failed" ? L("Could not reconnect") : L("In session"),
                 symbol: "macwindow", foreground: Theme.sessionText, background: Theme.sessionSoft)
        } else {
            HStack(spacing: 6) {
                StatusBadge(badge(presence), model.label(presence))
                if model.adminRow(device)?.unattendedOn == true {
                    Pill(L("Unattended"), symbol: "key.fill", foreground: Theme.infoText, background: Theme.infoSoft).help(L("Unattended access is on"))
                }
            }
            .help(model.reason(for: device))
        }
    }

    func badge(_ presence: DevicesModel.Presence) -> StatusBadge.Kind {
        switch presence {
        case .online, .this: .online
        case .offline: .offline
        case .checking: .busy
        case .unknown: .unknown
        }
    }

    @ViewBuilder private func actions(_ presence: DevicesModel.Presence, _ state: String?) -> some View {
        HStack(spacing: 6) {
            if state == "ready" || state == "starting" || state == "reconnecting" || state == "reconnect_failed" {
                Button { workspace.show(device.connectionId) } label: { Label(L("Show"), systemImage: "macwindow") }
                    .help(L("Show the session with %@", device.name))
            } else {
                let blocked = model.connectBlocked(device)
                let connecting = state == "connecting"
                Button(connecting ? L("Connecting…") : L("Connect")) {
                    workspace.connect(peer: device.connectionId, name: device.name, os: device.os, unattended: true, checkPhone: false)
                }
                .disabled(!blocked.isEmpty || connecting || workspace.attempt?.failure.isEmpty == true)
                .help(blocked.isEmpty ? L("Connect to %@", device.name) : blocked)
            }
            Menu { menuItems(presence) } label: { Image(systemName: "ellipsis") }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .frame(width: 28)
                .accessibilityLabel(Text(verbatim: L("More for %@", device.name)))
        }
    }

    @ViewBuilder private func menuItems(_ presence: DevicesModel.Presence) -> some View {
        let blocked = model.connectBlocked(device)
        Button(L("Connect without confirmation (unattended access)")) {
            workspace.connect(peer: device.connectionId, name: device.name, os: device.os, unattended: true, checkPhone: false)
        }.disabled(!blocked.isEmpty)
        Button(L("Connect and ask the person to accept")) {
            workspace.connect(peer: device.connectionId, name: device.name, os: device.os, unattended: false, checkPhone: false)
        }.disabled(!blocked.isEmpty)
        Button(L("Connect in new window")) {
            workspace.connect(peer: device.connectionId, name: device.name, os: device.os, unattended: true, checkPhone: false)
            workspace.openAttemptInWindow()
        }.disabled(!blocked.isEmpty)
        if presence == .offline, !model.knownMacs(device).isEmpty {
            Button(L("Wake")) { model.wake(device) }
        }
        Button(L("Copy ID")) { Pasteboard.copy(String(device.connectionId)) }
        if model.tenant != nil {
            Button(model.isFavorite(device) ? L("Remove from favorites") : L("Add to favorites")) { model.toggleFavorite(device) }
        }
        if model.manages, model.canAdmin(device) {
            Divider()
            Button(L("Rename…")) { rename(device) }.disabled(!model.writable)
            if model.groupsOn { Button(L("Move to group…")) { move(device) }.disabled(!model.writable) }
            if model.adminAvailable {
                Button(L("Notes…")) { notes(device) }.disabled(!model.writable)
                Toggle(isOn: Binding(get: { model.adminRow(device)?.unattendedOn ?? false }, set: { on in Task { _ = await model.setUnattended(device, on: on) } })) {
                    Text(verbatim: L("Unattended access"))
                }.disabled(!model.writable)
            }
            Divider()
            Button(L("Remove…"), role: .destructive) { remove(device) }
        } else if model.managesIsmDevices, ["macos", "windows", "linux"].contains(device.os) {
            Divider()
            Button(L("Edit device")) { edit(device) }
        }
    }
}

/// The steps of a connection, as the connecting panel and the row show them.
enum ConnectSteps {
    static func title(_ step: Int, unattended: Bool) -> String {
        switch step {
        case 0: L("Reaching device")
        case 1: unattended ? L("Checking unattended access") : L("Waiting for the person to accept")
        case 2: L("Encrypting end-to-end")
        default: L("Starting video")
        }
    }
}

/// Bottom right: the connection being prepared (non-blocking), and "ready · Open" notices.
struct ConnectNotices: View {
    @ObservedObject var app: AppModel
    @ObservedObject var workspace: WorkspaceModel

    var body: some View {
        VStack(alignment: .trailing, spacing: 10) {
            ForEach(workspace.readyNotices, id: \.self) { peer in
                if let tab = workspace.tab(peer) { ready(tab) }
            }
            if let attempt = workspace.attempt { panel(attempt) }
        }
        .animation(.easeOut(duration: 0.18), value: workspace.readyNotices)
        .animation(.easeOut(duration: 0.18), value: workspace.attempt)
    }

    private func ready(_ tab: WorkspaceModel.SessionTab) -> some View {
        HStack(spacing: 10) {
            OSTile(os: tab.os, dot: Theme.success, size: 28)
            VStack(alignment: .leading, spacing: 1) {
                Text(verbatim: tab.name).font(.system(size: 13, weight: .semibold))
                Text(verbatim: L("The session is ready.")).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            Button(L("Open")) { workspace.show(tab.peer) }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.return, modifiers: [.command])
            Button { workspace.dismissNotice(tab.peer) } label: { Image(systemName: "xmark").font(.system(size: 10, weight: .bold)) }
                .buttonStyle(.plain).foregroundStyle(.secondary)
                .help(L("Dismiss"))
                .accessibilityLabel(Text(verbatim: L("Dismiss")))
        }
        .padding(12)
        .frame(width: 380)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.hairline))
        .shadow(color: .black.opacity(0.15), radius: 14, y: 6)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(verbatim: L("Session with %@ is ready", tab.name)))
    }

    private func panel(_ attempt: WorkspaceModel.Attempt) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                Image(systemName: "bolt").foregroundStyle(Theme.brand)
                Text(verbatim: attempt.failure.isEmpty ? L("Connecting") : L("Not connected")).font(.system(size: 14, weight: .semibold))
                if attempt.failure.isEmpty {
                    Text(verbatim: "· " + (attempt.inWindow ? L("keep working, it opens in its own window") : L("keep working, it opens as a tab")))
                        .font(.system(size: 13)).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Divider()
            HStack(spacing: 10) {
                OSTile(os: attempt.os, dot: nil, size: 34)
                VStack(alignment: .leading, spacing: 1) {
                    Text(verbatim: attempt.name).font(.system(size: 14, weight: .semibold))
                    Text(verbatim: Format.remoteId(attempt.peer)).font(.system(size: 13, design: .monospaced)).foregroundStyle(.secondary)
                }
            }
            if attempt.failure.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(0..<4, id: \.self) { step in
                        HStack(spacing: 10) {
                            Group {
                                if step < attempt.step {
                                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.success)
                                } else if step == attempt.step {
                                    ProgressView().controlSize(.small).scaleEffect(0.75)
                                } else {
                                    Image(systemName: "circle").foregroundStyle(.tertiary)
                                }
                            }
                            .frame(width: 18, height: 18)
                            Text(verbatim: ConnectSteps.title(step, unattended: attempt.unattended))
                                .foregroundStyle(step > attempt.step ? .secondary : .primary)
                        }
                        .accessibilityElement(children: .combine)
                        .accessibilityValue(Text(verbatim: step < attempt.step ? L("done") : step == attempt.step ? L("in progress") : L("waiting")))
                    }
                }
                if attempt.step == 0, !app.progress.isEmpty {
                    Text(verbatim: app.progress).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            } else {
                MessageLine(attempt.failure)
            }
            HStack {
                Button(attempt.failure.isEmpty ? L("Cancel") : L("Close")) { workspace.cancelAttempt() }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                if attempt.failure.isEmpty, !attempt.inWindow {
                    Button(L("Open in window instead")) { workspace.openAttemptInWindow() }.linkButton()
                }
            }
        }
        .padding(16)
        .frame(width: 380)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.hairline))
        .shadow(color: .black.opacity(0.18), radius: 18, y: 8)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(verbatim: L("Connecting to %@", attempt.name)))
    }
}

/// Sessions: every running session, with Open, own window / back into a tab, and End.
struct SessionsPage: View {
    @ObservedObject var app: AppModel
    @ObservedObject var workspace: WorkspaceModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text(verbatim: L("Sessions")).font(.system(size: 26, weight: .bold)).accessibilityAddTraits(.isHeader)
                Text(verbatim: workspace.tabs.isEmpty ? L("No session is running.") : L("%d sessions", workspace.tabs.count)).foregroundStyle(.secondary)
            }
            if workspace.tabs.isEmpty {
                Text(verbatim: L("Connect to a device under Devices; its session appears here and as a tab in the title bar.")).foregroundStyle(.secondary)
                Button(L("Go to Devices")) { app.section = .devices }
            }
            ForEach(workspace.tabs) { tab in
                HStack(spacing: 12) {
                    OSTile(os: tab.os, dot: tab.state == "ready" ? Theme.success : Theme.warning, size: 34)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: tab.name).font(.system(size: 14, weight: .semibold))
                        Text(verbatim: Format.remoteId(tab.peer) + " · " + (workspace.isInWindow(tab.peer) ? L("In its own window") : L("In a tab")))
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(L("Open")) { workspace.show(tab.peer) }.buttonStyle(PrimaryButtonStyle())
                    if workspace.isInWindow(tab.peer) {
                        Button(L("Move back into a tab")) { workspace.dock(tab.peer) }
                    } else {
                        Button(L("Open in its own window")) { workspace.popOut(tab.peer) }
                    }
                    Button(L("End session")) { workspace.end(tab.peer) }
                }
                .padding(14)
                .background(Theme.page, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.hairline))
            }
            Spacer()
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.page)
    }
}
#endif


/// "Add this computer": the enrolment card in a sheet that closes itself once this computer is registered.
struct AddThisComputerSheet: View {
    @ObservedObject var app: AppModel
    @ObservedObject var enrollment: EnrollmentModel
    @Environment(\.dismiss) private var dismiss
    @State private var started: String?

    init(app: AppModel) {
        self.app = app
        enrollment = app.enrollment
    }

    var body: some View {
        VStack(alignment: .trailing, spacing: 0) {
            Button(L("Close")) { dismiss() }
                .keyboardShortcut(.cancelAction)
                .padding([.top, .trailing], 14)
            ScrollView { EnrollmentCard(app: app, enrollment: enrollment).padding(20) }
        }
        .frame(minWidth: 460, minHeight: 340)
        .onAppear { started = enrollment.current?.device }
        .onChange(of: enrollment.current?.device) { device in
            if device != nil, device != started { dismiss() }
        }
    }
}
