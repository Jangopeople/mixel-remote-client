import SwiftUI

/// Settings (on the Mac also the Settings window, ⌘,).
public struct SettingsView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var settings: SettingsModel
    @ObservedObject var enrollment: EnrollmentModel
    @State private var confirmRemove = false
    @State private var removalResult = ""

    public init(app: AppModel) {
        self.app = app
        settings = app.settings
        enrollment = app.enrollment
    }

    public var body: some View {
        Form {
            Section {
                Toggle(isOn: Binding(get: { settings.startup?.remembered ?? false }, set: { settings.setStaySignedIn($0) })) {
                    Text(verbatim: L("Stay signed in on this computer"))
                }
                .disabled(!app.signedIn)
                if !app.isPhone {
                    Toggle(isOn: Binding(get: { settings.startup?.autostart ?? false }, set: { settings.setAutostart($0) })) {
                        Text(verbatim: L("Start Mixel Remote when this computer starts"))
                    }
                }
                Picker(selection: Binding(get: { settings.language }, set: { settings.setLanguage($0) })) {
                    Text(verbatim: L("Automatic (this computer’s language)")).tag("auto")
                    Text(verbatim: "English").tag("en")
                    Text(verbatim: "Deutsch").tag("de")
                    Text(verbatim: "Français").tag("fr")
                    Text(verbatim: "Italiano").tag("it")
                } label: { Text(verbatim: L("Language")) }
            } header: { Text(verbatim: L("General")) }

            Section {
                LabeledContent {
                    Text(verbatim: Format.remoteId(app.remote.host ?? enrollment.current?.connectionId)).monospacedDigit().textSelection(.enabled)
                } label: { Text(verbatim: L("Connection ID")) }
                if settings.removalPending {
                    MessageLine(L("This computer is being removed from Mixel Remote. It is removed as soon as it is online."), error: false)
                } else if enrollment.current != nil || app.remote.host != nil {
                    Button(L("Remove this computer from Mixel Remote"), role: .destructive) { confirmRemove = true }
                        .disabled(enrollment.working)
                } else {
                    Text(verbatim: L("This computer is not set up yet.")).foregroundStyle(.secondary)
                }
                MessageLine(removalResult, error: false)
                MessageLine(enrollment.error)
            } header: { Text(verbatim: app.isPhone ? L("This phone") : L("This computer")) }

            if !app.isPhone {
                Section {
                    permissionRow(L("Screen recording"), granted: settings.screenAccess, detail: L("Needed to share this screen with a technician.")) { settings.requestScreenAccess() }
                    permissionRow(L("Accessibility"), granted: app.remote.inputAccess, detail: L("Needed so a technician can use keyboard and mouse here.")) { settings.requestInputAccess() }
                } header: { Text(verbatim: L("macOS permissions")) }

                if let printing = settings.printing, printing.supported {
                    Section {
                        Text(verbatim: L("Adds the printer “Mixel Remote Printer” to this computer. What is printed on it during a session arrives at the technician, who prints it on a printer there. Needs administrator approval on this computer."))
                            .font(.callout).foregroundStyle(.secondary)
                        Toggle(isOn: Binding(get: { printing.installed }, set: { settings.setPrinting($0) })) {
                            Text(verbatim: L("Remote printing"))
                        }
                    } header: { Text(verbatim: L("Remote printing")) }
                }
            }

            if !app.isPhone, app.account.account?.staff == true {
                RolloutSection(enrollment: enrollment)
            }

            if !app.isPhone, let signIn = settings.signInScreen, app.account.account?.staff == true || signIn.connectionId != nil {
                SignInScreenSection(app: app, settings: settings, enrollment: enrollment, status: signIn)
            }

            Section {
                LabeledContent { Text(verbatim: app.info.version) } label: { Text(verbatim: L("Version")) }
                if app.isPhone {
                    Text(verbatim: L("Mixel Remote for iPhone and iPad updates through the App Store or TestFlight.")).font(.callout).foregroundStyle(.secondary)
                } else {
                    Toggle(isOn: $settings.autoUpdate) { Text(verbatim: L("Install updates automatically")) }
                    HStack {
                        Button(L("Check for updates")) { settings.checkForUpdate() }.disabled(settings.installing)
                        if let update = settings.update {
                            Text(verbatim: L("Version %@ is available.", update.version)).foregroundStyle(.secondary)
                            if update.installable {
                                Button(L("Update now")) { settings.installUpdate() }.buttonStyle(PrimaryButtonStyle()).disabled(settings.installing)
                            } else {
                                Button(L("Download")) { settings.openStorePage() }
                            }
                        } else if settings.updateChecked {
                            Text(verbatim: L("This is the newest version.")).foregroundStyle(.secondary)
                        }
                    }
                    MessageLine(settings.notice, error: false)
                }
            } header: { Text(verbatim: L("Updates")) }

            Section {
                AboutBlock(app: app)
            } header: { Text(verbatim: L("About and legal")) }
            MessageLine(settings.error)
        }
        .formStyle(.grouped)
        .background(Theme.background)
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

    private func permissionRow(_ title: String, granted: Bool, detail: String, request: @escaping () -> Void) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: title)
                Text(verbatim: detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if granted {
                Label(L("Allowed"), systemImage: "checkmark.circle.fill").foregroundStyle(Theme.success)
            } else {
                Button(L("Configure…"), action: request)
            }
        }
    }
}

