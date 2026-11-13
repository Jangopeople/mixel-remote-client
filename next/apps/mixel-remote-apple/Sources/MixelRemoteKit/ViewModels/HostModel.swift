import Foundation

/// This computer as the one being helped: the consent sheet (screen, control, clipboard, files,
/// chat, recording), a second technician asking to join, unattended access, the one-time password,
/// and pairing.
@MainActor
public final class HostModel: ObservableObject {
    unowned let app: AppModel
    @Published public private(set) var sources: [CaptureTarget] = []
    @Published public var source = ""
    @Published public var confirmDisplay = false
    @Published public var allowControl = false
    @Published public var allowClipboard = false
    @Published public var allowFiles = false
    @Published public var allowChat = false
    @Published public var allowRecording = false
    @Published public private(set) var screenAccess = true
    @Published public var error = ""
    // Unattended access.
    @Published public private(set) var unattendedSources: [CaptureTarget] = []
    @Published public var unattendedSource = ""
    @Published public var unattendedConfirm = false
    @Published public var unattendedControl = false

    private static let unattendedKey = "mixel.remote.unattended.v1"
    private var restored = false

    init(app: AppModel) { self.app = app }

    public var remote: RemoteStatus { app.remote }
    /// A technician asks to connect and this person decides.
    public var asking: Bool { remote.phase == "incoming" && !remote.unattendedSession }
    public var selected: CaptureTarget? { sources.first { $0.id == source } }
    public var canAccept: Bool {
        guard asking, !app.busy, let selected else { return false }
        return !selected.fullDisplay || confirmDisplay
    }
    public var controlAvailable: Bool { asking && (selected?.fullDisplay ?? false) && remote.inputAccess }

    func statusChanged(from previous: RemoteStatus, to next: RemoteStatus) {
        if next.phase == "incoming", previous.phase != "incoming", !next.controllerOnly {
            allowChat = false; allowRecording = false; allowFiles = false; allowClipboard = false
            Task { await loadSources() }
        }
        if next.phase == "ready", previous.phase != "ready" { Task { await restoreUnattended() } }
    }

    public func loadSources() async {
        do {
            screenAccess = (try? await app.engine.run("screen_access", as: Bool.self)) ?? true
            let targets: [CaptureTarget] = try await app.engine.run("capture_targets")
            sources = targets.filter(\.fullDisplay).sorted { $0.fullDisplay && !$1.fullDisplay }
            if !sources.contains(where: { $0.id == source }) { source = sources.first?.id ?? "" }
            // One screen: it is the one to share. Keyboard and mouse are pre-selected when they can be
            // allowed; the person still decides with Allow.
            confirmDisplay = sources.count == 1
            allowControl = confirmDisplay && remote.inputAccess
        } catch {
            self.error = AppModel.message(error)
        }
    }

    public func decide(accept: Bool) {
        guard let session = remote.session else { return }
        let decision = Decision(session: session, accept: accept, source: source, confirm: confirmDisplay,
                                control: accept && allowControl && controlAvailable, clipboard: accept && allowClipboard,
                                files: accept && allowFiles, chat: accept && allowChat, recording: accept && allowRecording)
        app.act { try await self.app.engine.perform("remote_decide", ["decision": decision]) }
    }

    public func requestScreenAccess() {
        app.act {
            _ = try await self.app.engine.run("request_screen_access", as: Bool.self)
            await self.loadSources()
        }
    }

    public func requestInputAccess() {
        app.act { _ = try await self.app.engine.run("request_input_access", as: Bool.self) }
    }

    // MARK: During a hosted session

    public func decideJoin(accept: Bool, control: Bool) {
        guard let request = remote.joinRequest else { return }
        app.act { try await self.app.engine.perform("remote_decide_join", ["session": request.session, "accept": accept, "control": control]) }
    }

    public func removeColleague(_ colleague: Colleague) {
        app.act { try await self.app.engine.perform("remote_remove_colleague", ["session": colleague.session]) }
    }

    public var meetingOffer: String? {
        guard let offer = remote.meetingOffer, offer.count == 2, remote.phase == "active", remote.sharing,
              offer[0] == remote.session, offer[1] != app.session.dismissedMeetingRoom else { return nil }
        return offer[1]
    }

    public func openMeetingOffer() {
        guard let room = meetingOffer else { return }
        app.session.dismissedMeetingRoom = room
        app.act {
            let link: String = try await self.app.engine.run("meeting_link", ["link": "https://rs.mixel.ch/meet/#\(room)"])
            if let url = URL(string: link) { self.app.openURL(url) }
        }
    }

    /// The helped person ends the session.
    public func stopSharing() { app.session.end() }

    // MARK: Unattended access, one-time password, pairing

    public func loadUnattendedSources() {
        app.act {
            let targets: [CaptureTarget] = try await self.app.engine.run("capture_targets")
            self.unattendedSources = targets.filter(\.fullDisplay)
            self.unattendedSource = self.unattendedSources.first?.id ?? ""
            self.unattendedConfirm = false
            if self.unattendedSources.isEmpty { throw EngineFailure("An available display is required before enabling unattended sharing.") }
        }
    }

    public func enableUnattended() {
        guard !unattendedSource.isEmpty, unattendedConfirm else {
            error = L("Choose a display and confirm it before enabling unattended sharing.")
            return
        }
        let source = unattendedSource, control = unattendedControl && remote.inputAccess
        unattendedConfirm = false
        UserDefaults.standard.set(["source": source, "control": control], forKey: Self.unattendedKey)
        app.act { try await self.app.engine.perform("remote_unattended", ["source": source, "control": control]) }
    }

    public func disableUnattended() {
        UserDefaults.standard.removeObject(forKey: Self.unattendedKey)
        app.act { try await self.app.engine.perform("remote_unattended", ["source": nil, "control": false]) }
    }

    /// After a start with a remembered sign-in, unattended sharing comes back as the owner left it.
    func restoreUnattended() async {
        guard !restored, !remote.unattendedEnabled, remote.unattendedPending == nil,
              let saved = UserDefaults.standard.dictionary(forKey: Self.unattendedKey),
              let source = saved["source"] as? String, let control = saved["control"] as? Bool else { return }
        guard let startup: StartupSettings = try? await app.engine.run("startup_settings"), startup.restored || startup.launchedAtStartup,
              let targets: [CaptureTarget] = try? await app.engine.run("capture_targets"),
              targets.contains(where: { $0.id == source && $0.fullDisplay }) else { return }
        restored = true
        try? await app.engine.perform("remote_unattended", ["source": source, "control": control])
    }

    public func oneTimePassword(required: Bool) {
        app.act { try await self.app.engine.perform("remote_support_code", ["required": required]) }
    }

    public func checkPairing() {
        app.act { try await self.app.engine.perform("remote_pairing", ["confirmation": nil]) }
    }

    public func confirmPairing() {
        guard let pairing = remote.pairing else { return }
        app.act { try await self.app.engine.perform("remote_pairing", ["confirmation": pairing.requestId]) }
    }

    public func copyId() {
        app.act { try await self.app.engine.perform("copy_connection_id") }
    }
}
