#if os(iOS)
import SwiftUI

/// iPhone and iPad: Devices, Connect and Account as tabs; a session covers the whole screen.
public struct PhoneRootView: View {
    @ObservedObject var app: AppModel

    public init(app: AppModel) { self.app = app }

    var sessionShown: Binding<Bool> {
        Binding(get: { app.remote.viewing || app.remote.phase == "outgoing" || app.session.frame != nil }, set: { _ in })
    }

    public var body: some View {
        TabView(selection: $app.section) {
            NavigationStack { PhoneDevicesView(app: app) }
                .tabItem { Label(L("Devices"), systemImage: "laptopcomputer.and.iphone") }
                .tag(AppSection.devices)
            NavigationStack { PhoneConnectView(app: app) }
                .tabItem { Label(L("Connect"), systemImage: "arrow.left.arrow.right") }
                .tag(AppSection.remote)
            if app.company.tenant != nil {
                NavigationStack { ReportsView(app: app) }
                    .tabItem { Label(L("Reports"), systemImage: "list.bullet.rectangle") }
                    .tag(AppSection.reports)
            }
            NavigationStack { PhoneAccountView(app: app) }
                .tabItem { Label(L("Account"), systemImage: "person.crop.circle") }
                .tag(AppSection.account)
        }
        .tint(Theme.brand)
        .fullScreenCover(isPresented: sessionShown) { PhoneSessionView(app: app) }
        .sheet(item: Binding(get: { sessionShown.wrappedValue ? nil : app.reports.noteFor.map(NoteTarget.init) }, set: { if $0 == nil { app.reports.noteFor = nil } })) { target in
            SessionNoteSheet(reports: app.reports, host: target.id, name: app.devices.name(of: target.id))
        }
    }
}

/// Connect: a Remote ID (computer or phone with Mixel Quick Support), invitations from colleagues.
struct PhoneConnectView: View {
    @ObservedObject var app: AppModel
    @State private var partner = ""
    @State private var password = ""
    @State private var showHelp = false

    var body: some View {
        Form {
            if !app.signedIn {
                Section {
                    Text(verbatim: L("Sign in to access managed devices.")).foregroundStyle(.secondary)
                    Button(L("Sign in")) { app.section = .account }
                }
            } else if app.enrollment.needsSetup {
                Section { EnrollmentCard(app: app, enrollment: app.enrollment).listRowInsets(EdgeInsets()) }
            }
            Section {
                RemoteIdField(text: $partner)
                SecureField(text: $password, prompt: Text(verbatim: L("One-time password, if provided"))) { Text(verbatim: L("One-time password, if provided")) }
                Button {
                    guard let id = Format.parseRemoteId(partner) else { app.error = L("Enter a nine-digit connection ID"); return }
                    let supplied = password
                    password = ""
                    app.connect(to: id, password: supplied)
                } label: {
                    HStack { Spacer(); Text(verbatim: L("Connect")).font(.headline); Spacer() }
                }
                .disabled(app.busy || !app.signedIn)
                if app.busy || app.remote.phase == "outgoing", !app.progress.isEmpty {
                    HStack(spacing: 8) { ProgressView(); Text(verbatim: app.progress).font(.callout).foregroundStyle(.secondary) }
                }
                MessageLine(app.error)
            } header: {
                Text(verbatim: L("Partner connection ID"))
            } footer: {
                Text(verbatim: L("Enter the 9-digit ID shown by the Mixel Remote host."))
            }
            if app.account.account?.staff == true {
                Section {
                    Button(L("How do I help someone on their phone?")) { showHelp = true }
                } footer: {
                    Text(verbatim: L("The person opens Mixel Quick Support on their phone and tells you the ID it shows."))
                }
            }
            if !app.remote.invitations.isEmpty {
                Section {
                    ForEach(app.remote.invitations) { invitation in
                        HStack {
                            Text(verbatim: L("Computer %@", Format.remoteId(invitation.host)))
                            Spacer()
                            Button(L("Join")) { app.session.join(invitation) }
                        }
                    }
                } header: { Text(verbatim: L("A colleague invites you to a session")) }
            }
            Section {
                HStack {
                    StatusBadge(app.remote.connected ? .online : app.remote.phase == "error" ? .attention : .offline,
                                app.remote.connected ? L("Online") : app.remote.phase == "error" ? L("Connection problem") : L("Offline"))
                    Spacer()
                    if app.signedIn {
                        if app.remote.disconnected { Button(L("Connect to Mixel Remote")) { app.goOnline() } }
                        else { Button(L("Go offline")) { app.goOffline() } }
                    }
                }
            } footer: { Text(verbatim: app.connectionMessage) }
        }
        .navigationTitle(L("Connect"))
        .sheet(isPresented: $showHelp) { PhoneHelpSheet() }
    }
}