/// Version and the legal texts.
public struct AboutBlock: View {
    @ObservedObject var app: AppModel
    public init(app: AppModel) { self.app = app }
    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                MixelMark(size: 44)
                VStack(alignment: .leading) {
                    Text(verbatim: "Mixel Remote").font(.title3.weight(.semibold))
                    Text(verbatim: L("Version %@", app.info.version)).font(.callout).foregroundStyle(.secondary)
                }
            }
            Text(verbatim: "© Mixel IT and Corporate Services").font(.caption).foregroundStyle(.secondary)
            ViewThatFits(in: .horizontal) {
                HStack { links }
                VStack(alignment: .leading) { links }
            }
        }
    }

    @ViewBuilder private var links: some View {
        Button(L("Terms of Service")) { app.settings.openLegal("terms") }.linkButton()
        Button(L("Privacy Policy")) { app.settings.openLegal("privacy") }.linkButton()
        Button(L("Data Processing Agreement")) { app.settings.openLegal("dpa") }.linkButton()
        Button(L("Sub-processors")) { app.settings.openLegal("subprocessors") }.linkButton()
    }
}

/// Access at this Mac's sign-in screen, before anyone signs in (Mixel team). Typing there needs
/// Apple's virtual keyboard entitlement: until Apple grants it, the app says so and offers nothing.
struct SignInScreenSection: View {
    @ObservedObject var app: AppModel
    @ObservedObject var settings: SettingsModel
    @ObservedObject var enrollment: EnrollmentModel
    let status: SignInScreenStatus
    @State private var company = ""
    @State private var name = ""

