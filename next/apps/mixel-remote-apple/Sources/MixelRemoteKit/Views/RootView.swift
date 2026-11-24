import SwiftUI

#if os(macOS)
/// The Mac window ("Pro Console"): session tabs in the title bar; under the Devices tab the sidebar
/// (Devices, Sessions, Reports, Settings, views, groups, this computer, the account) and the page
/// beside it. Connecting never leaves the Devices list; a ready session waits behind an "Open"
/// notice unless Settings say otherwise. The consent sheet appears over everything.
public struct RootView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var workspace: WorkspaceModel
    /// Draw the title bar's tab strip (off for screenshots of the page alone).
    let titleBar: Bool

    public init(app: AppModel, titleBar: Bool = true) {
        self.app = app
        workspace = app.workspace
        self.titleBar = titleBar
    }

    /// Signed out: the sign-in screen takes the whole window (Settings stay reachable from it).
    /// Do not flash the sign-in screen while restoring a saved session ("verifying").
    private var showsSignIn: Bool { app.accountChecked && !app.signedIn && app.account.phase != "verifying" && app.section != .settings }

    public var body: some View {
        Group {
            if app.quickSupportMode && !app.signedIn {
                QuickSupportConsoleView(app: app)
            } else if showsSignIn {
                SignInScreen(app: app)
            } else if !app.accountChecked || (!app.signedIn && app.account.phase == "verifying") {
                VStack(spacing: 12) {
                    ProgressView().controlSize(.regular)
                    Text(verbatim: L("Signing in…"))
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.muted)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.page)
            } else {
                shell
            }
        }
        .background(Theme.page)
        .frame(minWidth: app.quickSupportStandalone ? 520 : 960, minHeight: app.quickSupportStandalone ? 440 : 600)
        .sheet(isPresented: Binding(get: { app.host.asking }, set: { _ in })) {
            ConsentSheet(app: app).interactiveDismissDisabled()
        }
        .sheet(item: Binding(get: { app.host.asking ? nil : app.devices.savePromptFor.map(NoteTarget.init) }, set: { if $0 == nil { app.devices.savePromptFor = nil } })) { target in
            SaveConnectedDeviceDialog(app: app, model: app.devices, connectionId: target.id)
        }
        .sheet(item: Binding(get: { (app.host.asking || app.devices.savePromptFor != nil) ? nil : app.reports.noteFor.map(NoteTarget.init) }, set: { if $0 == nil { app.reports.noteFor = nil } })) { target in
            SessionNoteSheet(reports: app.reports, host: target.id, name: app.devices.name(of: target.id))
        }
    }

    private var shell: some View {
        VStack(spacing: 0) {
            if titleBar {
                TitleTabStrip(app: app, workspace: workspace)
                Rectangle().fill(Theme.hairline).frame(height: 1)
            }
            ZStack(alignment: .bottomTrailing) {
                content
                ConnectNotices(app: app, workspace: workspace)
                    .padding(18)
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch workspace.selected {
        case .session(let peer) where workspace.tab(peer) != nil && !workspace.isInWindow(peer):
            SessionPane(app: app, peer: peer, inWindow: false)
        default:
            HStack(spacing: 0) {
                Sidebar(app: app, devices: app.devices, workspace: workspace).frame(width: 248)
                Rectangle().fill(Theme.hairline).frame(width: 1)
                detail.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
    }

    @ViewBuilder private var detail: some View {
        VStack(spacing: 0) {
            if app.remote.phase == "active", app.remote.sharing, app.section != .remote { HostedBanner(app: app) }
            switch app.section {
            case .remote: RemoteControlView(app: app)
            case .devices: DeviceConsoleView(app: app)
            case .sessions: SessionsPage(app: app, workspace: workspace)
            case .reports: ReportsView(app: app)
            case .account: AccountView(app: app)
            case .settings: SettingsView(app: app)
            }
        }
    }
}

/// A technician is connected to this computer: always visible, one click to the details.
struct HostedBanner: View {
    @ObservedObject var app: AppModel
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "dot.radiowaves.left.and.right").foregroundStyle(Theme.brand)
            Text(verbatim: L("A technician is connected to this computer.")).font(.callout.weight(.semibold))
            Spacer()
            Button(L("Show")) { app.section = .remote }
            Button(L("End session")) { app.host.stopSharing() }.buttonStyle(.borderedProminent).tint(Theme.brand)
        }
        .padding(.horizontal, 20).padding(.vertical, 9)
        .background(Theme.activeRow)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.hairline).frame(height: 1) }
    }
}

