#if os(macOS)
import SwiftUI

/// The device administration dialogs of the console (rename, notes, remove), as in the redesign:
/// a title, one line of what it does, the field, and Cancel / the action. The server decides who may do
/// it; its answer (by hint) shows under the field.
struct ConsoleDialog<Content: View>: View {
    let title: String
    var symbol: String?
    var danger = false
    let text: String
    let confirm: String
    let enabled: Bool
    let error: String
    let working: Bool
    let cancel: () -> Void
    let submit: () -> Void
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let symbol {
                Image(systemName: symbol).font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(danger ? Theme.brand : Color.primary)
                    .frame(width: 38, height: 38)
                    .background(danger ? Theme.brandSoft : Theme.chrome, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .accessibilityHidden(true)
            }
            Text(verbatim: title).font(.system(size: 17, weight: .semibold)).accessibilityAddTraits(.isHeader)
            if !text.isEmpty { Text(verbatim: text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            content()
            MessageLine(error)
            HStack {
                Spacer()
                Button(L("Cancel"), action: cancel).keyboardShortcut(.cancelAction)
                Button(action: submit) { Text(verbatim: confirm).fontWeight(.semibold) }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.brand)
                    .keyboardShortcut(.defaultAction)
                    .disabled(!enabled || working)
            }
            .padding(.top, 4)
        }
        .padding(22)
        .frame(width: 420)
        .background(Theme.page)
    }
}

/// "Rename device": shown to everyone in the company.
struct RenameDeviceDialog: View {
    @ObservedObject var model: DevicesModel
    let device: Device
    let company: String
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var working = false
    @State private var error = ""

    init(model: DevicesModel, device: Device, company: String) {
        self.model = model
        self.device = device
        self.company = company
        _name = State(initialValue: device.name)
    }

    private var trimmed: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        ConsoleDialog(title: L("Rename device"), text: company.isEmpty ? L("Shown to everyone who sees this device.") : L("Shown to everyone in %@.", company),
                      confirm: L("Rename"), enabled: !trimmed.isEmpty && trimmed != device.name, error: error, working: working, cancel: { dismiss() }, submit: {
            working = true
            Task {
                if await model.rename(device, to: String(trimmed.prefix(60))) { dismiss() } else { error = model.error }
                working = false
            }
        }) {
            FieldRow(L("Device name")) {
                TextField(text: $name) { Text(verbatim: L("Device name")) }
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: name) { if $0.count > 60 { name = String($0.prefix(60)) } }
            }
        }
    }
}

/// "Notes for <device>": visible to the company's technicians, shown under the device name.
struct DeviceNotesDialog: View {
    @ObservedObject var model: DevicesModel
    let device: Device
    @Environment(\.dismiss) private var dismiss
    @State private var note: String
    @State private var working = false
    @State private var error = ""

    init(model: DevicesModel, device: Device) {
        self.model = model
        self.device = device
        _note = State(initialValue: model.note(of: device))
    }

