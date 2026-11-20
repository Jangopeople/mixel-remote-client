import Foundation

/// This computer as the one being helped: the consent sheet (screen, control, clipboard, files,
/// chat, recording), a second technician asking to join, unattended access, the one-time password,
/// and pairing.
@MainActor
public final class HostModel: ObservableObject {
    // The app model lives as long as the app; its parts keep it (no reference cycle matters there).
    let app: AppModel
    @Published public private(set) var sources: [CaptureTarget] = []
    @Published public var source = ""
    @Published public var allowControl = true
    @Published public var allowClipboard = true
    @Published public var allowFiles = false
    @Published public var allowChat = false
    @Published public var allowRecording = false
    @Published public private(set) var screenAccess = true
    @Published public private(set) var inputAccess = true
    @Published public var error = ""
    // Unattended access.
    @Published public private(set) var unattendedSources: [CaptureTarget] = []
    @Published public var unattendedSource = ""
    @Published public var unattendedConfirm = false
    @Published public var unattendedControl = false

    private static let unattendedKey = "mixel.remote.unattended.v1"
    private var restoring = false
    private var lastPermissionCheck = Date.distantPast
    private var promptedOnStart = false

    init(app: AppModel) { self.app = app }

    public var remote: RemoteStatus { app.remote }
    /// Whether macOS has granted both Screen Recording and Accessibility / Input Control permissions.
    public var permissionsReady: Bool { screenAccess && inputAccess }
    /// A technician asks to connect and this person decides.
    public var asking: Bool { remote.phase == "incoming" && !remote.unattendedSession }
    public var selected: CaptureTarget? { sources.first { $0.id == source } }
    public var canAccept: Bool { asking && !app.busy && screenAccess && selected?.fullDisplay == true }
    public var controlAvailable: Bool { asking && (selected?.fullDisplay ?? false) && inputAccess }

    /// Polls macOS Screen Recording and Accessibility permissions; when `promptIfMissing` is true,
    /// immediately triggers the native macOS permission dialogs so the user grants them before sharing their ID.
    public func checkPermissions(promptIfMissing: Bool = false) async {
        guard !app.isPhone, !(app.engine is MockEngine), !remote.controllerOnly else {
            screenAccess = true
            inputAccess = true
            return
        }
        lastPermissionCheck = Date()
        let screen = (try? await app.engine.run("screen_access", as: Bool.self)) ?? true
        let input = remote.inputAccess
        if screen != screenAccess { screenAccess = screen }
        if input != inputAccess { inputAccess = input }
        if promptIfMissing, !promptedOnStart {
            promptedOnStart = true
            if !screen {
                _ = try? await app.engine.run("request_screen_access", as: Bool.self)
            }
            if !input {
                _ = try? await app.engine.run("request_input_access", as: Bool.self)
            }
            let nextScreen = (try? await app.engine.run("screen_access", as: Bool.self)) ?? screen
            if nextScreen != screenAccess { screenAccess = nextScreen }
        }
    }

    func statusChanged(from previous: RemoteStatus, to next: RemoteStatus) {
        if !(app.engine is MockEngine), !app.isPhone, !next.controllerOnly {
            if next.inputAccess != inputAccess { inputAccess = next.inputAccess }
            let interval: TimeInterval = permissionsReady ? 30.0 : 1.5
            if Date().timeIntervalSince(lastPermissionCheck) >= interval {
                Task { await checkPermissions(promptIfMissing: false) }
            }
        }
        if asking, !next.controllerOnly, previous.phase != "incoming" || previous.session != next.session {
            sources = []
            source = ""
            error = ""
            allowControl = true
            allowChat = false; allowRecording = false; allowFiles = false; allowClipboard = true
            Task { await loadSources() }
        }
        if next.phase == "ready", previous.phase != "ready" { Task { await restoreUnattended() } }
    }

    public func loadSources() async {
        guard asking, let session = remote.session else { return }
        do {
            let access = (try? await app.engine.run("screen_access", as: Bool.self)) ?? true
            let targets: [CaptureTarget] = try await app.engine.run("capture_targets")
            guard asking, remote.session == session else { return }
            screenAccess = access
            sources = targets.filter(\.fullDisplay)
            if !sources.contains(where: { $0.id == source }) { source = sources.first?.id ?? "" }
            error = ""
        } catch {
            guard asking, remote.session == session else { return }
            self.error = AppModel.message(error)
        }
    }

