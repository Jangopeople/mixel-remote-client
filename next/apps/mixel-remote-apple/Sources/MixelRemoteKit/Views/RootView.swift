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
                // Mixel's own layout: a calm sidebar panel and the page beside it.
                HStack(spacing: 0) {
                    Sidebar(app: app).frame(width: 224)
                    Divider()
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
            VStack(spacing: 4) {
                ForEach(AppSection.allCases) { section in
                    SidebarItem(section: section, selected: app.section == section) { app.section = section }
                }
            }
            .padding(.horizontal, 10)
            Spacer()
            Divider()
            connection.padding(14)
        }
        .background(Theme.surface)
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
        }
    }
}

struct SidebarItem: View {
    let section: AppSection
    let selected: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: section.symbol).frame(width: 20)
                Text(verbatim: section.title)
                Spacer()
            }
            .font(.body.weight(selected ? .semibold : .regular))
            .foregroundStyle(selected ? Theme.brand : Color.primary.opacity(0.8))
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(selected ? Theme.brandSoft : hovering ? Color.primary.opacity(0.05) : .clear, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(alignment: .leading) {
                if selected { RoundedRectangle(cornerRadius: 2).fill(Theme.brand).frame(width: 3, height: 18).offset(x: -10) }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .accessibilityLabel(Text(verbatim: section.title))
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
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