    var body: some View {
        Section {
            switch status.availability {
            case .waitingForApple, .notHere:
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "hourglass").foregroundStyle(Theme.warning).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(verbatim: L("Waiting for Apple")).font(.callout.weight(.semibold))
                        Text(verbatim: L("Typing at the sign-in screen needs Apple’s approval of the virtual keyboard for Mixel Remote. Mixel has asked Apple for it; this option turns on by itself with the update that includes it."))
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
                if let id = status.connectionId {
                    LabeledContent { Text(verbatim: Format.remoteId(id)).monospacedDigit() } label: { Text(verbatim: L("Sign-in screen Remote ID")) }
                    Button(L("Turn off sign-in screen access"), role: .destructive) { settings.disableSignInScreen() }.disabled(settings.signInWorking)
                }
            case .ready:
                if let id = status.connectionId {
                    LabeledContent { Text(verbatim: Format.remoteId(id)).monospacedDigit().textSelection(.enabled) } label: { Text(verbatim: L("Sign-in screen Remote ID")) }
                    if let name = status.name { LabeledContent { Text(verbatim: name) } label: { Text(verbatim: L("Name")) } }
                    HStack {
                        Button(L("Update")) { settings.updateSignInScreen() }
                        Button(L("Turn off sign-in screen access"), role: .destructive) { settings.disableSignInScreen() }
                    }
                    .disabled(settings.signInWorking)
                } else {
                    Text(verbatim: L("A technician can then reach this Mac at its sign-in screen, before anyone signs in. macOS asks for an administrator twice, and once at the sign-in screen for Screen Recording."))
                        .font(.callout).foregroundStyle(.secondary)
                    Picker(selection: $company) {
                        Text(verbatim: L("Choose a company")).tag("")
                        ForEach(enrollment.companies) { Text(verbatim: $0.clientName).tag($0.clientId) }
                    } label: { Text(verbatim: L("Company")) }
                    TextField(text: $name) { Text(verbatim: L("Computer name")) }
                    Button(L("Turn on sign-in screen access")) { settings.enableSignInScreen(company: company, name: name) }
                        .disabled(company.isEmpty || name.trimmingCharacters(in: .whitespaces).isEmpty || settings.signInWorking)
                }
            }
            if settings.signInWorking { ProgressView().controlSize(.small) }
            MessageLine(settings.signInMessage, error: false)
        } header: { Text(verbatim: L("Sign-in screen")) }
        .task {
            if status.availability == .ready, status.connectionId == nil, enrollment.companies.isEmpty { enrollment.loadTeamRecords() }
            if name.isEmpty { name = enrollment.name }
        }
    }
}

/// Mixel team: a rollout token per company and the one-line command for many computers.
struct RolloutSection: View {
    @ObservedObject var enrollment: EnrollmentModel
    @State private var company = ""
    @State private var label = ""
    @State private var days = 30
    @State private var computers = 50

    var body: some View {
        Section {
            Picker(selection: $company) {
                Text(verbatim: L("Choose a company")).tag("")
                ForEach(enrollment.companies) { Text(verbatim: $0.clientName).tag($0.clientId) }
            } label: { Text(verbatim: L("Company")) }
                .onChange(of: company) { value in enrollment.rolloutCreated = nil; enrollment.loadRollouts(company: value) }
            TextField(text: $label) { Text(verbatim: L("Name")) }
            Stepper(value: $computers, in: 1...2000) { Text(verbatim: L("Computers: %d", computers)) }
            Stepper(value: $days, in: 1...365) { Text(verbatim: L("Valid for %d days", days)) }
            Button(L("Create rollout command")) { enrollment.createRollout(company: company, label: label, days: days, computers: computers) }
                .disabled(company.isEmpty || label.isEmpty || enrollment.working)
            if let created = enrollment.rolloutCreated {
                ForEach([("Windows", created.windowsCommand), ("macOS", created.macosCommand), ("Linux", created.linuxCommand)], id: \.0) { item in
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(verbatim: item.0).font(.caption.weight(.semibold))
                            Text(verbatim: item.1).font(.caption.monospaced()).textSelection(.enabled)
                        }
                        Spacer()
                        Button { Pasteboard.copy(item.1) } label: { Label(L("Copy command"), systemImage: "doc.on.doc") }.labelStyle(.iconOnly)
                    }
                }
            }
            ForEach(enrollment.rollouts) { token in
                HStack {
                    VStack(alignment: .leading) {
                        Text(verbatim: token.label)
                        Text(verbatim: L("%d of %d computers", token.uses, token.maxUses)).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if token.revokedAt != nil {
                        Text(verbatim: L("Revoked")).foregroundStyle(.secondary)
                    } else {
                        Button(L("Revoke"), role: .destructive) { enrollment.revokeRollout(token, company: company) }
                    }
                }
            }
        } header: { Text(verbatim: L("Rollout for many computers")) }
        .task { if enrollment.companies.isEmpty { enrollment.loadTeamRecords() } }
    }
}
