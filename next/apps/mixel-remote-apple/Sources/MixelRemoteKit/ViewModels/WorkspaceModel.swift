import Foundation

/// The technician's workspace (redesign "Pro Console"): the session tabs in the title bar, which tab
/// is in front, which sessions are in their own window, the connection being prepared, and the two
/// remembered choices ("when a session is ready" and "open sessions in").
///
/// Sessions are keyed by the other computer's Remote ID, not by the engine's session id: the session
/// id changes when the connection is renewed, the computer does not. Everything is a list so several
/// sessions at once only need `sessionTabs` to read more than one session from the engine.
@MainActor
public final class WorkspaceModel: ObservableObject {
    let app: AppModel

    /// What happens when a session is ready to be shown.
    public enum WhenReady: String, CaseIterable, Sendable {
        /// Stay where you are; an "Open" notice appears (the default).
        case stay
        /// Show the session at once.
        case open
    }

    /// Where a session opens.
    public enum OpenIn: String, CaseIterable, Sendable {
        case tab
        case window
    }

    /// The tab in the title bar: the Devices console, or one session.
    public enum Tab: Hashable, Sendable {
        case console
        case session(UInt32)
    }

    /// One session tab.
    public struct SessionTab: Identifiable, Equatable, Sendable {
        /// The other computer's Remote ID.
        public var peer: UInt32
        public var name: String
        public var os: String
        /// `connecting`, `starting` (video not there yet), `ready`, `ending`.
        public var state: String
        public var id: UInt32 { peer }
    }

    /// A connection being prepared from the Devices list or "Connect by ID".
    public struct Attempt: Equatable, Sendable {
        public var peer: UInt32
        public var name: String
        public var os: String
        public var started: Date
        /// The step reached: 0 reaching the device, 1 waiting for approval or unattended access,
        /// 2 encrypting end-to-end, 3 starting video, 4 ready.
        public var step: Int
        public var unattended: Bool
        public var failure: String
        /// "Open in window instead" was chosen for this connection.
        public var inWindow: Bool
    }

    static let whenReadyKey = "mixel.remote.workspace.whenReady"
    static let openInKey = "mixel.remote.workspace.openIn"

    @Published public var whenReady: WhenReady { didSet { defaults.set(whenReady.rawValue, forKey: Self.whenReadyKey) } }
    @Published public var openIn: OpenIn { didSet { defaults.set(openIn.rawValue, forKey: Self.openInKey) } }
    @Published public var selected: Tab = .console
    @Published public private(set) var tabs: [SessionTab] = []
    /// Sessions shown in their own window.
    @Published public private(set) var windows: Set<UInt32> = []
    @Published public private(set) var attempt: Attempt?
    /// Sessions that became ready while the person stayed elsewhere: "SRV-DC-02 is ready · Open".
    @Published public private(set) var readyNotices: [UInt32] = []

    /// Opens a session window (set by the Mac app; `openWindow(id: "session", value: peer)`).
    public var openWindow: (UInt32) -> Void = { _ in }
    /// Brings an open session window to the front, or closes it (set by the Mac app).
    public var focusWindow: (UInt32) -> Void = { _ in }
    public var closeWindow: (UInt32) -> Void = { _ in }

    private let defaults: UserDefaults

    init(app: AppModel, defaults: UserDefaults = .standard) {
        self.app = app
        self.defaults = defaults
        whenReady = WhenReady(rawValue: defaults.string(forKey: Self.whenReadyKey) ?? "") ?? .stay
        openIn = OpenIn(rawValue: defaults.string(forKey: Self.openInKey) ?? "") ?? .tab
    }

    // MARK: Reading the engine

    /// The sessions this app views as technician. Today the engine runs one session at a time; when it
    /// reports several (`remote_status.sessions`, see docs/mixel-remote-ui-engine-requests.md), this is
    /// the only place that reads them.
    func sessionTabs(_ status: RemoteStatus, frameShown: Bool) -> [SessionTab] {
        guard let peer = status.peer, !status.sharing, status.session != nil else { return [] }
        let state: String
        switch status.phase {
        case "active": state = frameShown ? "ready" : "starting"
        case "closing": state = "ending"
        case "outgoing", "negotiating": state = "connecting"
        default: return []
        }
        // A session tab exists once the other side accepted (the picture is coming).
        guard state != "connecting" else { return [] }
        return [SessionTab(peer: peer, name: app.devices.name(of: peer), os: app.devices.os(of: peer), state: state)]
    }

    func statusChanged(from previous: RemoteStatus, to next: RemoteStatus) {
        refresh(next)
    }

    /// Called when the remote status or the first picture changes.
    func refresh(_ status: RemoteStatus? = nil) {
        let status = status ?? app.remote
        let next = sessionTabs(status, frameShown: app.session.frame != nil)
        let before = Dictionary(uniqueKeysWithValues: tabs.map { ($0.peer, $0) })
        if next != tabs { tabs = next }
        let open = Set(next.map(\.peer))
        // Ended sessions: their tab, window and notice go away; the Devices list comes back.
        for peer in before.keys where !open.contains(peer) {
            if windows.contains(peer) { windows.remove(peer); closeWindow(peer) }
            readyNotices.removeAll { $0 == peer }
            if selected == .session(peer) { selected = .console }
        }
        updateAttempt(status)
        for tab in next where tab.state == "ready" && before[tab.peer]?.state != "ready" {
            becameReady(tab.peer)
        }
    }

