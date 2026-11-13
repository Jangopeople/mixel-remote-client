import SwiftUI

#if os(macOS)
/// The Mac window: sidebar with the sections and this computer's connection; the session takes the
/// whole window while it runs. The consent sheet appears over everything.
public struct RootView: View {
    @ObservedObject var app: AppModel

    public init(app: AppModel) { self.app = app }

    public var body: some View {
        Group {
            if app.remote.viewing || app.remote.phase == "outgoing" || (app.remote.phase == "negotiating" && !app.remote.sharing && app.remote.session != nil) {
                SessionWorkspaceView(app: app)
            } else {
                NavigationSplitView {
                    Sidebar(app: app)
                        .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 280)
                } detail: {
                    detail.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                }
            }
        }
        .frame(minWidth: 860, minHeight: 560)
        .sheet(isPresented: Binding(get: { app.host.asking }, set: { _ in })) {
            ConsentSheet(app: app).interactiveDismissDisabled()
        }
    }

    @ViewBuilder private var detail: some View {
        switch app.section {
        case .remote: RemoteControlView(app: app)
        case .devices: DevicesView(app: app)
        case .account: AccountView(app: app)
        case .settings: SettingsView(app: app)
        }
    }
}

struct Sidebar: View {
    @ObservedObject var app: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Brand().padding(.horizontal, 16).padding(.top, 6).padding(.bottom, 18)
            List(selection: Binding(get: { app.section }, set: { if let value = $0 { app.section = value } })) {
                ForEach(AppSection.allCases) { section in
                    Label(section.title, systemImage: section.symbol).tag(section)
                }
            }
            .listStyle(.sidebar)
            Divider()
            connection.padding(14)
        }
    }

    private var connection: some View {
        let remote = app.remote
        return VStack(alignment: .leading, spacing: 4) {
            StatusBadge(remote.connected ? .online : remote.phase == "error" ? .attention : .offline,
                        remote.connected ? L("Online") : remote.phase == "error" ? L("Connection problem") : L("Offline"))
            if app.signedIn, let host = remote.host {
                Text(verbatim: Format.remoteId(host)).font(.headline.monospacedDigit()).textSelection(.enabled)
                    .accessibilityLabel(Text(verbatim: L("Connection ID") + " " + Format.remoteId(host)))
            } else if !app.signedIn {
                Button(L("Sign in")) { app.section = .account }.linkButton()
            }
            Text(verbatim: L("Preview for testing")).font(.caption2.weight(.semibold)).foregroundStyle(Theme.brand)
        }
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
            Button(L("End session")) { app.session.end() }
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
