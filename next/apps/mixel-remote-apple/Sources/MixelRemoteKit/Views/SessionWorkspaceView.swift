import SwiftUI

/// The session on the technician's side (Mac and iPad with a pointer): the toolbar above, the remote
/// screen, and the tools (chat, files, clipboard) beside it.
public struct SessionWorkspaceView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel
    @State private var tools: Tool? = nil
    @State private var inviting = false

    enum Tool: String, CaseIterable, Identifiable {
        case chat, files, clipboard, assistant
        var id: String { rawValue }
        var title: String {
            switch self {
            case .chat: L("Chat")
            case .files: L("Files")
            case .clipboard: L("Clipboard")
            case .assistant: L("AI assistant and ticket note")
            }
        }
        var symbol: String {
            switch self {
            case .chat: "bubble.left.and.bubble.right"
            case .files: "folder"
            case .clipboard: "doc.on.clipboard"
            case .assistant: "sparkles"
            }
        }
    }

    public init(app: AppModel) {
        self.app = app
        session = app.session
    }

    var remote: RemoteStatus { app.remote }

    public var body: some View {
        VStack(spacing: 0) {
            SessionToolbar(app: app, session: session, tools: $tools, inviting: $inviting)
            Divider()
            HStack(spacing: 0) {
                screen
                if let tool = tools {
                    Divider()
                    ToolPanel(app: app, session: session, tool: tool).frame(width: 300)
                }
            }
        }
        .background(Theme.background)
        .sheet(isPresented: $inviting) { InviteColleagueSheet(app: app, session: session) }
    }

    @ViewBuilder private var screen: some View {
        ZStack {
            Color.black
            #if os(macOS)
            RemoteScreen(session: session)
            #else
            if let frame = session.frame {
                Image(decorative: frame, scale: 1).resizable().interpolation(.medium).aspectRatio(contentMode: .fit)
            }
            #endif
            if session.frame != nil { DrawingOverlay(drawing: app.drawing, pictureSize: session.size) }
            if session.frame == nil {
                VStack(spacing: 12) {
                    ProgressView().controlSize(.large).tint(.white)
                    Text(verbatim: remote.phase == "outgoing" ? L("Waiting for the other person to accept your request.") : L("Connecting securely…"))
                        .foregroundStyle(.white.opacity(0.85))
                }
            }
            VStack {
                Spacer()
                if !session.notice.isEmpty || !session.error.isEmpty || !session.meetingNotice.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        if !session.notice.isEmpty { Text(verbatim: session.notice) }
                        if !session.meetingNotice.isEmpty { Text(verbatim: session.meetingNotice) }
                        if !session.error.isEmpty { Text(verbatim: session.error).foregroundStyle(Color(light: 0xFFB4BD, dark: 0xFFB4BD)) }
                    }
                    .font(.callout)
                    .foregroundStyle(.white)
                    .padding(10)
                    .background(.black.opacity(0.65), in: RoundedRectangle(cornerRadius: 10))
                    .padding(12)
                }
            }
        }
        .frame(minWidth: 480, minHeight: 320)
    }
}