/// How phone help works (Mixel Quick Support by ID).
struct PhoneHelpSheet: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Label(L("The person installs Mixel Quick Support from the App Store or Google Play."), systemImage: "1.circle")
                Label(L("They open it and read you the 9-digit ID it shows."), systemImage: "2.circle")
                Label(L("You type the ID under Connect and choose Connect."), systemImage: "3.circle")
                Label(L("On an iPhone the person taps “Start sharing my screen”; on Android they allow the session."), systemImage: "4.circle")
            }
            .navigationTitle(L("Help someone on their phone"))
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L("Done")) { dismiss() } } }
        }
    }
}

/// Devices on the phone: the same directory and status as on the Mac.
struct PhoneDevicesView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var model: DevicesModel

    init(app: AppModel) {
        self.app = app
        model = app.devices
    }

    var body: some View {
        List {
            if !app.signedIn {
                Section {
                    Text(verbatim: L("Sign in to see the devices available to your account.")).foregroundStyle(.secondary)
                    Button(L("Sign in")) { app.section = .account }
                }
            }
            ForEach(model.devices) { device in
                let presence = model.presence(of: device)
                let blocked = model.connectBlocked(device)
                Button {
                    model.connect(device)
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: Format.osSymbol(device.os)).font(.title2).foregroundStyle(presence == .online ? Theme.brand : .secondary).frame(width: 32)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(verbatim: device.name).font(.body.weight(.semibold)).foregroundStyle(.primary)
                            StatusBadge(presence == .online ? .online : presence == .offline ? .offline : .unknown, model.label(presence))
                            let reason = model.reason(for: device)
                            if !reason.isEmpty { Text(verbatim: reason).font(.caption).foregroundStyle(.secondary) }
                        }
                        Spacer()
                        Text(verbatim: Format.remoteId(device.connectionId)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
                .buttonStyle(.plain)
                .opacity(blocked.isEmpty ? 1 : 0.75)
                .disabled(!blocked.isEmpty)
                .accessibilityHint(Text(verbatim: blocked.isEmpty ? L("Connect to %@", device.name) : blocked))
            }
            if app.signedIn, model.devices.isEmpty, !model.loading {
                Text(verbatim: model.tenant != nil ? L("Devices you add appear here. Ask an administrator of your company for access to other devices.") : L("Devices appear here after a Mixel team member enrolls them."))
                    .foregroundStyle(.secondary)
            }
            if model.page > 0 || model.hasNext {
                HStack {
                    Button(L("Previous")) { model.previousPage() }.disabled(model.page == 0).buttonStyle(.borderless)
                    Spacer()
                    Button(L("Next")) { model.nextPage() }.disabled(!model.hasNext).buttonStyle(.borderless)
                }
            }
            MessageLine(model.error)
        }
        .searchable(text: $model.search, prompt: Text(verbatim: L("Device name")))
        .onSubmit(of: .search) { Task { await model.load() } }
        .refreshable { await model.load() }
        .navigationTitle(L("Devices"))
        .overlay { if model.loading, model.devices.isEmpty { ProgressView() } }
    }
}

/// Account on the phone: sign-in, company basics (members, invitations), settings and legal.
struct PhoneAccountView: View {
    @ObservedObject var app: AppModel

    var body: some View {
        AccountView(app: app)
            .background(Theme.background)
            .navigationTitle(L("Account"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    NavigationLink {
                        SettingsView(app: app).navigationTitle(L("Settings"))
                    } label: { Label(L("Settings"), systemImage: "gearshape") }
                }
            }
    }
}
#endif