// MARK: Title bar

/// The tabs in the title bar: Devices, one tab per session, and + (connect by ID).
struct TitleTabStrip: View {
    @ObservedObject var app: AppModel
    @ObservedObject var workspace: WorkspaceModel
    @State private var ending: WorkspaceModel.SessionTab?

    var body: some View {
        HStack(spacing: 2) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 2) {
                    TitleTab(selected: workspace.selected == .console, label: {
                        Image(systemName: "laptopcomputer").font(.system(size: 13))
                        Text(verbatim: L("Devices"))
                    }, select: { workspace.selected = .console }, close: nil)
                    .accessibilityLabel(Text(verbatim: L("Devices")))
                    ForEach(workspace.tabs) { tab in
                        TitleTab(selected: workspace.selected == .session(tab.peer), label: {
                            Circle().fill(dot(tab)).frame(width: 7, height: 7)
                            OSGlyph(os: tab.os).frame(width: 13, height: 13)
                            Text(verbatim: tab.name).lineLimit(1)
                            if workspace.isInWindow(tab.peer) {
                                Image(systemName: "arrow.up.forward.square").foregroundStyle(Theme.infoText)
                                    .accessibilityLabel(Text(verbatim: L("In its own window")))
                            }
                        }, select: { workspace.show(tab.peer, inWindow: false) }, close: { ending = tab })
                        .accessibilityLabel(Text(verbatim: L("Session with %@", tab.name)))
                        .contextMenu {
                            if workspace.isInWindow(tab.peer) {
                                Button(L("Move back into a tab")) { workspace.dock(tab.peer) }
                            } else {
                                Button(L("Open in its own window")) { workspace.popOut(tab.peer) }
                            }
                            Divider()
                            Button(L("End session")) { ending = tab }
                        }
                    }
                    Button {
                        workspace.selected = .console
                        app.section = .devices
                        NotificationCenter.default.post(name: .mixelFocusConnectById, object: nil)
                    } label: { Image(systemName: "plus").font(.system(size: 13, weight: .medium)).frame(width: 26, height: 26) }
                        .buttonStyle(.plain)
                        .help(L("Connect by ID"))
                        .accessibilityLabel(Text(verbatim: L("Connect by ID")))
                }
            }
            Spacer(minLength: 20)
        }
        .padding(.leading, 84)
        .padding(.trailing, 12)
        .frame(height: 40)
        .background(WindowDragArea())
        .background(Theme.chrome)
        .alert(Text(verbatim: L("End the session with %@?", ending?.name ?? "")), isPresented: Binding(get: { ending != nil }, set: { if !$0 { ending = nil } })) {
            Button(L("Cancel"), role: .cancel) { ending = nil }
            Button(L("End session"), role: .destructive) { if let tab = ending { workspace.end(tab.peer) }; ending = nil }
        } message: { Text(verbatim: L("The connection ends for both sides.")) }
    }

    func dot(_ tab: WorkspaceModel.SessionTab) -> Color {
        switch tab.state {
        case "ready": Theme.success
        case "ending": .secondary
        case "reconnect_failed": Theme.brand
        default: Theme.warning
        }
    }
}

struct TitleTab<Label: View>: View {
    let selected: Bool
    @ViewBuilder let label: () -> Label
    let select: () -> Void
    let close: (() -> Void)?
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 7) {
            Button(action: select) {
                HStack(spacing: 7) { label() }
                    .font(.system(size: 13, weight: selected ? .semibold : .regular))
                    .foregroundStyle(selected ? Color.primary : Color.secondary)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
            if let close {
                Button(action: close) { Image(systemName: "xmark").font(.system(size: 10, weight: .semibold)).frame(width: 16, height: 16) }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .help(L("End session"))
                    .accessibilityLabel(Text(verbatim: L("End session")))
            }
        }
        .padding(.horizontal, 12)
        .frame(height: 32)
        .frame(maxWidth: 240)
        .background {
            if selected {
                UnevenRoundedTop(radius: 8).fill(Theme.page)
                    .overlay(UnevenRoundedTop(radius: 8).stroke(Theme.hairline, lineWidth: 1))
            } else if hovering {
                RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Color.primary.opacity(0.05))
            }
        }
        .offset(y: selected ? 4 : 0)
        .onHover { hovering = $0 }
    }
}