    private func updateAttempt(_ status: RemoteStatus) {
        guard var current = attempt else { return }
        let matches = status.peer == current.peer
        var step = current.step
        if matches {
            switch status.phase {
            case "outgoing": step = max(step, 1)
            case "negotiating": step = max(step, 2)
            case "active": step = max(step, app.session.frame == nil ? 3 : 4)
            default: break
            }
        }
        if step == 4 {
            // Ready: the notice (or the session itself) takes over from the panel.
            attempt = nil
            return
        }
        let running = matches && status.inSession
        if current.failure.isEmpty, !app.busy, !running {
            if !app.error.isEmpty {
                current.failure = app.error
            } else if step >= 1 {
                // The request was sent and the session is gone again: declined, timed out or refused.
                current.failure = status.message.isEmpty || status.phase == "ready"
                    ? L("The connection was not established. The other side declined, did not answer, or unattended access is not allowed.")
                    : Translator.shared.translate(status.message)
            }
        }
        current.step = step
        if current != attempt { attempt = current }
    }

    private func becameReady(_ peer: UInt32) {
        let wantedWindow = attemptWindow.remove(peer) != nil
        if wantedWindow {
            show(peer, inWindow: true)
        } else if whenReady == .open {
            show(peer)
        } else if selected != .session(peer), !windows.contains(peer) {
            if !readyNotices.contains(peer) { readyNotices.append(peer) }
        }
    }

    private var attemptWindow: Set<UInt32> = []

    // MARK: Actions

    /// Connect from the Devices list or by ID; the person stays in Devices.
    public func connect(peer: UInt32, name: String? = nil, os: String = "", unattended: Bool, password: String? = nil, checkPhone: Bool) {
        attemptWindow.remove(peer)
        var next = Attempt(peer: peer, name: name ?? app.devices.name(of: peer), os: os.isEmpty ? app.devices.os(of: peer) : os,
                           started: Date(), step: 0, unattended: unattended, failure: "", inWindow: false)
        if app.busy {
            next.failure = L("Please wait a moment, the previous step is still running.")
            attempt = next
            return
        }
        attempt = next
        app.connect(to: peer, password: password, unattended: unattended, checkPhone: checkPhone)
    }

    /// "Open in window instead" while connecting.
    public func openAttemptInWindow() {
        guard var current = attempt else { return }
        current.inWindow = true
        attempt = current
        attemptWindow.insert(current.peer)
    }

    /// Cancel while connecting (ends the request for both sides), or close a failed attempt.
    public func cancelAttempt() {
        guard let current = attempt else { return }
        attempt = nil
        attemptWindow.remove(current.peer)
        if app.remote.peer == current.peer, app.remote.inSession { app.session.end() }
    }

    public func dismissNotice(_ peer: UInt32) { readyNotices.removeAll { $0 == peer } }

    /// Shows a session: in its window if it has one, else as the chosen default (tab or own window).
    public func show(_ peer: UInt32, inWindow: Bool? = nil) {
        guard tabs.contains(where: { $0.peer == peer }) else { return }
        readyNotices.removeAll { $0 == peer }
        if windows.contains(peer) { focusWindow(peer); return }
        if inWindow ?? (openIn == .window) {
            popOut(peer)
        } else {
            selected = .session(peer)
        }
    }

    /// Moves the session from its tab into its own window.
    public func popOut(_ peer: UInt32) {
        guard tabs.contains(where: { $0.peer == peer }) else { return }
        readyNotices.removeAll { $0 == peer }
        if selected == .session(peer) { selected = .console }
        if windows.insert(peer).inserted { openWindow(peer) } else { focusWindow(peer) }
    }

    /// Puts the session's window back into its tab (also when its window is closed).
    public func dock(_ peer: UInt32, select: Bool = true) {
        guard windows.remove(peer) != nil else { return }
        closeWindow(peer)
        if select, tabs.contains(where: { $0.peer == peer }) { selected = .session(peer) }
    }

    /// The session window was closed by the person (red button): it goes back into its tab.
    public func windowClosed(_ peer: UInt32) {
        guard windows.remove(peer) != nil else { return }
        if tabs.contains(where: { $0.peer == peer }) { selected = .session(peer) }
    }

    public func tab(_ peer: UInt32) -> SessionTab? { tabs.first { $0.peer == peer } }
    public func isInWindow(_ peer: UInt32) -> Bool { windows.contains(peer) }

    /// The session model of a tab. One today; one per session once the engine runs several.
    public func session(for peer: UInt32) -> SessionModel? {
        app.remote.peer == peer ? app.session : nil
    }

    /// Ends one session.
    public func end(_ peer: UInt32) {
        session(for: peer)?.end()
    }

    /// The device of a Remote ID is in a session with this app, or being connected.
    public func state(of peer: UInt32) -> String? {
        if let tab = tab(peer) { return tab.state }
        if attempt?.peer == peer, attempt?.failure.isEmpty == true { return "connecting" }
        return nil
    }
}
