import SwiftUI

/// Set up this computer once: the path depends on the account (company, Mixel team, personal).
public struct EnrollmentCard: View {
    @ObservedObject var app: AppModel
    @ObservedObject var enrollment: EnrollmentModel
    @State private var company = ""
    @State private var group: String?

    public init(app: AppModel, enrollment: EnrollmentModel) {
        self.app = app
        self.enrollment = enrollment
    }

    public var body: some View {
        Card {
            CardTitle(app.isPhone ? L("Set up this phone") : L("Set up this computer"), symbol: "checkmark.shield")
            switch enrollment.path {
            case .company:
                Text(verbatim: L("Add this computer to %@ so you can connect from it and to it.", app.company.tenant?.name ?? "")).foregroundStyle(.secondary)
                FieldRow(L("Name of this computer")) { TextField(text: $enrollment.name) { Text(verbatim: L("Device name")) }.textFieldStyle(.roundedBorder) }
                if !app.devices.groups.isEmpty {
                    Picker(selection: $group) {
                        Text(verbatim: L("No group")).tag(String?.none)
                        ForEach(app.devices.groups) { Text(verbatim: $0.name).tag(Optional($0.groupId)) }
                    } label: { Text(verbatim: L("Group")) }
                }
                Button(L("Add this computer")) { enrollment.addToCompany(group: group) }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(enrollment.working || !app.devices.addBlocked.isEmpty)
                if !app.devices.addBlocked.isEmpty { MessageLine(app.devices.addBlocked, error: false) }
            case .companyMember:
                Text(verbatim: L("Devices you add appear here. Ask an administrator of your company for access to other devices.")).foregroundStyle(.secondary)
            case .personal:
                Text(verbatim: L("Register this computer with your personal account.")).foregroundStyle(.secondary)
                FieldRow(L("Device name")) { TextField(text: $enrollment.name) { Text(verbatim: L("Device name")) }.textFieldStyle(.roundedBorder) }
                Button(L("Register this computer")) { enrollment.registerPersonal() }
                    .buttonStyle(PrimaryButtonStyle()).disabled(enrollment.working)
            case .team:
                Text(verbatim: L("Enroll this computer against its existing ISM record, or add it to a company.")).foregroundStyle(.secondary)
                HStack {
                    TextField(text: $enrollment.recordSearch) { Text(verbatim: L("Device name")) }.textFieldStyle(.roundedBorder)
                    Button(L("Find device")) { enrollment.loadTeamRecords() }
                }
                ForEach(enrollment.records) { record in
                    HStack {
                        Label(record.name, systemImage: Format.osSymbol(record.os))
                        Spacer()
                        Button(L("Enroll this computer")) { enrollment.enroll(record: record) }.disabled(enrollment.working)
                    }
                }
                if !enrollment.companies.isEmpty {
                    Divider()
                    Text(verbatim: L("This computer is not in the list? Add it")).font(.callout.weight(.medium))
                    Picker(selection: $company) {
                        ForEach(enrollment.companies) { Text(verbatim: $0.clientName).tag($0.clientId) }
                    } label: { Text(verbatim: L("Company")) }
                    FieldRow(L("Computer name")) { TextField(text: $enrollment.name) { Text(verbatim: L("Computer name")) }.textFieldStyle(.roundedBorder) }
                    Button(L("Add and enroll this computer")) { enrollment.addAndEnroll(company: company) }
                        .disabled(company.isEmpty || enrollment.working)
                }
            case .none:
                Text(verbatim: L("Create a company to manage your devices and team, or a personal space for your own devices.")).foregroundStyle(.secondary)
                Button(L("Create a company")) { app.section = .account }.buttonStyle(PrimaryButtonStyle())
            }
            if enrollment.working { ProgressView().controlSize(.small) }
            MessageLine(enrollment.error)
        }
    }
}

/// The person at this computer decides about a connection request: which screen, and what the
/// technician may do. Nothing is allowed before Accept.
public struct ConsentSheet: View {
    @ObservedObject var app: AppModel
    @ObservedObject var host: HostModel

    public init(app: AppModel) {
        self.app = app
        host = app.host
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                MixelMark(size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: L("Someone wants to connect")).font(.title2.weight(.semibold))
                    Text(verbatim: L("Device %@ requests to connect. Its enrolled identity has been verified with Mixel.", Format.remoteId(app.remote.peer)))
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            if !host.screenAccess || host.sources.isEmpty {
                MessageLine(L("Allow screen recording for Mixel Remote in System Settings to share a screen."), error: false)
                Button(L("Enable screen sharing in macOS…")) { host.requestScreenAccess() }
            } else {
                Picker(selection: $host.source) {
                    ForEach(host.sources) { Text(verbatim: $0.name).tag($0.id) }
                } label: { Text(verbatim: L("Screen to share")) }
                if host.selected?.fullDisplay == true {
                    Toggle(isOn: $host.confirmDisplay) { Text(verbatim: L("I confirm sharing this whole display")) }
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                Toggle(isOn: $host.allowControl) { Label(L("Keyboard & mouse"), systemImage: "computermouse") }
                    .disabled(!host.controlAvailable)
                if !app.remote.inputAccess {
                    HStack {
                        Text(verbatim: L("Enable Accessibility in macOS to allow keyboard and mouse control.")).font(.callout).foregroundStyle(.secondary)
                        Button(L("Enable keyboard control in macOS…")) { host.requestInputAccess() }.linkButton()
                    }
                }
                Toggle(isOn: $host.allowClipboard) { Label(L("Clipboard"), systemImage: "doc.on.clipboard") }
                Toggle(isOn: $host.allowFiles) { Label(L("Files"), systemImage: "folder") }
                Toggle(isOn: $host.allowChat) { Label(L("Chat"), systemImage: "bubble.left.and.bubble.right") }
                Toggle(isOn: $host.allowRecording) { Label(L("Allow recording of this session"), systemImage: "record.circle") }
            }
            #if os(macOS)
            .toggleStyle(.checkbox)
            #endif
            MessageLine(host.error)
            MessageLine(app.error)
            HStack {
                Spacer()
                Button(L("Decline")) { host.decide(accept: false) }
                    .keyboardShortcut(.cancelAction)
                Button(host.allowControl && host.controlAvailable ? L("Allow with control") : L("Allow view only")) { host.decide(accept: true) }
                    .buttonStyle(PrimaryButtonStyle())
                    .keyboardShortcut(.defaultAction)
                    .disabled(!host.canAccept)
            }
        }
        .padding(24)
        .frame(width: 480)
        .background(Theme.background)
    }
}