/// The session toolbar: what the other computer offers is shown, nothing else.
struct SessionToolbar: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel
    @Binding var tools: SessionWorkspaceView.Tool?
    @Binding var inviting: Bool

    var remote: RemoteStatus { app.remote }

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(verbatim: L("Computer %@", Format.remoteId(remote.peer))).font(.headline.monospacedDigit())
                HStack(spacing: 6) {
                    Image(systemName: remote.control ? "computermouse.fill" : "eye")
                    Text(verbatim: remote.control ? L("View and control") : L("View only"))
                    if session.fps > 0 { Text(verbatim: "· \(Int(session.size.width)) × \(Int(session.size.height)) · \(session.fps) fps").monospacedDigit() }
                }
                .font(.caption).foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .combine)
            Spacer(minLength: 8)
            if app.drawing.available { DrawingControls(drawing: app.drawing) }
            displays
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) { actions }
            }
            .frame(maxWidth: 620)
            Menu {
                ForEach(SessionWorkspaceView.Tool.allCases.filter { $0 != .assistant || app.assistant.available }) { tool in
                    Button { tools = tools == tool ? nil : tool } label: { Label(tool.title, systemImage: tool.symbol) }
                        .disabled(tool == .files && !remote.files || tool == .clipboard && !remote.clipboard)
                }
            } label: { Label(L("Tools"), systemImage: "sidebar.right") }
                .fixedSize()
            Button(role: .destructive) { session.end() } label: { Label(L("End session"), systemImage: "xmark.circle.fill") }
                .buttonStyle(.borderedProminent).tint(Theme.brand)
                .keyboardShortcut(".", modifiers: .command)
                .disabled(session.stopping)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
    }

    @ViewBuilder private var displays: some View {
        if remote.control, remote.displays > 1 {
            Picker(selection: Binding(get: { remote.display }, set: { session.selectDisplay($0) })) {
                ForEach(0..<remote.displays, id: \.self) { index in
                    Text(verbatim: remote.displayAll && index == remote.displays - 1 ? L("All screens") : L("Display %d", Int(index) + 1)).tag(index)
                }
            } label: { Text(verbatim: L("Display")) }
                .pickerStyle(.menu)
                .fixedSize()
        }
    }

    @ViewBuilder private var actions: some View {
        if remote.offers("privacy") {
            toggle(L("Privacy screen"), "eye.slash", session.privacy) { session.togglePrivacy() }
        }
        if remote.offers("block") {
            toggle(L("Block input"), "hand.raised", session.blockInput) { session.toggleBlockInput() }
        }
        if remote.offers("pointer") {
            toggle(L("Show pointer"), "cursorarrow.rays", session.pointerMode) { session.togglePointer() }
        }
        if remote.offers("lock") {
            tool(L("Lock computer"), "lock") { session.lockNow() }
        }
        if remote.offers("lockend") {
            toggle(L("Lock when done"), "lock.badge.clock", session.lockWhenDone) { session.toggleLockWhenDone() }
        }
        if remote.offers("sas") {
            tool(L("Ctrl+Alt+Del"), "keyboard") { session.ctrlAltDel() }
        }
        if remote.offers("restart") {
            tool(session.restartArmed ? L("Click again to restart") : L("Restart computer"), "restart") { session.restartComputer() }
        }
        if remote.offers("sound") {
            toggle(L("Sound"), session.sound ? "speaker.wave.2" : "speaker.slash", session.sound) { session.toggleSound() }
        }
        if let recording = remote.recording {
            tool(recording.running ? L("Stop recording") : L("Record"), recording.running ? "stop.circle" : "record.circle") {
                if recording.running { session.stopRecording() } else { record() }
            }
            .disabled(session.recordingPending || recording.phase == "saving" || session.frame == nil)
        }
        if remote.phase == "active", !remote.switched {
            tool(L("Invite a colleague"), "person.badge.plus") { inviting = true }
        }
        if session.canSwitchSides {
            tool(L("Switch sides"), "arrow.left.arrow.right.square") { session.switchSides() }
        } else if remote.offers("meet") {
            tool(L("Show my screen"), "rectangle.on.rectangle") { session.showMyScreen() }
        }
        tool(L("Renew connection"), "arrow.triangle.2.circlepath") { session.renew() }
    }

    private func record() {
        Task {
            guard await session.mayRecord() else { session.error = L("Start a permitted recording after the remote screen appears"); return }
            if let path = await app.chooseSaveLocation("Mixel Remote recording.mp4") { session.startRecording(path: path) }
        }
    }

    private func tool(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Label(title, systemImage: symbol) }
            .labelStyle(.iconOnly)
            .help(title)
            .accessibilityLabel(Text(verbatim: title))
            .disabled(app.busy)
    }

    private func toggle(_ title: String, _ symbol: String, _ on: Bool, action: @escaping () -> Void) -> some View {
        let state = on ? L("on") : L("off")
        return Button(action: action) { Label("\(title): \(state)", systemImage: symbol) }
            .labelStyle(.iconOnly)
            .buttonStyle(.bordered)
            .tint(on ? Theme.brand : nil)
            .help("\(title): \(state)")
            .accessibilityLabel(Text(verbatim: title))
            .accessibilityValue(Text(verbatim: state))
            .disabled(app.busy)
    }
}

