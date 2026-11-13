import SwiftUI

/// Devices: every device the account may reach, with its status and the reason when it cannot be
/// reached; for companies also groups, the Manage menu, Add this computer and Add other computers.
public struct DevicesView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var model: DevicesModel
    @State private var renaming: Device?
    @State private var moving: Device?
    @State private var removing: Device?
    @State private var addingOthers = false
    @State private var addingThis = false

    public init(app: AppModel) {
        self.app = app
        model = app.devices
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    PageHeader(L("Devices"), subtitle: model.tenant.map { $0.name } ?? "")
                    Spacer()
                    if model.tenant != nil, app.signedIn {
                        if model.manages, !model.registeredHere {
                            Button(L("Add this computer")) { addingThis = true }
                                .disabled(!model.addBlocked.isEmpty).help(model.addBlocked)
                        }
                        if model.deploys {
                            Button(L("Add other computers")) { addingOthers = true }
                                .disabled(!model.addBlocked.isEmpty).help(model.addBlocked)
                        }
                    }
                    Button { Task { await model.load() } } label: { Label(L("Refresh devices"), systemImage: "arrow.clockwise") }
                        .labelStyle(.iconOnly)
                        .disabled(!app.signedIn || model.loading)
                        .keyboardShortcut("r", modifiers: .command)
                }
                HStack {
                    TextField(text: $model.search, prompt: Text(verbatim: L("Device name"))) { Text(verbatim: L("Device name")) }
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { Task { await model.load() } }
                        .frame(maxWidth: 320)
                    if model.manages, model.groupsOn, !model.groups.isEmpty {
                        Picker(selection: $model.groupFilter) {
                            Text(verbatim: L("All groups")).tag(String?.none)
                            ForEach(model.groups) { Text(verbatim: $0.name).tag(Optional($0.groupId)) }
                        } label: { Text(verbatim: L("Group")) }
                            .frame(maxWidth: 220)
                            .onChange(of: model.groupFilter) { _ in Task { await model.load() } }
                    }
                    Button(L("Search")) { Task { await model.load() } }.disabled(!app.signedIn || model.loading)
                }
                if !model.addBlocked.isEmpty, model.manages { MessageLine(planNotice, error: false) }
                if app.signedIn, !app.remote.connected, app.remote.phase != "closing", !app.enrollment.needsSetup { connectionBanner }
                MessageLine(model.error)
            }
            .padding([.horizontal, .top], 24)
            .padding(.bottom, 12)
            list
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.background)
        .sheet(item: $renaming) { device in
            FormSheet(title: L("Rename device"), fields: [.init(id: "name", label: L("Device name"), value: device.name)], confirm: L("Rename")) { values in
                await model.rename(device, to: values["name"] ?? "") ? nil : model.error
            }
        }
        .sheet(item: $moving) { device in MoveSheet(model: model, device: device) }
        .sheet(isPresented: $addingOthers) { DeployLinkSheet(model: model) }
        .sheet(isPresented: $addingThis) {
            ScrollView { EnrollmentCard(app: app, enrollment: app.enrollment).padding(20) }.frame(minWidth: 460, minHeight: 320)
        }
        .confirmationDialog(L("Remove this device?"), isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }), presenting: removing) { device in
            Button(L("Remove %@", device.name), role: .destructive) { Task { _ = await model.remove(device) } }
        } message: { _ in
            Text(verbatim: L("Its running sessions end at once. It can be added again later."))
        }
    }

    private var planNotice: String {
        if !model.writable { return L("This company is read-only. You can still remove devices; adding devices and other changes need an active plan.") }
        return L("Device limit of your plan reached. Remove a device or upgrade the plan to add more.")
    }

    private var connectionBanner: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "wifi.exclamationmark").foregroundStyle(Theme.warning)
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: app.remote.phase == "error" ? L("This computer lost its connection") : L("This computer is not connected yet")).font(.callout.weight(.semibold))
                Text(verbatim: L("Mixel Remote reconnects by itself. Device status appears once it is connected; you can still choose Connect.")).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            Button(L("Reconnect now")) { app.goOnline() }
        }
        .padding(12)
        .background(Theme.warning.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder private var list: some View {
        if model.devices.isEmpty {
            VStack(spacing: 10) {
                Spacer()
                Image(systemName: "laptopcomputer.and.iphone").font(.system(size: 44)).foregroundStyle(.secondary)
                Text(verbatim: emptyTitle).font(.title3.weight(.semibold))
                Text(verbatim: emptyDetail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
                if !app.signedIn { Button(L("Sign in to Mixel →")) { app.section = .account }.linkButton() }
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(24)
        } else {
            List {
                if !model.favoriteDevices.isEmpty {
                    Section { ForEach(model.favoriteDevices) { row($0) } } header: { Text(verbatim: L("Favorites")) }
                }
                if !model.recentDevices.isEmpty {
                    Section { ForEach(model.recentDevices) { row($0) } } header: { Text(verbatim: L("Recent connections")) }
                }
                Section {
                    ForEach(model.devices) { row($0) }
                } header: { if !model.favoriteDevices.isEmpty || !model.recentDevices.isEmpty { Text(verbatim: L("All devices")) } }
                if model.page > 0 || model.hasNext {
                    HStack {
                        Button(L("Previous")) { model.previousPage() }.disabled(model.page == 0 || model.loading)
                        Spacer()
                        Text(verbatim: model.message).font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        Button(L("Next")) { model.nextPage() }.disabled(!model.hasNext || model.loading)
                    }
                }
            }
            #if os(macOS)
            .listStyle(.inset(alternatesRowBackgrounds: false))
            #endif
        }
    }

    private func row(_ device: Device) -> some View {
        DeviceRow(app: app, model: model, device: device, rename: { renaming = $0 }, move: { moving = $0 }, remove: { removing = $0 })
    }

    private var emptyTitle: String {
        if model.loading { return L("Loading your devices…") }
        if !app.signedIn { return L("Your devices belong here") }
        if !model.search.isEmpty { return L("No matching devices") }
        if model.tenant != nil, model.groupFilter != nil { return L("No devices in this group") }
        return L("No devices to display")
    }

    private var emptyDetail: String {
        if !app.signedIn { return L("Sign in to see the devices available to your account.") }
        if !model.search.isEmpty { return L("Try another device name or clear the search.") }
        if model.tenant != nil {
            return model.manages ? L("Add this computer, or add other computers with a deploy link.") : L("Devices you add appear here. Ask an administrator of your company for access to other devices.")
        }
        return L("Devices appear here after a Mixel team member enrolls them.")
    }
}

struct DeviceRow: View {
    @ObservedObject var app: AppModel
    @ObservedObject var model: DevicesModel
    let device: Device
    let rename: (Device) -> Void
    let move: (Device) -> Void
    let remove: (Device) -> Void

    var body: some View {
        let presence = model.presence(of: device)
        let blocked = model.connectBlocked(device)
        HStack(spacing: 14) {
            if model.tenant != nil {
                Button { model.toggleFavorite(device) } label: {
                    Image(systemName: model.isFavorite(device) ? "star.fill" : "star").foregroundStyle(model.isFavorite(device) ? Theme.warning : .secondary)
                }
                .buttonStyle(.plain)
                .help(model.isFavorite(device) ? L("Remove from favorites") : L("Add to favorites"))
                .accessibilityLabel(Text(verbatim: model.isFavorite(device) ? L("Remove from favorites") : L("Add to favorites")))
            }
            Image(systemName: Format.osSymbol(device.os))
                .font(.title2)
                .foregroundStyle(presence == .online || presence == .this ? Theme.brand : .secondary)
                .frame(width: 34)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: device.name).font(.body.weight(.semibold))
                HStack(spacing: 6) {
                    Text(verbatim: Format.os(device.os))
                    if let group = device.groupName { Text(verbatim: "· \(group)") }
                    Text(verbatim: "· \(Format.remoteId(device.connectionId))").monospacedDigit()
                }
                .font(.callout).foregroundStyle(.secondary)
            }
            .frame(minWidth: 180, alignment: .leading)
            Spacer(minLength: 12)
            VStack(alignment: .trailing, spacing: 2) {
                StatusBadge(badge(presence), model.label(presence))
                let reason = model.reason(for: device)
                if !reason.isEmpty {
                    Text(verbatim: reason).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.trailing).frame(maxWidth: 280, alignment: .trailing)
                }
            }
            if presence == .offline, !model.knownMacs(device).isEmpty {
                Button(L("Wake")) { model.wake(device) }
                    .help(L("Sends a wake signal on this computer's network (Wake-on-LAN)."))
                    .disabled(app.busy)
            }
            if model.manages {
                Menu {
                    Button(L("Rename…")) { rename(device) }.disabled(!model.writable)
                    if model.groupsOn { Button(L("Move to group…")) { move(device) }.disabled(!model.writable) }
                    Divider()
                    Button(L("Remove…"), role: .destructive) { remove(device) }
                } label: { Text(verbatim: L("Manage")) }
                    .fixedSize()
                    .accessibilityLabel(Text(verbatim: L("Manage %@", device.name)))
            }
            Button(L("Connect")) { model.connect(device) }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(!blocked.isEmpty)
                .help(blocked.isEmpty ? L("Connect to %@", device.name) : blocked)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .contain)
    }

    func badge(_ presence: DevicesModel.Presence) -> StatusBadge.Kind {
        switch presence {
        case .online, .this: .online
        case .offline: .offline
        case .checking: .busy
        case .unknown: .unknown
        }
    }
}