    public func decide(accept: Bool) {
        guard asking, !app.busy, let session = remote.session, !accept || canAccept else { return }
        let decision = Decision(session: session, accept: accept, source: source, confirm: accept,
                                control: accept && allowControl && controlAvailable, clipboard: accept && allowClipboard,
                                files: accept && allowFiles, chat: accept && allowChat, recording: accept && allowRecording)
        app.act { try await self.app.engine.perform("remote_decide", ["decision": decision]) }
    }

    public func requestScreenAccess() {
        Task {
            _ = try? await self.app.engine.run("request_screen_access", as: Bool.self)
            await self.checkPermissions(promptIfMissing: false)
            if self.asking { await self.loadSources() }
            if !self.screenAccess, let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
                self.app.openURL(url)
            }
        }
    }

    public func requestInputAccess() {
        Task {
            _ = try? await self.app.engine.run("request_input_access", as: Bool.self)
            await self.app.refreshRemote()
            await self.checkPermissions(promptIfMissing: false)
            if !self.inputAccess, let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
                self.app.openURL(url)
            }
        }
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
    public func stopSharing() { app.idleSession.end() }

    // MARK: Unattended access, one-time password, pairing

    public func loadUnattendedSources() {
        app.act {
            let targets: [CaptureTarget] = try await self.app.engine.run("capture_targets")
            self.unattendedSources = targets.filter(\.fullDisplay)
            self.unattendedSource = self.unattendedSources.first?.id ?? ""
            self.unattendedConfirm = false
            self.unattendedControl = self.remote.inputAccess
            if self.unattendedSources.isEmpty { throw EngineFailure("An available display is required before enabling unattended sharing.") }
        }
    }

    public func enableUnattended() {
        guard !unattendedSource.isEmpty, unattendedConfirm, let profile = app.account.account?.profile, let host = remote.host else {
            error = L("Choose a display and confirm it before enabling unattended sharing.")
            return
        }
        let source = unattendedSource, control = unattendedControl && remote.inputAccess
        unattendedConfirm = false
        app.act {
            try await self.app.engine.perform("remote_unattended", ["source": source, "control": control])
            UserDefaults.standard.set(["source": source, "control": control, "profile": profile, "host": host], forKey: Self.unattendedKey)
        }
    }

    /// The person closed the "turn on" steps without enabling.
    public func cancelUnattendedSetup() {
        unattendedSources = []
        unattendedConfirm = false
    }

    public func disableUnattended() {
        UserDefaults.standard.set(["enabled": false], forKey: Self.unattendedKey)
        app.act { try await self.app.engine.perform("remote_unattended", ["source": nil, "control": false]) }
    }

    /// Restore after a trusted startup or after this same host reconnects during this app run.
    func restoreUnattended() async {
        guard !restoring, remote.phase == "ready", !remote.unattendedEnabled, remote.unattendedPending == nil,
              let profile = app.account.account?.profile, let host = remote.host else { return }
        restoring = true
        defer { restoring = false }
        let saved = UserDefaults.standard.dictionary(forKey: Self.unattendedKey)
        if let saved {
            if let explicit = saved["enabled"] as? Bool, !explicit { return }
            guard saved["profile"] == nil || saved["profile"] as? String == profile,
                  saved["host"] == nil || (saved["host"] as? NSNumber)?.uint32Value == host else { return }
        }
        let wantedSource = saved?["source"] as? String
        let wantedControl = (saved?["control"] as? Bool) ?? true
        guard let targets: [CaptureTarget] = try? await app.engine.run("capture_targets"),
              let matched = targets.first(where: { $0.id == wantedSource && $0.fullDisplay }) ?? targets.first(where: \.fullDisplay),
              app.account.account?.profile == profile, remote.host == host, remote.phase == "ready",
              !remote.unattendedEnabled, remote.unattendedPending == nil
        else { return }
        let control = wantedControl && remote.inputAccess
        try? await app.engine.perform("remote_unattended", ["source": matched.id, "control": control])
        if saved == nil {
            UserDefaults.standard.set(["source": matched.id, "control": control, "profile": profile, "host": host, "enabled": true], forKey: Self.unattendedKey)
        }
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