/// Chat, files, clipboard and typed text during a session.
struct ToolPanel: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel
    let tool: SessionWorkspaceView.Tool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(verbatim: tool.title).font(.headline)
            switch tool {
            case .chat: chat
            case .files: files
            case .clipboard: clipboard
            case .assistant: AssistantPanel(assistant: app.assistant)
            }
        }
        .padding(14)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Theme.background)
    }

    private var chat: some View {
        VStack(spacing: 8) {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(session.chat) { message in
                        HStack {
                            if message.mine { Spacer(minLength: 30) }
                            Text(verbatim: message.text)
                                .padding(8)
                                .foregroundStyle(message.mine ? .white : .primary)
                                .background(message.mine ? Theme.brand : Theme.surface, in: RoundedRectangle(cornerRadius: 10))
                                .textSelection(.enabled)
                            if !message.mine { Spacer(minLength: 30) }
                        }
                    }
                }
            }
            if session.chat.isEmpty { Text(verbatim: L("Messages appear here.")).font(.callout).foregroundStyle(.secondary) }
            HStack {
                TextField(text: $session.chatDraft, prompt: Text(verbatim: L("Chat message"))) { Text(verbatim: L("Chat message")) }
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { session.sendChat() }
                Button(L("Send")) { session.sendChat() }.disabled(session.chatDraft.isEmpty)
            }
        }
    }

    @ViewBuilder private var files: some View {
        if !app.remote.files {
            Text(verbatim: L("File transfer was not allowed for this session.")).foregroundStyle(.secondary)
        } else {
            Button(L("Send a file…")) {
                Task { if let path = await app.chooseFile() { session.sendFile(path: path) } }
            }
            .disabled(session.files?.connected != true || session.files?.outgoing.map { ["offered", "waiting", "transferring", "verifying", "paused"].contains($0.phase) } == true)
            if let view = session.files {
                if let outgoing = view.outgoing { transfer(outgoing, outgoing: true) }
                if let incoming = view.incoming {
                    transfer(incoming, outgoing: false)
                    if incoming.phase == "offered" {
                        HStack {
                            Button(L("Accept and save as…")) {
                                Task {
                                    let name = await session.offeredName(incoming.id) ?? incoming.name
                                    if let path = await app.chooseSaveLocation(name) { session.acceptFile(incoming.id, path: path) }
                                }
                            }
                            Button(L("Decline")) { session.cancelFile(incoming.id) }
                        }
                    }
                }
                if !view.message.isEmpty { Text(verbatim: Translator.shared.translate(view.message)).font(.callout).foregroundStyle(.secondary) }
            }
        }
    }

    private func transfer(_ transfer: FileView.Transfer, outgoing: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(transfer.name, systemImage: outgoing ? "arrow.up.doc" : "arrow.down.doc").lineLimit(1)
            ProgressView(value: transfer.progress)
            HStack {
                Text(verbatim: "\(Format.bytes(transfer.offset)) / \(Format.bytes(transfer.size))").font(.caption).monospacedDigit()
                Spacer()
                if ["transferring", "waiting", "paused", "offered"].contains(transfer.phase) {
                    Button(L("Cancel")) { session.cancelFile(transfer.id) }.controlSize(.small)
                }
            }
            if !transfer.message.isEmpty { Text(verbatim: Translator.shared.translate(transfer.message)).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(10)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder private var clipboard: some View {
        if app.remote.clipboard {
            Button(L("Send my clipboard")) { session.clipboard(push: true) }
            Button(L("Get remote clipboard")) { session.clipboard(push: false) }
            MessageLine(session.clipboardResult, error: false)
        } else {
            Text(verbatim: L("The clipboard was not allowed for this session.")).foregroundStyle(.secondary)
        }
        if app.remote.control {
            Divider()
            Text(verbatim: L("Send text")).font(.headline)
            TextField(text: $session.textToSend, prompt: Text(verbatim: L("Text to type on the remote computer"))) { Text(verbatim: L("Send text")) }
                .textFieldStyle(.roundedBorder)
                .onSubmit { session.typeText() }
            Button(L("Send")) { session.typeText() }.disabled(session.textToSend.isEmpty)
        }
        if !app.remote.printJobs.isEmpty {
            Divider()
            Text(verbatim: L("Print jobs")).font(.headline)
            ForEach(app.remote.printJobs, id: \.self) { path in
                HStack {
                    Text(verbatim: URL(fileURLWithPath: path).lastPathComponent).lineLimit(1)
                    Spacer()
                    Button(L("Print")) { session.printJob(path, open: false) }
                    Button(L("Open")) { session.printJob(path, open: true) }
                }
            }
        }
    }
}

/// Invite a colleague into this session: by the Remote ID of their computer, or with a meeting link.
struct InviteColleagueSheet: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel
    @State private var device = ""
    @State private var control = false
    @State private var link: MeetingRoom?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(verbatim: L("Invite a colleague")).font(.title2.weight(.semibold))
            if app.remote.peerFeatures.contains("join") {
                Text(verbatim: L("Your colleague gets the invitation in Mixel Remote and joins this session. The person at the computer is asked first, unless unattended access is set up.")).foregroundStyle(.secondary)
                FieldRow(L("Or the Remote ID of their computer")) { RemoteIdField(text: $device) }
                Toggle(isOn: $control) { Text(verbatim: L("Allow viewing and control")) }
                if let message = app.remote.inviteMessage { MessageLine(Translator.shared.translate(message), error: false) }
            } else {
                Text(verbatim: L("The other computer cannot take a second technician directly. Use a meeting link instead.")).foregroundStyle(.secondary)
            }
            if let link {
                FieldRow(L("Meeting link")) {
                    HStack {
                        Text(verbatim: link.invite).font(.callout.monospaced()).textSelection(.enabled)
                        Button { Pasteboard.copy(link.invite) } label: { Label(L("Copy"), systemImage: "doc.on.doc") }.labelStyle(.iconOnly)
                    }
                }
            }
            MessageLine(session.error)
            HStack {
                Button(L("Use a meeting link instead")) { Task { link = await session.meetingLink() } }.linkButton()
                Spacer()
                Button(L("Close")) { dismiss() }.keyboardShortcut(.cancelAction)
                if app.remote.peerFeatures.contains("join") {
                    Button(L("Send invitation")) {
                        guard let id = Format.parseRemoteId(device) else { session.error = L("Enter a nine-digit connection ID"); return }
                        session.invite(colleague: nil, device: id, control: control)
                    }
                    .buttonStyle(PrimaryButtonStyle()).keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(22)
        .background(Theme.background)
        .frame(minWidth: 440)
    }
}

/// Mixel team: the company's AI server looks at the remote screen and writes the ticket note.
struct AssistantPanel: View {
    @ObservedObject var assistant: AssistantModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if !assistant.connected {
                    TextField(text: $assistant.address, prompt: Text(verbatim: "http://ai-server:11434")) { Text(verbatim: L("AI server")) }
                        .textFieldStyle(.roundedBorder)
                    Button(L("Connect")) { assistant.connect() }
                } else if assistant.models.count > 1 {
                    Picker(selection: $assistant.model) { ForEach(assistant.models, id: \.self) { Text(verbatim: $0).tag($0) } } label: { Text(verbatim: L("Model")) }
                        .onChange(of: assistant.model) { _ in assistant.connect(announce: false) }
                }
                MessageLine(assistant.status, error: false)
                TextField(text: $assistant.question, prompt: Text(verbatim: L("Question or keywords (optional)"))) { Text(verbatim: L("Question")) }
                    .textFieldStyle(.roundedBorder)
                HStack {
                    Button(L("Explain the screen")) { assistant.run(.ask) }
                    Button(L("Write ticket note")) { assistant.run(.note) }
                }
                .disabled(!assistant.connected || assistant.busy)
                if !assistant.answer.isEmpty {
                    TextEditor(text: $assistant.answer).font(.callout).frame(minHeight: 140)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                    Button(L("Copy")) { Pasteboard.copy(assistant.answer) }.controlSize(.small)
                }
                Divider()
                Text(verbatim: L("Save in ISM ticket")).font(.subheadline.weight(.semibold))
                HStack {
                    TextField(text: $assistant.ticket, prompt: Text(verbatim: "T-2026-0001")) { Text(verbatim: L("Ticket number")) }.textFieldStyle(.roundedBorder)
                    Button(L("Save note")) { assistant.saveTicket() }
                }
                MessageLine(assistant.ticketStatus, error: false)
            }
        }
        .onAppear { if !assistant.connected { assistant.connect(announce: false) } }
    }
}
