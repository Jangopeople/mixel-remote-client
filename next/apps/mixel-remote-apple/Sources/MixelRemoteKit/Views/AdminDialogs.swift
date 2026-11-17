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
#endif
