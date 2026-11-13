import CoreGraphics
import Foundation

/// The session on the technician's side: the remote picture, keyboard and mouse (or touch), and the
/// toolbar (displays, privacy screen, block input, pointer, lock, Ctrl+Alt+Del, restart, sound,
/// clipboard, files, chat, recording, invite a colleague, switch sides, end).
@MainActor
public final class SessionModel: ObservableObject {
    unowned let app: AppModel
    @Published public private(set) var frame: CGImage?
    @Published public private(set) var size = CGSize.zero
    @Published public private(set) var fps = 0
    @Published public private(set) var stopping = false
    @Published public var error = ""
    @Published public var notice = ""
    // Toolbar toggles of this session.
    @Published public private(set) var privacy = false
    @Published public private(set) var blockInput = false
    @Published public private(set) var lockWhenDone = false
    @Published public var pointerMode = false
    @Published public private(set) var sound = true
    @Published public private(set) var restartArmed = false
    // Tools.
    @Published public private(set) var chat: [ChatMessage] = []
    @Published public var chatDraft = ""
    @Published public private(set) var files: FileView?
    @Published public var clipboardResult = ""
    @Published public var textToSend = ""
    @Published public private(set) var recordingPending = false
    @Published public private(set) var meetingNotice = ""
    @Published public var dismissedMeetingRoom = ""

    var lastRequestUnattended = false
    private var video: Task<Void, Never>?
    private var tools: Task<Void, Never>?
    private var viewingSession: String?
    private var toggleSession: String?
    private var stoppedConsent: String?
    private var shownTimes: [Date] = []
    private var inputQueue: [InputEvent] = []
    private var sending = false
    private var reconnect: Reconnect?

    struct Reconnect {
        var host: UInt32
        var signin: UInt32?
        var unattended: Bool
        var until: Date
        var since: Date
        var wentOffline = false
        var lastTry = Date.distantPast
        var lastPresence = Date.distantPast
        var session: String
    }

    init(app: AppModel) { self.app = app }

    public var remote: RemoteStatus { app.remote }
    public var session: String? { remote.session }
    /// The picture is shown and keyboard and mouse go to the other computer.
    public var controllable: Bool {
        !stopping && remote.viewing && remote.control && frame != nil && !locallyStopped
    }
    var locallyStopped: Bool { remote.consent != nil && remote.consent == stoppedConsent }

    func statusChanged(from previous: RemoteStatus, to next: RemoteStatus) {
        if next.session != toggleSession {
            toggleSession = next.session
            privacy = false; blockInput = false; lockWhenDone = false; pointerMode = false; sound = true
            chat = []; files = nil; clipboardResult = ""
        }
        if next.viewing, !stopping, !locallyStopped, let session = next.session {
            if viewingSession != session { startVideo(session) }
        } else if viewingSession != nil {
            clear()
        }
        if next.phase == "active" || ["incoming", "outgoing"].contains(next.phase) { startTools() }
        else { tools?.cancel(); tools = nil }
        if !next.inSession { stoppedConsent = nil }
        continueReconnect(next)
    }

    /// Stops the picture and forgets it.
    public func clear() {
        video?.cancel()
        video = nil
        viewingSession = nil
        frame = nil
        size = .zero
        fps = 0
        inputQueue.removeAll()
    }