/// A rectangle with rounded top corners (macOS 13 has no UnevenRoundedRectangle).
struct UnevenRoundedTop: Shape {
    let radius: CGFloat
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + radius))
        path.addQuadCurve(to: CGPoint(x: rect.minX + radius, y: rect.minY), control: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - radius, y: rect.minY))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY + radius), control: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        return path
    }
}

/// The operating system's mark, small (tabs, toolbar).
struct OSGlyph: View {
    let os: String
    var body: some View {
        if os.lowercased() == "windows" {
            WindowsMark().padding(1)
        } else {
            Image(systemName: OSTile.symbol(os)).resizable().scaledToFit()
        }
    }
}

/// Lets the window be moved (and zoomed by a double click) by its custom title bar.
struct WindowDragArea: NSViewRepresentable {
    final class DragView: NSView {
        override var mouseDownCanMoveWindow: Bool { true }
        override func mouseDown(with event: NSEvent) {
            if event.clickCount == 2 { window?.performZoom(nil) } else { window?.performDrag(with: event) }
        }
    }
    func makeNSView(context: Context) -> NSView { DragView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

public extension Notification.Name {
    /// "+" in the title bar: put the cursor into "Connect by ID".
    static let mixelFocusConnectById = Notification.Name("mixel.remote.focusConnectById")
}

// MARK: Sidebar

struct Sidebar: View {
    @ObservedObject var app: AppModel
    @ObservedObject var devices: DevicesModel
    @ObservedObject var workspace: WorkspaceModel
    @State private var creatingGroup = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    brand.padding(.horizontal, 10).padding(.top, 14).padding(.bottom, 14)
                    item(.devices, count: app.signedIn ? devices.allDevices.count : nil)
                    item(.sessions, badge: workspace.tabs.filter { $0.state == "ready" || $0.state == "starting" }.count)
                    if app.company.tenant != nil { item(.reports) }
                    item(.settings)
                    if app.signedIn, !devices.allDevices.isEmpty || !devices.recentConnectionIds.isEmpty {
                        heading(L("Views"))
                        view(.all, L("All devices"), "laptopcomputer")
                        view(.online, L("Online now"), "bolt")
                        if devices.tenant != nil || devices.adminAvailable {
                            view(.favorites, L("Favourites"), "star")
                        }
                        view(.recent, L("Recent"), "clock")
                        if !devices.groupNames.isEmpty || devices.deploys && devices.groupsOn {
                            HStack {
                                heading(L("Groups"))
                                if devices.deploys, devices.groupsOn, devices.writable {
                                    Button { creatingGroup = true } label: { Image(systemName: "plus").frame(width: 22, height: 22) }
                                        .buttonStyle(.plain).foregroundStyle(.secondary)
                                        .help(L("Create group"))
                                        .accessibilityLabel(Text(verbatim: L("Create group")))
                                        .padding(.top, 14)
                                }
                            }
                            ForEach(devices.groupNames, id: \.self) { name in
                                groupRow(name)
                            }
                        }
                    }
                }
                .padding(.horizontal, 10)
            }
            Spacer(minLength: 0)
            ThisComputerCard(app: app).padding(.horizontal, 12).padding(.bottom, 10)
            Rectangle().fill(Theme.hairline).frame(height: 1)
            ProfileRow(app: app).padding(12)
        }
        .background(Theme.chrome)
        .sheet(isPresented: $creatingGroup) {
            FormSheet(title: L("Create group"), fields: [.init(id: "name", label: L("Name"))], confirm: L("Create group")) { values in
                await devices.createGroup(values["name"] ?? "") ? nil : devices.error
            }
        }
    }

    private var brand: some View {
        HStack(spacing: 10) {
            MixelMark(size: 34)
            VStack(alignment: .leading, spacing: 1) {
                Text(verbatim: "Mixel Remote").font(.system(size: 14, weight: .semibold))
                if !organization.isEmpty { Text(verbatim: organization).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var organization: String {
        if let tenant = app.company.tenant { return tenant.name }
        if app.account.account?.staff == true { return "Mixel IT" }
        return ""
    }

    private func heading(_ text: String) -> some View {
        Text(verbatim: text.uppercased())
            .font(.system(size: 11, weight: .semibold)).tracking(0.8)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 10).padding(.top, 16).padding(.bottom, 4)
            .accessibilityAddTraits(.isHeader)
    }

    private func item(_ section: AppSection, count: Int? = nil, badge: Int = 0) -> some View {
        let selected = app.section == section
        return SidebarRow(selected: selected, symbol: symbol(section), title: section == .reports ? L("Reports") : section.title, count: count, badge: badge) {
            app.section = section
            workspace.selected = .console
        }
    }

    private func symbol(_ section: AppSection) -> String {
        switch section {
        case .devices: "laptopcomputer"
        case .sessions: "macwindow"
        case .reports: "chart.bar"
        case .settings: "slider.horizontal.3"
        default: section.symbol
        }
    }

    private func view(_ view: DevicesModel.ListView, _ title: String, _ symbol: String) -> some View {
        SidebarRow(selected: app.section == .devices && devices.listView == view, symbol: symbol, title: title, count: devices.devices(in: view).count, badge: 0) {
            devices.listView = view
            app.section = .devices
            workspace.selected = .console
        }
    }

    private func groupRow(_ name: String) -> some View {
        let selected = app.section == .devices && devices.listView == .group(name)
        return Button {
            devices.listView = .group(name)
            app.section = .devices
            workspace.selected = .console
        } label: {
            HStack(spacing: 10) {
                Circle().fill(Theme.groupColor(name)).frame(width: 8, height: 8).frame(width: 18)
                Text(verbatim: name).lineLimit(1)
                Spacer()
                Text(verbatim: "\(devices.devices(in: .group(name)).count)").foregroundStyle(.secondary).monospacedDigit()
            }
            .font(.system(size: 13, weight: selected ? .semibold : .regular))
            .foregroundStyle(selected ? Theme.brand : Color.primary)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(selected ? Theme.brandSoft : .clear, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}

struct SidebarRow: View {
    let selected: Bool
    let symbol: String
    let title: String
    let count: Int?
    let badge: Int
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).frame(width: 18)
                Text(verbatim: title).lineLimit(1)
                Spacer()
                if badge > 0 {
                    Text(verbatim: "\(badge)").font(.caption.weight(.bold)).foregroundStyle(.white)
                        .padding(.horizontal, 6).frame(minWidth: 18, minHeight: 18)
                        .background(Theme.brand, in: Capsule())
                } else if let count {
                    Text(verbatim: "\(count)").foregroundStyle(selected ? Theme.brand : .secondary).monospacedDigit()
                }
            }
            .font(.system(size: 13, weight: selected ? .semibold : .regular))
            .foregroundStyle(selected ? Theme.brand : Color.primary)
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(selected ? Theme.brandSoft : hovering ? Color.primary.opacity(0.05) : .clear, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .accessibilityLabel(Text(verbatim: badge > 0 ? "\(title), \(badge)" : title))
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}

/// "This computer · online" with its ID; a click opens this computer's page (being helped, passwords).
struct ThisComputerCard: View {
    @ObservedObject var app: AppModel
    @ObservedObject var host: HostModel

    init(app: AppModel) {
        self.app = app
        host = app.host
    }

    var body: some View {
        let remote = app.remote
        Button { app.section = .remote; app.workspace.selected = .console } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Circle().fill(!host.permissionsReady ? Theme.warning : remote.connected ? Theme.success : remote.phase == "error" ? Theme.brand : Color.secondary).frame(width: 7, height: 7)
                    Text(verbatim: L("This computer") + " · " + (!host.permissionsReady ? L("permissions required") : remote.connected ? L("online") : remote.phase == "error" ? L("Connection problem") : L("offline")))
                        .font(.caption).foregroundStyle(.secondary)
                }
                HStack {
                    if !host.permissionsReady {
                        Text(verbatim: L("Grant macOS permissions to see ID")).font(.callout.weight(.medium)).foregroundStyle(Theme.warning)
                            .fixedSize(horizontal: false, vertical: true)
                    } else if app.signedIn, let hostId = remote.host {
                        Text(verbatim: Format.remoteId(hostId)).font(.system(size: 17, weight: .semibold, design: .monospaced))
                    } else {
                        Text(verbatim: app.signedIn ? L("Not set up yet") : L("Sign in to get your connection ID.")).font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.page, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(!host.permissionsReady ? Theme.warning : app.section == .remote ? Theme.brand : Theme.hairline, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .overlay(alignment: .topTrailing) {
            if app.signedIn, remote.host != nil, host.permissionsReady {
                Button { app.host.copyId() } label: { Image(systemName: "doc.on.doc").frame(width: 26, height: 26) }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
                    .help(L("Copy ID"))
                    .accessibilityLabel(Text(verbatim: L("Copy ID")))
                    .padding(.top, 22).padding(.trailing, 6)
            }
        }
        .accessibilityLabel(Text(verbatim: L("This computer") + ", " + (!host.permissionsReady ? L("permissions required") : app.signedIn ? Format.remoteId(remote.host) : L("offline"))))
    }
}

/// The signed-in person (initials, name, e-mail); a click opens the account.
struct ProfileRow: View {
    @ObservedObject var app: AppModel

    var body: some View {
        Button { app.section = .account; app.workspace.selected = .console } label: {
            HStack(spacing: 10) {
                Group {
                    if let initials {
                        Text(verbatim: initials)
                            .font(.system(size: 12, weight: .bold)).foregroundStyle(.white)
                    } else {
                        Image(systemName: "person.fill")
                            .font(.system(size: 14, weight: .semibold)).foregroundStyle(.white)
                    }
                }
                .frame(width: 32, height: 32)
                .background(app.signedIn ? Theme.brand : Color.secondary, in: Circle())
                VStack(alignment: .leading, spacing: 1) {
                    Text(verbatim: app.signedIn ? (app.account.account?.displayName ?? L("Signed in")) : L("Sign in")).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                    Text(verbatim: app.signedIn ? L("Account") : L("Sign in to Mixel")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(verbatim: L("Account")))
    }

    private var initials: String? {
        guard app.signedIn, let name = app.account.account?.displayName, !name.isEmpty else { return nil }
        let parts = name.split(separator: " ").prefix(2)
        let out = parts.compactMap(\.first).map(String.init).joined().uppercased()
        return out.isEmpty ? nil : out
    }
}

/// The menu bar extra: the connection, the ID, and the main actions.
public struct MenuBarContent: View {
    @ObservedObject var app: AppModel
    let open: () -> Void
    let quit: () -> Void

    public init(app: AppModel, open: @escaping () -> Void, quit: @escaping () -> Void) {
        self.app = app
        self.open = open
        self.quit = quit
    }

    public var body: some View {
        let remote = app.remote
        Text(verbatim: remote.connected ? L("Online · ID %@", Format.remoteId(remote.host)) : L("Offline"))
        if remote.inSession {
            Text(verbatim: app.connectionMessage)
            Button(L("End session")) { if remote.sharing { app.host.stopSharing() } else { app.session.end() } }
        }
        Divider()
        Button(L("Open Mixel Remote")) { open() }.keyboardShortcut("o")
        if app.signedIn, remote.host != nil {
            Button(L("Copy ID")) { app.host.copyId() }
        }
        if app.signedIn {
            if remote.disconnected { Button(L("Make this computer available")) { app.goOnline() } }
            else if !remote.inSession { Button(L("Go offline")) { app.goOffline() } }
        } else {
            Button(L("Sign in…")) { open(); app.section = .account }
        }
        Divider()
        Button(L("Quit Mixel Remote")) { quit() }.keyboardShortcut("q")
    }
}
#endif

/// The Remote ID a session note is asked for.
struct NoteTarget: Identifiable { let id: UInt32 }