    var body: some View {
        ConsoleDialog(title: L("Notes for %@", device.name), text: L("Visible to your company’s technicians, shown under the device name."),
                      confirm: L("Save"), enabled: note.count <= 500, error: error, working: working, cancel: { dismiss() }, submit: {
            working = true
            Task {
                if await model.setNote(device, to: note) { dismiss() } else { error = model.error }
                working = false
            }
        }) {
            FieldRow(L("Note")) {
                VStack(alignment: .leading, spacing: 4) {
                    TextEditor(text: $note)
                        .font(.body)
                        .frame(height: 92)
                        .padding(4)
                        .background(Theme.page, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).stroke(Theme.hairline))
                    Text(verbatim: L("E.g. location, owner role, maintenance window.")).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

/// "Remove <device>?": says what happens, with the device's ID (the server checks it too).
struct RemoveDeviceDialog: View {
    @ObservedObject var model: DevicesModel
    let device: Device
    let inSession: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var working = false
    @State private var error = ""

    var body: some View {
        ConsoleDialog(title: L("Remove “%@”?", device.name), symbol: "trash", danger: true,
                      text: L("%@ (%@) is signed out of Mixel Remote and can no longer be reached. You can add it again later from that computer.", device.name, Format.remoteId(device.connectionId))
                            + (inSession ? " " + L("Its running session ends at once.") : ""),
                      confirm: L("Remove device"), enabled: true, error: error, working: working, cancel: { dismiss() }, submit: {
            working = true
            Task {
                if await model.remove(device) { dismiss() } else { error = model.error }
                working = false
            }
        }) { EmptyView() }
    }
}

/// Shown after a technician's remote session ends: asks if the technician wants to save this device
/// for next time, requiring Device Name, Company, and User.
struct SaveConnectedDeviceDialog: View {
    @ObservedObject var app: AppModel
    @ObservedObject var model: DevicesModel
    let connectionId: UInt32
    @Environment(\.dismiss) private var dismiss
    @State private var deviceName: String = ""
    @State private var companyName: String = ""
    @State private var userName: String = ""
    @State private var selectedClientId: String?
    @State private var companies: [EnrollmentModel.EnrollmentCompany] = []
    @State private var working = false
    @State private var error = ""

    private var cleanDevice: String { deviceName.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var cleanCompany: String { companyName.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var cleanUser: String { userName.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var valid: Bool { !cleanDevice.isEmpty && !cleanCompany.isEmpty && !cleanUser.isEmpty }

    var body: some View {
        ConsoleDialog(
            title: L("Save this device for next time?"),
            symbol: "externaldrive.badge.checkmark",
            text: L("Session with %@ ended. Fill in the Device Name, Company, and User to save this computer to your Devices list.", Format.remoteId(connectionId)),
            confirm: L("Save device"),
            enabled: valid,
            error: error,
            working: working,
            cancel: { dismiss() },
            submit: {
                working = true
                Task {
                    if await model.saveConnectedDevice(
                        connectionId: connectionId,
                        deviceName: String(cleanDevice.prefix(60)),
                        companyName: String(cleanCompany.prefix(60)),
                        userName: String(cleanUser.prefix(80)),
                        targetClient: selectedClientId
                    ) {
                        dismiss()
                    } else {
                        error = model.error
                    }
                    working = false
                }
            }
        ) {
            VStack(alignment: .leading, spacing: 10) {
                FieldRow(L("Connection ID")) {
                    Text(verbatim: Format.remoteId(connectionId))
                        .font(.system(size: 15, weight: .semibold, design: .monospaced))
                        .padding(.vertical, 4)
                }
                FieldRow(L("Device name *")) {
                    TextField(text: $deviceName, prompt: Text(verbatim: L("e.g. Reception MacBook Pro"))) { Text(verbatim: L("Device name")) }
                        .textFieldStyle(.roundedBorder)
                }
                FieldRow(L("Company *")) {
                    VStack(alignment: .leading, spacing: 6) {
                        if !companies.isEmpty {
                            Picker(selection: Binding(
                                get: { selectedClientId },
                                set: { nextId in
                                    selectedClientId = nextId
                                    if let nextId, let found = companies.first(where: { $0.clientId == nextId }) {
                                        companyName = found.clientName
                                    }
                                }
                            )) {
                                Text(verbatim: L("Choose a company or type below…")).tag(String?.none)
                                ForEach(companies) { company in
                                    Text(verbatim: company.clientName).tag(Optional(company.clientId))
                                }
                            } label: { EmptyView() }
                            .labelsHidden()
                        } else if !model.groups.isEmpty {
                            Picker(selection: Binding(
                                get: { companyName },
                                set: { if !$0.isEmpty { companyName = $0 } }
                            )) {
                                Text(verbatim: L("Choose a company/group or type below…")).tag("")
                                ForEach(model.groups) { group in
                                    Text(verbatim: group.name).tag(group.name)
                                }
                            } label: { EmptyView() }
                            .labelsHidden()
                        }
                        TextField(text: $companyName, prompt: Text(verbatim: L("Company name"))) { Text(verbatim: L("Company")) }
                            .textFieldStyle(.roundedBorder)
                            .onChange(of: companyName) { next in
                                if let sel = selectedClientId,
                                   let found = companies.first(where: { $0.clientId == sel }),
                                   found.clientName != next {
                                    selectedClientId = nil
                                }
                            }
                    }
                }
                FieldRow(L("User *")) {
                    TextField(text: $userName, prompt: Text(verbatim: L("e.g. Jane Doe"))) { Text(verbatim: L("User")) }
                        .textFieldStyle(.roundedBorder)
                }
            }
        }
        .task {
            let existing = model.allDevices.first { $0.connectionId == connectionId }
            let admin = existing.flatMap { model.adminRow($0) }
            if let existingName = existing?.name, !existingName.isEmpty, existingName != Format.remoteId(connectionId) {
                deviceName = existingName
            } else {
                deviceName = ""
            }
            if let grp = existing?.groupName ?? admin?.groupName, !grp.isEmpty {
                companyName = grp
            } else if let comp = admin?.companyName, !comp.isEmpty, comp != "Mixel IT and Corporate Services (Remote devices)" {
                companyName = comp
            } else if let tenantName = model.tenant?.name, !tenantName.isEmpty {
                companyName = tenantName
            }
            if let note = admin?.note, let range = note.range(of: "User: ") {
                userName = String(note[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            }
            if app.account.account?.staff == true {
                if let loaded: [EnrollmentModel.EnrollmentCompany] = try? await app.engine.run("enrollment_companies") {
                    companies = loaded
                    if let match = loaded.first(where: { $0.clientName.caseInsensitiveCompare(companyName) == .orderedSame }) {
                        selectedClientId = match.clientId
                    }
                }
            }
        }
    }
}
#endif

/// "Unattended access, Who may connect…": the company's technicians with a switch each (owners and administrators only).
struct UnattendedWhoDialog: View {
    @ObservedObject var model: DevicesModel
    let device: Device
    @Environment(\.dismiss) private var dismiss
    @State private var rows: [GrantRow]?
    @State private var loaded = false
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(verbatim: L("Who may connect…")).font(.headline)
            Text(verbatim: L("People who may connect to %@ (%@) without anyone there being asked.", device.name, Format.remoteId(device.connectionId)))
                .font(.callout).foregroundStyle(.secondary)
            if !loaded {
                ProgressView().controlSize(.small)
            } else if let rows {
                if rows.isEmpty { Text(verbatim: L("This company has no other technicians.")).foregroundStyle(.secondary) }
                ForEach(rows) { row in
                    Toggle(isOn: Binding(get: { row.granted || row.automatic }, set: { on in change(row, on) })) {
                        Text(verbatim: row.name + " · " + row.role + (row.automatic ? " · " + L("automatic (company setting)") : ""))
                    }.disabled(row.automatic)
                }
            } else {
                Text(verbatim: error.isEmpty ? L("The list could not be read.") : error)
            }
            if loaded, rows != nil, !error.isEmpty { Text(verbatim: error).foregroundStyle(.red).font(.callout) }
            HStack { Spacer(); Button(L("Done")) { dismiss() }.keyboardShortcut(.defaultAction) }
        }
        .padding(20).frame(minWidth: 380)
        .task {
            rows = await model.unattendedWho(device)
            if rows == nil { error = model.error }
            loaded = true
        }
    }

    private func change(_ row: GrantRow, _ on: Bool) {
        Task {
            if await model.setUnattendedGrant(device, user: row.userId, granted: on) {
                error = ""
                if let index = rows?.firstIndex(where: { $0.userId == row.userId }) { rows?[index].granted = on }
            } else {
                error = model.error
            }
        }
    }
}