    private func startVideo(_ session: String) {
        clear()
        viewingSession = session
        video = Task { [weak self] in
            var sequence: UInt64 = 0
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    let data = try await self.app.engine.nextVideo(session: session, compressed: false)
                    if Task.isCancelled || self.viewingSession != session { return }
                    if data.isEmpty {
                        try? await Task.sleep(nanoseconds: 16_000_000)
                        continue
                    }
                    let decoded = try VideoFrame.decode(data, after: sequence)
                    sequence = decoded.sequence
                    self.show(decoded)
                } catch is CancellationError {
                    return
                } catch VideoFrame.Problem.compressed {
                    continue
                } catch {
                    if self.viewingSession == session {
                        self.error = error is VideoFrame.Problem ? L("Invalid native video frame") : AppModel.message(error)
                        try? await self.app.engine.perform("remote_close", ["session": session])
                        self.clear()
                    }
                    return
                }
            }
        }
    }

    private func show(_ decoded: VideoFrame) {
        frame = decoded.image
        let next = CGSize(width: decoded.width, height: decoded.height)
        if next != size { size = next }
        let now = Date()
        shownTimes.append(now)
        shownTimes.removeAll { now.timeIntervalSince($0) > 1 }
        if fps != shownTimes.count { fps = shownTimes.count }
    }

    /// For screenshots and tests: show this picture without a running engine.
    public func showSample(_ data: Data) {
        if let decoded = try? VideoFrame.decode(data) { show(decoded) }
    }

    // MARK: Input

    /// Queues an input event; pointer moves replace the previous unsent move.
    public func send(_ event: InputEvent) {
        guard let session else { return }
        if case .releaseAll = event {} else if !controllable { return }
        if case .pointer = event, case .pointer = inputQueue.last { inputQueue[inputQueue.count - 1] = event }
        else if inputQueue.count >= 64 {
            inputQueue.removeAll()
            error = L("Input delivery stalled; the session has been stopped")
            Task { try? await app.engine.perform("remote_close", ["session": session]) }
            return
        } else { inputQueue.append(event) }
        flush(session)
    }

    private func flush(_ session: String) {
        guard !sending else { return }
        sending = true
        Task {
            defer { sending = false }
            while !inputQueue.isEmpty {
                let event = inputQueue.removeFirst()
                guard self.session == session else { inputQueue.removeAll(); return }
                do {
                    let posted = try await app.engine.send(input: event, session: session)
                    if !posted, case .text = event { error = L("The remote computer could not type this text.") }
                } catch {
                    inputQueue.removeAll()
                    self.error = AppModel.message(error)
                    return
                }
            }
        }
    }

    /// A pointer position in the picture (0…1 in both directions) as the engine expects (0…65535).
    public static func normalized(_ value: CGFloat) -> UInt16 {
        UInt16((max(0, min(1, value)) * 65535).rounded())
    }

    public func move(to point: CGPoint) {
        let x = Self.normalized(point.x), y = Self.normalized(point.y)
        if pointerMode {
            action("pointer:\(x),\(y)", quiet: true)
        } else {
            send(.pointer(x: x, y: y))
        }
    }

    public func typeText() {
        let text = textToSend
        textToSend = ""
        if !text.isEmpty { send(.text(text)) }
    }

    /// Presses and releases a key, with modifiers held around it (Ctrl+C and similar).
    public func press(_ usage: UInt16, modifiers: [UInt16] = []) {
        for modifier in modifiers { send(.key(usage: modifier, down: true)) }
        send(.key(usage: usage, down: true))
        send(.key(usage: usage, down: false))
        for modifier in modifiers.reversed() { send(.key(usage: modifier, down: false)) }
    }

    // MARK: Toolbar

    func action(_ name: String, quiet: Bool = false) {
        guard let session else { return }
        Task {
            do { _ = try await app.engine.send(input: .action(name), session: session) }
            catch { if !quiet { self.error = AppModel.message(error) } }
        }
    }

    public func togglePrivacy() { privacy.toggle(); action("privacy:\(privacy ? "on" : "off")") }
    public func toggleBlockInput() { blockInput.toggle(); action("block:\(blockInput ? "on" : "off")") }
    public func toggleLockWhenDone() { lockWhenDone.toggle(); action("lockend:\(lockWhenDone ? "on" : "off")") }
    public func togglePointer() {
        pointerMode.toggle()
        if !pointerMode { action("pointer:off", quiet: true) }
    }
    public func toggleSound() { sound.toggle(); action(sound ? "sound:on" : "sound:off", quiet: true) }
    public func lockNow() { action("lock") }
    public func ctrlAltDel() { action("sas") }
    public func selectDisplay(_ index: UInt16) {
        guard let session else { return }
        Task { _ = try? await app.engine.send(input: .selectDisplay(index), session: session) }
    }

    /// The first click arms, the second (within 5 s) restarts the remote computer; the app reconnects
    /// when it is back (up to 10 minutes).
    public func restartComputer() {
        guard let session, let peer = remote.peer else { return }
        if !restartArmed {
            restartArmed = true
            Task { try? await Task.sleep(nanoseconds: 5_000_000_000); self.restartArmed = false }
            return
        }
        restartArmed = false
        let now = Date()
        reconnect = Reconnect(host: peer, signin: remote.signinId, unattended: lastRequestUnattended || remote.unattendedSession, until: now.addingTimeInterval(600), since: now, session: session)
        action("restart")
    }

    private func continueReconnect(_ status: RemoteStatus) {
        guard var plan = reconnect else { return }
        if status.phase == "active" { if status.session != plan.session { reconnect = nil; notice = "" }; return }
        guard !["negotiating", "outgoing", "deciding"].contains(status.phase) else { return }
        let now = Date()
        if now > plan.until { reconnect = nil; error = L("The remote computer did not come back within 10 minutes."); return }
        if now.timeIntervalSince(plan.lastPresence) > 5 {
            plan.lastPresence = now
            let hosts = [plan.host] + (plan.signin.map { [$0] } ?? [])
            Task { try? await app.engine.perform("remote_presence", ["hosts": hosts]) }
        }
        if !status.online.contains(plan.host) { plan.wentOffline = true }
        let ready = plan.wentOffline || now.timeIntervalSince(plan.since) > 120
        var target: UInt32?
        if ready {
            if status.online.contains(plan.host) { target = plan.host }
            else if let signin = plan.signin, status.online.contains(signin), now.timeIntervalSince(plan.since) > 45 { target = signin }
            else if plan.unattended, now.timeIntervalSince(plan.since) > 60 { target = plan.host }
        }
        notice = L("The remote computer is restarting. Mixel Remote reconnects when it is back…")
        if let target, status.phase == "ready", !app.busy, now.timeIntervalSince(plan.lastTry) > 15 {
            plan.lastTry = now
            let unattended = plan.unattended || target == plan.signin
            app.act {
                struct Request: Encodable { var host: UInt32; var password: String?; var unattended: Bool }
                try await self.app.engine.perform("remote_request", Request(host: target, password: nil, unattended: unattended))
            }
        }
        reconnect = plan
    }

    /// Renew the connection (new keys, same session).
    public func renew() {
        guard let session else { return }
        app.act { try await self.app.engine.perform("remote_restart", ["session": session]) }
    }

    /// Ends the session for both sides.
    public func end() {
        guard let consent = remote.consent, !stopping else { return }
        stoppedConsent = consent
        stopping = true
        clear()
        error = ""
        Task {
            do { try await app.engine.perform("remote_stop_session", ["consent": consent]) }
            catch { self.error = L("Could not confirm that the session ended. Try End session again.") + " " + AppModel.message(error) }
            stopping = false
            await app.refreshRemote()
        }
    }

    // MARK: Clipboard, chat, files

    public func clipboard(push: Bool) {
        guard let session else { return }
        clipboardResult = L("Transferring…")
        Task {
            do {
                try await app.engine.perform("remote_clipboard", ["session": session, "push": push])
                clipboardResult = push ? L("Remote clipboard updated.") : L("Your clipboard was updated.")
            } catch { clipboardResult = AppModel.message(error) }
        }
    }

    private func startTools() {
        guard tools == nil else { return }
        tools = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.readTools()
                try? await Task.sleep(nanoseconds: 700_000_000)
            }
        }
    }

    func readTools() async {
        let status = remote
        if let session = status.session, status.phase == "active" {
            if let messages = try? await app.engine.run("remote_chat_messages", ["session": session], as: [ChatMessage]?.self) ?? nil, messages != chat { chat = messages }
        }
        if let consent = status.consent, status.files, status.phase == "active" {
            let view: FileView? = try? await app.engine.run("remote_file_status", ["consent": consent])
            if view != files { files = view }
        } else if files != nil { files = nil }
    }

    public func sendChat() {
        let text = chatDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let session, !text.isEmpty else { return }
        chatDraft = ""
        Task {
            do { try await app.engine.perform("remote_chat_send", ["session": session, "text": text]); await readTools() }
            catch { self.error = AppModel.message(error) }
        }
    }

    /// Sends the file at `path` (chosen in the app's Open panel).
    public func sendFile(path: String) {
        guard let consent = remote.consent else { return }
        Task {
            do { try await app.engine.perform("remote_file_send", ["consent": consent, "path": path]); await readTools() }
            catch { self.error = AppModel.message(error) }
        }
    }

    /// The suggested name of a file the other side offers (for the Save panel).
    public func offeredName(_ id: String) async -> String? {
        guard let consent = remote.consent else { return nil }
        return try? await app.engine.run("remote_file_pending", ["consent": consent, "id": id])
    }

    public func acceptFile(_ id: String, path: String) {
        guard let consent = remote.consent else { return }
        Task {
            do { try await app.engine.perform("remote_file_accept", ["consent": consent, "id": id, "path": path]); await readTools() }
            catch { self.error = AppModel.message(error) }
        }
    }

    public func cancelFile(_ id: String) {
        guard let consent = remote.consent else { return }
        Task { try? await app.engine.perform("remote_file_cancel", ["consent": consent, "id": id]); await readTools() }
    }

    // MARK: Recording

    public var recordingText: String {
        switch remote.recording?.phase {
        case "waiting_for_video": L("Waiting for a complete video frame…")
        case "recording": L("Recording video")
        case "saving": L("Saving recording…")
        case "saved": L("Recording saved")
        case "saved_partial": L("The recording stopped early. The playable portion was saved.")
        case "error": Translator.shared.translate(remote.recording?.message ?? "")
        default: ""
        }
    }

    public func mayRecord() async -> Bool {
        guard let session else { return false }
        return (try? await app.engine.run("remote_record_may_start", ["session": session], as: Bool.self)) ?? false
    }

    public func startRecording(path: String) {
        guard let session else { return }
        recordingPending = true
        Task {
            do { try await app.engine.perform("remote_record_start", ["session": session, "path": path]) }
            catch { self.error = AppModel.message(error) }
            recordingPending = false
            await app.refreshRemote()
        }
    }

    public func stopRecording() {
        guard let session else { return }
        recordingPending = true
        Task {
            do { try await app.engine.perform("remote_record_stop", ["session": session]) }
            catch { self.error = AppModel.message(error) }
            recordingPending = false
            await app.refreshRemote()
        }
    }

    // MARK: Colleagues, switching sides, meetings

    public func invite(colleague user: String?, device: UInt32?, control: Bool) {
        guard let session else { return }
        app.act {
            struct Args: Encodable { var session: String; var user: String?; var device: UInt32?; var control: Bool }
            try await self.app.engine.perform("remote_invite", Args(session: session, user: user, device: device, control: control))
        }
    }

    public func join(_ invitation: Invitation) {
        app.act { try await self.app.engine.perform("remote_join", ["invitation": invitation.invitation]) }
    }

    public func dismiss(_ invitation: Invitation) {
        app.act { try await self.app.engine.perform("remote_dismiss_invitation", ["invitation": invitation.invitation]) }
    }

    public var canSwitchSides: Bool {
        remote.phase == "active" && remote.peerFeatures.contains("switch")
    }

    public func switchSides() {
        guard let session else { return }
        app.act { try await self.app.engine.perform("remote_switch_sides", ["session": session]) }
    }

    /// Show my screen (older computers without switching sides): a Mixel Meet room the other side is
    /// asked to watch.
    public func showMyScreen() {
        guard let session else { return }
        app.act {
            let room: MeetingRoom = try await self.app.engine.run("meeting_create")
            if let url = URL(string: room.host) { self.app.openURL(url) }
            _ = try await self.app.engine.send(input: .action("meet:\(room.room)"), session: session)
            self.meetingNotice = L("Your meeting opens in the browser. Choose “Share screen” there; the other person is asked to watch.")
        }
    }

    /// The meeting link for a colleague (when the other computer cannot invite natively).
    public func meetingLink() async -> MeetingRoom? {
        do {
            let room: MeetingRoom = try await app.engine.run("meeting_create")
            if let url = URL(string: room.host) { app.openURL(url) }
            return room
        } catch {
            self.error = AppModel.message(error)
            return nil
        }
    }

    public func printJob(_ path: String, open: Bool) {
        app.act { _ = try await self.app.engine.run("print_job", ["path": path, "open": open], as: String.self) }
    }
}
