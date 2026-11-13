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

            Section {
                LabeledContent { Text(verbatim: "\(app.info.version) · \(L("Preview for testing"))") } label: { Text(verbatim: L("Version")) }
                HStack {
                    Button(L("Check for updates")) { settings.checkForUpdate() }
                    if let update = settings.update {
                        Text(verbatim: L("Version %@ is available.", update.version)).foregroundStyle(.secondary)
                        Button(L("Download")) { settings.openStorePage() }
                    } else if settings.updateChecked {
                        Text(verbatim: L("This is the newest version.")).foregroundStyle(.secondary)
                    }
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

/// Version, the legal texts and the preview note.
public struct AboutBlock: View {
    @ObservedObject var app: AppModel
    public init(app: AppModel) { self.app = app }
    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                MixelMark(size: 44)
                VStack(alignment: .leading) {
                    Text(verbatim: "Mixel Remote").font(.title3.weight(.semibold))
                    Text(verbatim: L("Native preview %@ · for testing only, not for customers yet.", app.info.version)).font(.callout).foregroundStyle(.secondary)
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