struct MoveSheet: View {
    @ObservedObject var model: DevicesModel
    let device: Device
    @State private var group: String?
    @State private var newGroup = ""
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(verbatim: L("Move %@ to a group", device.name)).font(.title2.weight(.semibold))
            Picker(selection: $group) {
                Text(verbatim: L("No group")).tag(String?.none)
                ForEach(model.groups) { Text(verbatim: $0.name).tag(Optional($0.groupId)) }
            } label: { Text(verbatim: L("Group")) }
            if model.deploys {
                HStack {
                    TextField(text: $newGroup) { Text(verbatim: L("New group")) }.textFieldStyle(.roundedBorder)
                    Button(L("Create group")) { Task { if await model.createGroup(newGroup) { newGroup = "" } } }.disabled(newGroup.isEmpty)
                }
            }
            MessageLine(model.error)
            HStack {
                Spacer()
                Button(L("Cancel")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("Move")) { Task { if await model.move(device, to: group) { dismiss() } } }
                    .buttonStyle(PrimaryButtonStyle()).keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(minWidth: 380)
        .onAppear { group = device.groupId }
    }
}

/// "Add other computers": a deploy link and the one-line command for each system.
struct DeployLinkSheet: View {
    @ObservedObject var model: DevicesModel
    @State private var label = ""
    @State private var days = 30
    @State private var computers = 50
    @State private var group: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(verbatim: L("Add other computers")).font(.title2.weight(.semibold))
            if let link = model.deployLink {
                Text(verbatim: L("Run the command on each computer (as administrator, or with your deployment tool). The link is shown only now.")).foregroundStyle(.secondary)
                command(L("Windows (PowerShell, as administrator)"), link.windowsCommand)
                command("macOS", link.macosCommand)
                command("Linux", link.linuxCommand)
                FieldRow(L("Deploy token")) { Text(verbatim: link.token).font(.callout.monospaced()).textSelection(.enabled) }
            } else {
                FieldRow(L("Name")) { TextField(text: $label) { Text(verbatim: L("Name")) }.textFieldStyle(.roundedBorder) }
                Stepper(value: $computers, in: 1...2000) { Text(verbatim: L("Computers: %d", computers)) }
                Stepper(value: $days, in: 1...365) { Text(verbatim: L("Valid for %d days", days)) }
                if model.groupsOn, !model.groups.isEmpty {
                    Picker(selection: $group) {
                        Text(verbatim: L("No group")).tag(String?.none)
                        ForEach(model.groups) { Text(verbatim: $0.name).tag(Optional($0.groupId)) }
                    } label: { Text(verbatim: L("Group")) }
                }
            }
            MessageLine(model.error)
            HStack {
                Spacer()
                Button(model.deployLink == nil ? L("Cancel") : L("Done")) { model.deployLink = nil; dismiss() }.keyboardShortcut(.cancelAction)
                if model.deployLink == nil {
                    Button(L("Create deploy link")) { Task { _ = await model.createDeployLink(label: label.isEmpty ? L("Rollout") : label, days: days, computers: computers, group: group) } }
                        .buttonStyle(PrimaryButtonStyle()).keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(22)
        .background(Theme.background)
        .frame(minWidth: 520)
    }

    func command(_ title: String, _ text: String) -> some View {
        FieldRow(title) {
            HStack(alignment: .top) {
                Text(verbatim: text).font(.callout.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                Button { Pasteboard.copy(text) } label: { Label(L("Copy command"), systemImage: "doc.on.doc") }.labelStyle(.iconOnly)
            }
            .padding(8)
            .background(Theme.background, in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

/// The system clipboard (for texts the app shows: commands, links).
public enum Pasteboard {
    public static func copy(_ text: String) {
        #if os(macOS)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        #else
        UIPasteboard.general.string = text
        #endif
    }
}
