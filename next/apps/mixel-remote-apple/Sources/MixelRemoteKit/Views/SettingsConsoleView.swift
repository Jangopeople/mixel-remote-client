#if os(macOS)
import AppKit
import SwiftUI

/// Settings in the "Pro Console" structure: Sessions, Unattended access, Updates, General, Account and
/// security, About and legal, with a section list at the left. Every setting the app had stays; settings
/// that need something the app does not have yet (an update channel, a default picture quality) are not shown.
struct SettingsConsoleView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var settings: SettingsModel
    @ObservedObject var enrollment: EnrollmentModel
    @ObservedObject var workspace: WorkspaceModel
    @ObservedObject var host: HostModel
    @ObservedObject var devices: DevicesModel
    @State private var confirmRemove = false
    @State private var removalResult = ""
    @State private var current = Section.sessions

    enum Section: String, CaseIterable, Identifiable {
        case sessions, unattended, updates, general, account, about, team
        var id: String { rawValue }
        var title: String {
            switch self {
            case .sessions: L("Sessions")
            case .unattended: L("Unattended access")
            case .updates: L("Updates")
            case .general: L("General")
            case .account: L("Account & security")
            case .about: L("About and legal")
            case .team: L("Mixel team")
            }
        }
    }

    init(app: AppModel) {
        self.app = app
        settings = app.settings
        enrollment = app.enrollment
        workspace = app.workspace
        host = app.host
        devices = app.devices
    }

    private var sections: [Section] {
        Section.allCases.filter { $0 != .team || app.account.account?.staff == true }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ConsoleHeader(title: L("Settings"), subtitle: L("Mixel Remote %@ on this Mac", app.info.version)) { EmptyView() }
                .padding(.horizontal, 28).padding(.top, 22).padding(.bottom, 14)
            GeometryReader { geometry in
                ScrollViewReader { proxy in
                    HStack(alignment: .top, spacing: 24) {
                        if geometry.size.width >= 760 { nav(proxy).frame(width: 180) }
                        ScrollView {
                            VStack(alignment: .leading, spacing: 28) {
                                ForEach(sections) { section in
                                    group(section).id(section.id)
                                }
                                MessageLine(settings.error)
                            }
                            .padding(.bottom, 40)
                            .frame(maxWidth: 760, alignment: .leading)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(.horizontal, 28)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.chrome.opacity(0.5))
        .background(Theme.page)
        .confirmationDialog(L("Remove this computer from Mixel Remote?"), isPresented: $confirmRemove) {
            Button(L("Remove this computer"), role: .destructive) {
                Task {
                    switch await enrollment.removeThisComputer() {
                    case .removed: removalResult = L("This computer was removed from Mixel Remote.")
                    case .queued: removalResult = L("Saved. This computer is removed as soon as Mixel Remote can be reached.")
                    case .stale: removalResult = L("This computer's registration changed since; nothing was removed.")
                    case nil: break
                    }
                }
            }
        } message: {
            Text(verbatim: L("It can no longer be reached or connect until it is added again."))
        }
        .task { await settings.load() }
    }

    // MARK: Section list

    private func nav(_ proxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(sections) { section in
                Button {
                    current = section
                    withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(section.id, anchor: .top) }
                } label: {
                    Text(verbatim: section.title)
                        .font(.system(size: 13, weight: current == section ? .semibold : .regular))
                        .foregroundStyle(current == section ? Theme.brand : Color.primary)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(current == section ? Theme.brandSoft : .clear, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(current == section ? [.isSelected, .isButton] : .isButton)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(verbatim: L("Settings sections")))
    }

    private func heading(_ title: String, _ text: String = "") -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(verbatim: title).font(.system(size: 17, weight: .semibold)).accessibilityAddTraits(.isHeader)
            if !text.isEmpty { Text(verbatim: text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
        }
    }

    @ViewBuilder private func group(_ section: Section) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            switch section {
            case .sessions: sessions
            case .unattended: unattended
            case .updates: updates
            case .general: general
            case .account: account
            case .about: about
            case .team: team
            }
        }
    }

    // MARK: Sessions

    private var sessions: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading(L("Sessions"), L("How new remote sessions open. You can always move a session between tab and window."))
            ConsoleCard {
                SettingRow(title: L("Open new sessions in"), help: L("Default for Connect, Connect by ID and links from Mixel ISM"), first: true) { EmptyView() }
                HStack(spacing: 12) {
                    choice(L("A tab in the main window"), L("All sessions in one place; switch with the tabs in the title bar."), on: workspace.openIn == .tab, window: false) { workspace.openIn = .tab }
                    choice(L("Its own window"), L("Best with several monitors; drag it where you want."), on: workspace.openIn == .window, window: true) { workspace.openIn = .window }
                }
                .padding(.horizontal, 16).padding(.bottom, 14)
                SettingRow(title: L("Bring the session to the front when it is ready"), help: L("Off: you stay in Devices and a notice offers “Open”.")) {
                    ConsoleSwitch(isOn: Binding(get: { workspace.whenReady == .open }, set: { workspace.whenReady = $0 ? .open : .stay }), label: L("Bring the session to the front when it is ready"))
                }
            }
        }
    }

    private func choice(_ title: String, _ detail: String, on: Bool, window: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                RoundedRectangle(cornerRadius: 6, style: .continuous).fill(Theme.chrome).frame(height: 54)
                    .overlay(alignment: .topLeading) {
                        if window {
                            RoundedRectangle(cornerRadius: 3).fill(Theme.page).frame(width: 70, height: 34).overlay(RoundedRectangle(cornerRadius: 3).stroke(Theme.hairline)).padding(10)
                        } else {
                            HStack(spacing: 3) {
                                ForEach(0..<3, id: \.self) { index in
                                    UnevenRoundedTop(radius: 3).fill(index == 0 ? Theme.page : Theme.hairline).frame(width: 34, height: 12)
                                }
                            }
                            .padding(.leading, 10).padding(.top, 10)
                        }
                    }
                    .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).stroke(Theme.hairline))
                Text(verbatim: title).font(.system(size: 13, weight: .semibold))
                Text(verbatim: detail).font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(on ? Theme.brandSoft : Theme.page, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(on ? Theme.brand : Theme.hairline, lineWidth: on ? 2 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? [.isSelected, .isButton] : .isButton)
        .accessibilityLabel(Text(verbatim: title))
    }

    // MARK: Unattended access

    private var remote: RemoteStatus { app.remote }

    private var unattended: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading(L("Unattended access"), L("Lets approved technicians of your company connect to this computer without someone confirming."))
            ConsoleCard {
                if remote.controllerOnly || !app.signedIn {
                    SettingRow(title: L("Allow unattended access to this computer"), help: remote.controllerOnly ? L("This app controls other devices; it cannot be controlled itself.") : L("Sign in to set up this computer."), first: true) { EmptyView() }
                } else {
                    SettingRow(title: L("Allow unattended access to this computer"), help: unattendedHelp, first: true) {
                        ConsoleSwitch(isOn: Binding(get: { remote.unattendedEnabled || remote.unattendedPending == "enabling" || !host.unattendedSources.isEmpty },
                                                    set: { on in
                            if on { host.loadUnattendedSources() }
                            else if remote.unattendedEnabled || remote.unattendedPending == "enabling" { host.disableUnattended() }
                            else { host.cancelUnattendedSetup() }
                        }), label: L("Allow unattended access to this computer"))
                        .disabled(app.busy || remote.phase != "ready" && !remote.unattendedEnabled)
                    }
                    if !remote.unattendedEnabled, !host.unattendedSources.isEmpty {
                        SettingRow(title: L("Display shared")) {
                            Picker(selection: $host.unattendedSource) {
                                ForEach(host.unattendedSources) { Text(verbatim: $0.name).tag($0.id) }
                            } label: { Text(verbatim: L("Display")) }.labelsHidden().fixedSize()
                        }
                        SettingRow(title: L("Allow keyboard and mouse control")) {
                            ConsoleSwitch(isOn: $host.unattendedControl, label: L("Allow keyboard and mouse control")).disabled(!remote.inputAccess)
                        }
                        SettingRow(title: L("I confirm that approved users may see this display without asking.")) {
                            ConsoleSwitch(isOn: $host.unattendedConfirm, label: L("I confirm that approved users may see this display without asking."))
                        }
                        SettingRow(title: L("Enable unattended sharing")) {
                            Button(L("Enable unattended sharing")) { host.enableUnattended() }.buttonStyle(PrimaryButtonStyle()).disabled(app.busy || !host.unattendedConfirm)
                        }
                    }
                    if let message = host.error as String?, !message.isEmpty { MessageLine(message).padding(.horizontal, 16).padding(.bottom, 10) }
                }
                SettingRow(title: L("Connection ID"), help: remote.host == nil ? L("This computer is not set up yet.") : L("The ID a technician uses to reach this computer.")) {
                    HStack(spacing: 8) {
                        Text(verbatim: Format.remoteId(remote.host ?? enrollment.current?.connectionId)).font(.system(size: 13, design: .monospaced)).textSelection(.enabled)
                        if app.signedIn, remote.host != nil { Button(L("Copy ID")) { host.copyId() }.controlSize(.small) }
                    }
                }
            }
            if let status = settings.signInScreen, app.account.account?.staff == true || status.connectionId != nil {
                ConsoleCard {
                    SettingRow(title: L("Sign-in screen"), help: L("Reach this Mac before anyone logs in."), first: true) { EmptyView() }
                    SignInScreenSection(app: app, settings: settings, enrollment: enrollment, status: status, embedded: true)
                }
            }
        }
    }

    private var unattendedHelp: String {
        remote.unattendedPending == "enabling" ? L("Turning on. Waiting for the connection service.")
            : remote.unattendedPending != nil ? L("Sharing is off. Confirming with the connection service.")
            : remote.unattendedEnabled ? L("On. Approved users can connect while this computer stays signed in.")
            : L("Off. Choose a display and confirm before enabling.")
    }

    // MARK: Updates

    private var updates: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading(L("Updates"), L("Mixel Remote updates itself in the background and restarts between sessions only."))
            ConsoleCard {
                HStack(spacing: 12) {
                    Image(systemName: settings.update == nil ? "checkmark" : "arrow.down.circle.fill").font(.system(size: 13, weight: .bold))
                        .foregroundStyle(settings.update == nil ? Theme.sessionText : Theme.updateText)
                        .frame(width: 28, height: 28)
                        .background(settings.update == nil ? Theme.sessionSoft : Theme.updateSoft, in: Circle())
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: settings.update.map { L("Version %@ is available.", $0.version) } ?? (settings.updateChecked ? L("Mixel Remote is up to date") : L("Mixel Remote %@", app.info.version)))
                            .font(.system(size: 13, weight: .semibold))
                        Text(verbatim: L("Version %@", app.info.version)).font(.system(size: 12)).foregroundStyle(.secondary).monospacedDigit()
                        if settings.update?.waitsForSession == true {
                            Text(verbatim: L("Update ready, installs after this session")).font(.system(size: 12)).foregroundStyle(Theme.updateText)
                        }
                        MessageLine(settings.notice, error: false)
                    }
                    Spacer()
                    if let update = settings.update {
                        if update.installable { Button(L("Update now")) { settings.installUpdate() }.buttonStyle(PrimaryButtonStyle()).disabled(settings.installing) }
                        else { Button(L("Download")) { settings.openStorePage() } }
                    }
                    Button { settings.checkForUpdate() } label: { Label(L("Check for updates"), systemImage: "arrow.clockwise") }.disabled(settings.installing)
                }
                .padding(16)
                SettingRow(title: L("Install updates automatically")) {
                    ConsoleSwitch(isOn: $settings.autoUpdate, label: L("Install updates automatically"))
                }
                if devices.versionsKnown, devices.outdatedCount > 0, let newest = devices.newestVersion {
                    SettingRow(title: L("Devices with an older version"), help: L("%d company devices are not on %@", devices.outdatedCount, newest)) {
                        Button(L("Show devices")) {
                            devices.listView = .all
                            devices.updateOnly = true
                            workspace.selected = .console
                            app.section = .devices
                        }
                    }
                }
            }
        }
    }

    // MARK: General

    private var general: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading(L("General"))
            ConsoleCard {
                SettingRow(title: L("Language"), first: true) {
                    Picker(selection: Binding(get: { settings.language }, set: { settings.setLanguage($0) })) {
                        Text(verbatim: L("Automatic (this computer’s language)")).tag("auto")
                        Text(verbatim: "English").tag("en")
                        Text(verbatim: "Deutsch").tag("de")
                        Text(verbatim: "Français").tag("fr")
                        Text(verbatim: "Italiano").tag("it")
                    } label: { Text(verbatim: L("Language")) }.labelsHidden().fixedSize()
                }
                SettingRow(title: L("Start Mixel Remote when this computer starts")) {
                    ConsoleSwitch(isOn: Binding(get: { settings.startup?.autostart ?? false }, set: { settings.setAutostart($0) }), label: L("Start Mixel Remote when this computer starts"))
                }
                SettingRow(title: L("Stay signed in on this computer")) {
                    ConsoleSwitch(isOn: Binding(get: { settings.startup?.remembered ?? false }, set: { settings.setStaySignedIn($0) }), label: L("Stay signed in on this computer")).disabled(!app.signedIn)
                }
                SettingRow(title: L("Appearance")) {
                    ConsoleSegmented(options: [(SettingsModel.Appearance.system, L("System")), (.light, L("Light")), (.dark, L("Dark"))], selection: $settings.appearance, label: L("Appearance"))
                }
            }
        }
    }

    // MARK: Account & security

    private var account: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading(L("Account & security"))
            ConsoleCard {
                HStack(spacing: 12) {
                    Group {
                        if let initials {
                            Text(verbatim: initials).font(.system(size: 12, weight: .bold)).foregroundStyle(.white)
                        } else {
                            Image(systemName: "person.fill").font(.system(size: 14, weight: .semibold)).foregroundStyle(.white)
                        }
                    }
                    .frame(width: 32, height: 32).background(app.signedIn ? Theme.brand : Color.secondary, in: Circle())
                    .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: app.signedIn ? (app.account.account?.displayName ?? L("Signed in")) : L("Not signed in")).font(.system(size: 13, weight: .semibold))
                        Text(verbatim: accountLine).font(.system(size: 12)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if app.signedIn {
                        Button(L("Account…")) { workspace.selected = .console; app.section = .account }
                        Button(L("Sign out")) { app.signOut() }.disabled(app.busy)
                    } else {
                        Button(L("Sign in")) { workspace.selected = .console; app.section = .account }.buttonStyle(PrimaryButtonStyle())
                    }
                }
                .padding(16)
                SettingRow(title: L("Screen recording"), help: L("Needed to share this screen with a technician.")) { permission(settings.screenAccess) { settings.requestScreenAccess() } }
                SettingRow(title: L("Accessibility"), help: L("Needed so a technician can use keyboard and mouse here.")) { permission(remote.inputAccess) { settings.requestInputAccess() } }
                if let printing = settings.printing, printing.supported {
                    SettingRow(title: L("Remote printing"), help: L("Adds the printer “Mixel Remote Printer” to this computer. What is printed on it during a session arrives at the technician, who prints it on a printer there. Needs administrator approval on this computer.")) {
                        ConsoleSwitch(isOn: Binding(get: { printing.installed }, set: { settings.setPrinting($0) }), label: L("Remote printing"))
                    }
                }
                SettingRow(title: L("This computer"), help: settings.removalPending ? L("This computer is being removed from Mixel Remote. It is removed as soon as it is online.")
                           : enrollment.current != nil || remote.host != nil ? L("Remove it from Mixel Remote when you give it away or stop using it.") : L("This computer is not set up yet.")) {
                    if !settings.removalPending, enrollment.current != nil || remote.host != nil {
                        Button(L("Remove this computer from Mixel Remote"), role: .destructive) { confirmRemove = true }.disabled(enrollment.working)
                    }
                }
                if !removalResult.isEmpty || !enrollment.error.isEmpty {
                    VStack(alignment: .leading, spacing: 4) { MessageLine(removalResult, error: false); MessageLine(enrollment.error) }.padding(.horizontal, 16).padding(.bottom, 10)
                }
            }
        }
    }

    private func permission(_ granted: Bool, request: @escaping () -> Void) -> some View {
        Group {
            if granted { Pill(L("Allowed"), symbol: "checkmark", foreground: Theme.sessionText, background: Theme.sessionSoft) }
            else { Button(L("Configure…"), action: request) }
        }
    }

    private var initials: String? {
        guard app.signedIn, let name = app.account.account?.displayName, !name.isEmpty else { return nil }
        let out = name.split(separator: " ").prefix(2).compactMap(\.first).map(String.init).joined().uppercased()
        return out.isEmpty ? nil : out
    }

    private var accountLine: String {
        guard app.signedIn, let profile = app.account.account else { return L("Sign in to Mixel") }
        var parts: [String] = []
        if profile.staff { parts.append(L("Mixel team account")) }
        else if profile.personal { parts.append(L("Personal account")) }
        else if let tenant = app.company.tenant { parts.append("\(tenant.name) · \(Format.role(tenant.role))") }
        else { parts.append(L("Mixel account")) }
        if profile.remembered { parts.append(L("Stays signed in on this computer")) }
        return parts.joined(separator: " · ")
    }

    // MARK: About and team

    private var about: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading(L("Connection & diagnostics"))
            ConsoleCard {
                VStack(alignment: .leading, spacing: 0) {
                    SettingRow(
                        title: L("Relay-only connections"),
                        help: L("Route all sessions through the Mixel relay instead of direct LAN/NAT paths (hides IP addresses).")
                    ) {
                        ConsoleSwitch(isOn: Binding(get: { settings.relayOnly }, set: { settings.setRelayOnly($0) }), label: L("Relay-only connections"))
                    }
                    SettingRow(
                        title: L("Unattended idle timeout"),
                        help: L("Automatically end unattended sessions after inactivity.")
                    ) {
                        Picker("", selection: Binding(get: { settings.idleTimeoutMinutes }, set: { settings.setIdleTimeout($0) })) {
                            Text(verbatim: L("Off")).tag(UInt32(0))
                            Text(verbatim: L("15 min")).tag(UInt32(15))
                            Text(verbatim: L("30 min")).tag(UInt32(30))
                            Text(verbatim: L("60 min")).tag(UInt32(60))
                        }
                        .labelsHidden()
                        .frame(width: 110)
                    }
                    SettingRow(
                        title: L("Send diagnostics"),
                        help: L("Save a sanitized log bundle (sign-in, signaling, connection path, performance) to attach to a support ticket.")
                    ) {
                        Button(L("Export diagnostics")) { settings.exportDiagnostics() }
                    }
                }
            }
            heading(L("About and legal"))
            ConsoleCard { AboutBlock(app: app).padding(16) }
        }
    }

    private var team: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading(L("Mixel team"), L("Rollout for many computers"))
            ConsoleCard { RolloutSection(enrollment: enrollment, embedded: true) }
        }
    }
}
#endif
