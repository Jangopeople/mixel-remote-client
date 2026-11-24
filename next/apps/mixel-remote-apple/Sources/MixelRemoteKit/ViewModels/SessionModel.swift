#if os(macOS)
import AppKit
#endif
import CoreGraphics
import Foundation

/// One session on the technician's side: the remote picture, keyboard and mouse (or touch), and the
/// toolbar (displays, privacy screen, block input, pointer, lock, Ctrl+Alt+Del, restart, sound,
/// clipboard, files, chat, recording, invite a colleague, switch sides, end).
///
/// The app holds one model per computer it views (`AppModel.sessionModels`, keyed by the computer's
/// Remote ID); each is bound to its session's stable id (`sessionId`), which every engine call carries.
/// Input, clipboard and toolbar actions only go out while this session is the focused one
/// (`WorkspaceModel.isFocused`). The model without a computer (`peer == nil`, `AppModel.idleSession`)
/// stands for "no session" and for the session this computer shares (it ends that one by consent).
@MainActor
public final class SessionModel: ObservableObject {
    // The app model lives as long as the app; its parts keep it (no reference cycle matters there).
    let app: AppModel
    #if os(macOS)
    /// Latest decoded picture. Intentionally NOT `@Published` on macOS so 30–60 fps video updates never force
    /// `SessionStage`, `SessionBar`, `ViewThatFits`, or tool panels to re-evaluate on every frame.
    /// `RemoteScreenView` subscribes directly via `onFrame` and paints `CALayer.contents` on the GPU.
    public private(set) var frame: CGImage?
    #else
    @Published public private(set) var frame: CGImage?
    #endif
    /// Flips once when the first video frame arrives (`false -> true`) and on `clear()` (`true -> false`).
    @Published public private(set) var hasFrame = false
    /// Direct frame sink for the native `RemoteScreenView` (Mac) or canvas — avoids SwiftUI diffing per frame.
    public var onFrame: ((CGImage?) -> Void)?
    @Published public private(set) var size = CGSize.zero
    @Published public private(set) var fps = 0
    /// W-1: True only when the engine reports neither video frames nor idle-liveness notifications for >3 s.
    public var pictureStalled: Bool { remote.videoStalled == true }
    @Published public private(set) var stopping = false
    @Published public var error = ""
    @Published public var notice = ""
    // Toolbar toggles of this session.
    @Published public private(set) var privacy = false
    @Published public private(set) var blockInput = false
    @Published public private(set) var lockWhenDone = false
    @Published public var pointerMode = false
    @Published public private(set) var sound = true
    @Published public private(set) var voice = false
    @Published public private(set) var restartArmed = false
    /// When enabled, sends printable characters using the technician's local keyboard layout instead of raw HID scancodes.
    @Published public var matchLocalKeyboard = false
    /// True while a local file is being dragged over the remote screen.
    @Published public var draggingFiles = false
    /// Active quality preference: "auto", "speed", or "quality".
    @Published public private(set) var qualityMode = "auto"
    /// True for 1:1 Actual Size view on macOS (false = Fit to window).
    @Published public var actualSize = false
    // Tools.
    @Published public private(set) var chat: [ChatMessage] = []
    @Published public var chatDraft = ""
    @Published public private(set) var files: FileView?
    @Published public var clipboardResult = ""
    @Published public var textToSend = ""
    @Published public private(set) var recordingPending = false
    @Published public private(set) var meetingNotice = ""
    @Published public var dismissedMeetingRoom = ""
    /// When the running recording started (for "REC 00:42" over the picture).
    @Published public private(set) var recordingSince: Date?

    /// Where the session picture is decoded: on this device (VideoToolbox) or by the engine.
    public enum Decoding: String, Sendable { case none, device, engine }
    @Published public private(set) var decoding = Decoding.none
    /// Decode H.264 on this device (tests and screenshots may turn it off).
    public var useHardwareDecoding = true

    private var video: Task<Void, Never>?
    private var tools: Task<Void, Never>?
    private var viewingSession: String?
    private var toggleSession: String?
    private var stoppedConsent: String?
    private var shownTimes: [Date] = []
    private var lastFpsPublish = Date.distantPast
    private var lastFrameAt: Date?
    private var inputQueue: [InputEvent] = []
    private var sending = false
    private var lastPointerSend = Date.distantPast
    private var pointerFlushTask: Task<Void, Never>?
    private var reconnect: Reconnect?
    private var restartArmTask: Task<Void, Never>?
    private var clipboardSyncing = false
    private var clipboardPullTask: Task<Void, Never>?
    private var lastAutoClipboardPull = Date.distantPast
    /// Automatic bidirectional clipboard sync (enabled by default in the app; tests can configure or inspect it).
    public var autoClipboardSync = false
    #if os(macOS)
    private var lastPasteboardChangeCount: Int = NSPasteboard.general.changeCount
    #endif

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

    /// The Remote ID of the computer this session views (`nil`: the model of no session).
    public let peer: UInt32?
    /// The session's stable id (what every engine call carries).
    public private(set) var sessionId: String?
    /// This session's own drawing board.
    public lazy var drawing = DrawingModel(owner: self)
    /// The engine's last word on this session.
    public private(set) var item: RemoteSessionItem?
    private var lastStatus: RemoteStatus?

    init(app: AppModel, peer: UInt32? = nil) {
        self.app = app
        self.peer = peer
    }

    /// The status as this session sees it: the single-session fields describe this session.
    public var remote: RemoteStatus {
        guard peer != nil else { return app.remote }
        if let item { return app.remote.describing(item) }
        return lastStatus ?? app.remote
    }
    /// The id engine calls carry: the stable session id (the shared session's id for the idle model).
    public var session: String? { peer == nil ? remote.session : sessionId }
    /// This session is the one keyboard, mouse, clipboard and toolbar actions go to.
    public var focused: Bool {
        guard let peer else { return true }
        return app.workspace.isFocused(peer)
    }
    /// The picture is shown and keyboard and mouse go to the other computer.
    public var controllable: Bool {
        !stopping && remote.viewing && remote.control && frame != nil && !locallyStopped
    }

    var lastRequestUnattended: Bool {
        get { app.requestFlags(for: peer).unattended }
        set { app.setRequestFlags(for: peer) { $0.unattended = newValue } }
    }
    /// The request this app sent carried a one-time password (the engine may not report it itself yet).
    var lastRequestPassword: Bool {
        get { app.requestFlags(for: peer).password }
        set { app.setRequestFlags(for: peer) { $0.password = newValue } }
    }
    /// What the person sees of the link to the other computer: live, asking again (the computer went away),
    /// or given up after a minute (Retry or End).
    public enum Link: Equatable, Sendable { case live, reconnecting, failed }
    public var link: Link {
        guard let item else { return .live }
        return item.reconnectFailed ? .failed : item.reconnecting ? .reconnecting : .live
    }

    /// "Retry" after "Could not reconnect": the engine asks the same computer again for another minute.
    /// Carries the stable id (the status's own `session` is empty while the session retries).
    public func retryReconnect() {
        guard link == .failed, let id = sessionId else { return }
        error = ""
        Task {
            do { try await app.engine.perform("remote_reconnect", ["session": id]) }
            catch { self.error = AppModel.message(error) }
            await app.refreshRemote()
        }
    }

    var locallyStopped: Bool { remote.consent != nil && remote.consent == stoppedConsent }

    /// The model of no session follows the shared session (this computer is helped) and nothing else.
    func statusChanged(from previous: RemoteStatus, to next: RemoteStatus) {
        guard peer == nil else { return }
        if next.session != toggleSession {
            toggleSession = next.session
            privacy = false; blockInput = false; lockWhenDone = false; pointerMode = false; sound = true
            chat = []; files = nil; clipboardResult = ""
        }
        if viewingSession != nil { clear() }
        if !next.inSession { stoppedConsent = nil }
    }

    /// The engine listed this session: bind to its stable id and follow it.
    func update(_ item: RemoteSessionItem, status: RemoteStatus) {
        guard peer != nil else { return }
        self.item = item
        let next = status.describing(item)
        lastStatus = next
        if item.id != toggleSession {
            toggleSession = item.id
            sessionId = item.id
            privacy = false; blockInput = false; lockWhenDone = false; pointerMode = false; sound = true
            chat = []; files = nil; clipboardResult = ""; stoppedConsent = nil
            lastAutoClipboardPull = .distantPast
            #if os(macOS)
            lastPasteboardChangeCount = NSPasteboard.general.changeCount
            #endif
            if viewingSession != nil { clear() }
        }
        // Several sessions run side by side: each keeps fetching its own pictures, so a tab that comes
        // to the front shows a current picture at once (the engine keeps only the newest per session).
        if next.viewing, !stopping, !locallyStopped {
            if viewingSession != item.id { startVideo(item.id) }
        } else if viewingSession != nil {
            clear()
        }
        let recording = next.recording?.running == true
        if recording, recordingSince == nil { recordingSince = Date() } else if !recording, recordingSince != nil { recordingSince = nil }
        if next.phase == "active" || next.phase == "outgoing" { startTools() }
        else { tools?.cancel(); tools = nil }
        continueReconnect(status)
    }

    /// The engine no longer lists this session (it ended, or the other side left).
    func ended(status: RemoteStatus) {
        let was = item != nil
        item = nil
        sessionId = nil
        toggleSession = nil
        tools?.cancel(); tools = nil
        clipboardPullTask?.cancel(); clipboardPullTask = nil
        clear()
        stoppedConsent = nil
        stopping = false
        if was || reconnect != nil { continueReconnect(status) }
    }

    /// An ended session's model is kept while it still waits for its computer to restart.
    var keepsAlive: Bool { reconnect != nil }

    /// Stops the picture and forgets it.
    public func clear() {
        video?.cancel()
        video = nil
        viewingSession = nil
        let had = frame != nil
        frame = nil
        if hasFrame { hasFrame = false }
        onFrame?(nil)
        size = .zero
        fps = 0
        lastFrameAt = nil
        shownTimes.removeAll()
        lastFpsPublish = .distantPast
        decoding = .none
        inputQueue.removeAll()
        pointerFlushTask?.cancel()
        pointerFlushTask = nil
        if had { app.workspace.refresh() }
    }

    private func startVideo(_ session: String) {
        clear()
        viewingSession = session
        let hardware = useHardwareDecoding
        let engine = app.engine
        video = Task.detached(priority: .userInitiated) { [weak self] in
            var sequence: UInt64 = 0
            var emptyPolls = 0
            // H.264 is decoded on this background worker (VideoToolbox) when it works; otherwise the engine decodes.
            var decoder: H264Decoder? = hardware ? H264Decoder() : nil
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    let data = try await engine.nextVideo(session: session, compressed: decoder != nil)
                    if Task.isCancelled { return }
                    if data.isEmpty {
                        // R-5 / Q-4: Adaptive idle backoff avoids 500 Hz FFI spinning on still screens
                        // while snapping back to 4 ms latency on the first motion frame.
                        emptyPolls = min(emptyPolls + 1, 100)
                        if emptyPolls == 35 {
                            await MainActor.run {
                                guard self.viewingSession == session, let last = self.lastFrameAt else { return }
                                if Date().timeIntervalSince(last) >= 1.5, self.fps != 0 {
                                    self.fps = 0
                                }
                            }
                        }
                        let delayNs: UInt64 = emptyPolls <= 5 ? 4_000_000 : emptyPolls <= 20 ? 12_000_000 : 33_000_000
                        try? await Task.sleep(nanoseconds: delayNs)
                        continue
                    }
                    emptyPolls = 0
                    if VideoFrame.isCompressed(data) {
                        guard let active = decoder else { continue }
                        do {
                            var latest = try active.decode(data)
                            var decodedCount = 1
                            var fallbackRaw: Data?
                            // Drain up to 3 already-queued compressed frames if the queue had a burst.
                            for _ in 0..<3 {
                                let nextData = (try? await engine.nextVideo(session: session, compressed: true)) ?? Data()
                                if nextData.isEmpty || Task.isCancelled { break }
                                if VideoFrame.isCompressed(nextData) {
                                    do {
                                        latest = try active.decode(nextData)
                                        decodedCount += 1
                                    } catch H264Decoder.Problem.waitingForKeyframe {
                                        continue
                                    } catch {
                                        // Surface decoder failure after showing the last good frame so we fall back cleanly.
                                        decoder = nil
                                        await MainActor.run {
                                            if self.viewingSession == session { self.decoding = .engine }
                                        }
                                        break
                                    }
                                } else {
                                    fallbackRaw = nextData
                                    break
                                }
                            }
                            sequence = 0
                            let frameToShow = latest
                            let frameCount = decodedCount
                            let deviceDecoding = decoder != nil
                            await MainActor.run {
                                guard self.viewingSession == session else { return }
                                if deviceDecoding, self.decoding != .device { self.decoding = .device }
                                self.show(frameToShow, count: frameCount)
                            }
                            if let raw = fallbackRaw, let decodedRaw = try? VideoFrame.decode(raw, after: sequence) {
                                sequence = decodedRaw.sequence
                                await MainActor.run {
                                    guard self.viewingSession == session else { return }
                                    self.show(decodedRaw, count: 1)
                                }
                            }
                        } catch H264Decoder.Problem.waitingForKeyframe {
                            continue
                        } catch {
                            // This device could not decode the stream: the engine decodes from now on.
                            decoder = nil
                            await MainActor.run {
                                if self.viewingSession == session { self.decoding = .engine }
                            }
                        }
                        continue
                    }
                    let decoded = try VideoFrame.decode(data, after: sequence)
                    sequence = decoded.sequence
                    let isEngine = decoder == nil
                    await MainActor.run {
                        guard self.viewingSession == session else { return }
                        if isEngine { self.decoding = .engine }
                        self.show(decoded, count: 1)
                    }
                } catch is CancellationError {
                    return
                } catch VideoFrame.Problem.compressed {
                    continue
                } catch {
                    await MainActor.run {
                        guard self.viewingSession == session else { return }
                        self.error = error is VideoFrame.Problem ? L("Invalid native video frame") : AppModel.message(error)
                        Task { try? await self.app.engine.perform("remote_close", ["session": session]) }
                        self.clear()
                    }
                    return
                }
            }
        }
    }

    private func show(_ decoded: VideoFrame, count: Int = 1) {
        let first = frame == nil
        frame = decoded.image
        onFrame?(decoded.image)
        if first {
            hasFrame = true
            app.workspace.refresh()
        }
        let next = CGSize(width: decoded.width, height: decoded.height)
        if next != size { size = next }
        let now = Date()
        lastFrameAt = now
        for _ in 0..<max(1, count) {
            shownTimes.append(now)
        }
        shownTimes.removeAll { now.timeIntervalSince($0) > 1 }
        // Q-4: Publish FPS at most once a second (or on first frame) so SwiftUI toolbars stay quiet.
        if first || now.timeIntervalSince(lastFpsPublish) >= 1.0 {
            lastFpsPublish = now
            if fps != shownTimes.count { fps = shownTimes.count }
        }
    }

    /// For screenshots and tests: show this picture without a running engine.
    public func showSample(_ data: Data) {
        if let decoded = try? VideoFrame.decode(data) { show(decoded) }
    }

    // MARK: Input

    /// Queues an input event; pointer moves and wheel scrolls coalesce with the previous unsent one.
    public func send(_ event: InputEvent) {
        guard let session else { return }
        if case .releaseAll = event {} else if !controllable || !focused { return }
        if case .pointer = event, case .pointer = inputQueue.last {
            inputQueue[inputQueue.count - 1] = event
        } else if case let .wheel(h1, v1) = event, case let .wheel(h0, v0) = inputQueue.last {
            let h = Int16(clamping: Int(h0) + Int(h1))
            let v = Int16(clamping: Int(v0) + Int(v1))
            inputQueue[inputQueue.count - 1] = .wheel(horizontal: max(-120, min(120, h)), vertical: max(-120, min(120, v)))
        } else if inputQueue.count >= 64 {
            if let idx = inputQueue.firstIndex(where: { if case .pointer = $0 { return true }; if case .wheel = $0 { return true }; return false }) {
                inputQueue.remove(at: idx)
                inputQueue.append(event)
            } else {
                // Drop oldest queued event rather than terminating the session on key-repeat bursts.
                inputQueue.removeFirst()
                inputQueue.append(event)
            }
        } else {
            inputQueue.append(event)
        }
        if case .pointer = event {
            // W-2: Coalesce pointer moves and rate-limit to at most 1 per 8 ms so 120 Hz trackpad
            // movement never saturates the input transport while clicks/keys flush immediately.
            let elapsed = Date().timeIntervalSince(lastPointerSend)
            if elapsed < 0.008 {
                if pointerFlushTask == nil {
                    let waitNs = UInt64(max(1_000_000, (0.008 - elapsed) * 1_000_000_000))
                    pointerFlushTask = Task { @MainActor [weak self] in
                        try? await Task.sleep(nanoseconds: waitNs)
                        guard let self else { return }
                        self.pointerFlushTask = nil
                        if let current = self.session { self.flush(current) }
                    }
                }
                return
            }
            lastPointerSend = Date()
        }
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
                    if case .pointer = event { continue }
                    if case .wheel = event { continue }
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
        guard let session, focused else { return }
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
    public func toggleVoice() { voice.toggle(); action(voice ? "voice:on" : "voice:off", quiet: true) }
    public func setQualityMode(_ mode: String) {
        guard ["auto", "speed", "quality"].contains(mode), mode != qualityMode else { return }
        qualityMode = mode
        action("quality:\(mode)", quiet: true)
    }
    public func lockNow() { action("lock") }
    public func ctrlAltDel() { action("sas") }
    public func selectDisplay(_ index: UInt16) {
        guard let session, focused else { return }
        Task {
            do { _ = try await app.engine.send(input: .selectDisplay(index), session: session) }
            catch { self.error = AppModel.message(error) }
        }
    }

    /// The first click arms, the second (within 5 s) restarts the remote computer; the app reconnects
    /// when it is back (up to 10 minutes).
    public func restartComputer() {
        guard let session, focused, let peer = remote.peer else { return }
        if !restartArmed {
            restartArmed = true
            restartArmTask?.cancel()
            restartArmTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                if !Task.isCancelled { self?.restartArmed = false }
            }
            return
        }
        restartArmTask?.cancel()
        restartArmTask = nil
        restartArmed = false
        let now = Date()
        reconnect = Reconnect(host: peer, signin: remote.signinId, unattended: lastRequestUnattended || remote.unattendedSession, until: now.addingTimeInterval(600), since: now, session: session)
        action("restart")
    }

    /// `status` is the whole engine status: the restart plan follows this computer's session in it.
    private func continueReconnect(_ status: RemoteStatus) {
        guard var plan = reconnect else { return }
        if let item {
            // The session of this computer is listed: the same one is still going down; a new one is the reconnect.
            if item.id != plan.session { reconnect = nil; notice = "" }
            return
        }
        if let other = status.viewerSessions.first(where: { $0.peer == plan.host }) {
            if other.phase == "active", other.id != plan.session { reconnect = nil; notice = "" }
            if other.phase != "closing" { return }
        }
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
        if let target, status.canRequestSession, !app.busy, now.timeIntervalSince(plan.lastTry) > 15 {
            plan.lastTry = now
            let unattended = plan.unattended || target == plan.signin
            app.act {
                struct Request: Encodable { var host: UInt32; var password: String?; var unattended: Bool }
                _ = try await self.app.engine.run("remote_request", Request(host: target, password: nil, unattended: unattended), as: JSONValue.self)
            }
        }
        reconnect = plan
    }

    /// Renew the connection (new keys, same session).
    public func renew() {
        guard let session, focused else { return }
        app.act { try await self.app.engine.perform("remote_restart", ["session": session]) }
    }

    /// Ends the session for both sides.
    public func end() {
        guard !stopping else { return }
        if peer == nil {
            // The session this computer shares: ended by its consent.
            guard let consent = remote.consent else { return }
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
            return
        }
        // A technician session: only this one ends (the others keep running).
        guard let id = sessionId else { return }
        stoppedConsent = remote.consent
        stopping = true
        clear()
        error = ""
        Task {
            do { try await app.engine.perform("remote_close", ["session": id]) }
            catch { self.error = L("Could not confirm that the session ended. Try End session again.") + " " + AppModel.message(error) }
            stopping = false
            await app.refreshRemote()
        }
    }

    // MARK: Clipboard, chat, files

    public func clipboard(push: Bool) {
        guard let session, focused else { return }
        clipboardResult = L("Transferring…")
        Task {
            await performClipboardSync(session: session, push: push, explicit: true)
        }
    }

    @discardableResult
    func performClipboardSync(session: String, push: Bool, explicit: Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(3)
        while clipboardSyncing {
            if Date() >= deadline || Task.isCancelled { return false }
            try? await Task.sleep(nanoseconds: 15_000_000)
            guard self.session == session else { return false }
        }
        guard self.session == session else { return false }
        clipboardSyncing = true
        defer { clipboardSyncing = false }
        #if os(macOS)
        if push {
            lastPasteboardChangeCount = NSPasteboard.general.changeCount
        }
        #endif
        do {
            try await app.engine.perform("remote_clipboard", ["session": session, "push": push])
            #if os(macOS)
            lastPasteboardChangeCount = NSPasteboard.general.changeCount
            #endif
            if explicit {
                clipboardResult = push ? L("Remote clipboard updated.") : L("Your clipboard was updated.")
            }
            return true
        } catch {
            if explicit {
                clipboardResult = AppModel.message(error)
            }
            return false
        }
    }

    /// The shortcut modifier expected by the remote OS: Control on Windows/Linux, Command on macOS.
    public var remoteShortcutModifier: UInt16 {
        if let peer, let os = (app.workspace.tab(peer)?.os ?? app.devices.devices.first(where: { $0.connectionId == peer })?.os)?.lowercased(),
           os.contains("win") || os.contains("linux") {
            return KeyMap.control
        }
        if remote.offers("sas") { return KeyMap.control }
        return KeyMap.command
    }

    /// Pushes the local clipboard to the remote host (if it changed since the last sync or `forcePush` is true)
    /// and then sends the paste keystroke (`usage` = 25 for `V`) with `modifiers` held around it.
    public func pasteToRemote(usage: UInt16 = 25, modifiers: [UInt16] = [], forcePush: Bool = true) {
        guard let session, controllable, focused else { return }
        let status = remote
        let shouldPush: Bool = {
            guard status.clipboard, status.phase == "active" else { return false }
            #if os(macOS)
            if forcePush || NSPasteboard.general.changeCount != lastPasteboardChangeCount { return true }
            return false
            #else
            return forcePush
            #endif
        }()
        if !shouldPush {
            #if os(macOS)
            if !status.clipboard, status.control, status.phase == "active",
               let text = NSPasteboard.general.string(forType: .string), !text.isEmpty, text.count <= 32_768 {
                if !modifiers.isEmpty { send(.releaseAll) }
                send(.text(text))
                return
            }
            #endif
            press(usage, modifiers: modifiers)
            return
        }
        Task {
            _ = await performClipboardSync(session: session, push: true, explicit: false)
            guard self.session == session, self.controllable, self.focused else { return }
            self.press(usage, modifiers: modifiers)
        }
    }

    /// Sends a copy/cut keystroke (`usage` = 6 for `C`, 27 for `X`) with `modifiers` held around it
    /// and pulls the remote clipboard onto the local Mac shortly after the remote app updates its clipboard.
    public func copyFromRemote(usage: UInt16, modifiers: [UInt16] = []) {
        guard controllable, focused else { return }
        press(usage, modifiers: modifiers)
        pullClipboardSoon()
    }

    /// Schedules a quick non-clobbering pull of the remote clipboard after a remote copy action.
    public func pullClipboardSoon() {
        guard let session, focused, remote.clipboard, remote.phase == "active" else { return }
        clipboardPullTask?.cancel()
        clipboardPullTask = Task { [weak self] in
            for delayNs: UInt64 in [140_000_000, 320_000_000] {
                try? await Task.sleep(nanoseconds: delayNs)
                if Task.isCancelled { return }
                guard let self, self.session == session, self.focused, self.remote.clipboard, self.remote.phase == "active" else { return }
                _ = await self.performClipboardSync(session: session, push: false, explicit: false)
                self.lastAutoClipboardPull = Date()
            }
        }
    }

    /// Automatic bidirectional clipboard sync: pushes local pasteboard changes to the remote host,
    /// and periodically pulls remote clipboard changes (deduplicated by digest in the engine).
    public func syncClipboardAutomatically(forcePull: Bool = false) async {
        let status = remote
        guard let session, focused, status.phase == "active", status.clipboard, status.controlPending != true else { return }
        #if os(macOS)
        let currentCount = NSPasteboard.general.changeCount
        if currentCount != lastPasteboardChangeCount {
            _ = await performClipboardSync(session: session, push: true, explicit: false)
            return
        }
        #endif
        let now = Date()
        if forcePull || now.timeIntervalSince(lastAutoClipboardPull) >= 1.2 {
            lastAutoClipboardPull = now
            _ = await performClipboardSync(session: session, push: false, explicit: false)
        }
    }

    private func startTools() {
        guard tools == nil else { return }
        autoClipboardSync = !(app.engine is MockEngine)
        tools = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.readTools()
                try? await Task.sleep(nanoseconds: 700_000_000)
                if Task.isCancelled { return }
                if self.autoClipboardSync {
                    await self.syncClipboardAutomatically()
                }
            }
        }
    }

    func readTools() async {
        let status = remote
        if let session, status.phase == "active" {
            if let messages = try? await app.engine.run("remote_chat_messages", ["session": session], as: [ChatMessage]?.self) ?? nil, messages != chat { chat = messages }
        }
        if let consent = status.consent, status.files, status.phase == "active" {
            let view: FileView? = try? await app.engine.run("remote_file_status", ["consent": consent])
            if view != files { files = view }
        } else if files != nil { files = nil }
    }

    public func sendChat() {
        let text = chatDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let session, focused, !text.isEmpty else { return }
        chatDraft = ""
        Task {
            do { try await app.engine.perform("remote_chat_send", ["session": session, "text": text]); await readTools() }
            catch { self.error = AppModel.message(error) }
        }
    }

    /// Sends the file at `path` (chosen in the app's Open panel).
    public func sendFile(path: String) {
        guard let consent = remote.consent, focused else { return }
        Task {
            do { try await app.engine.perform("remote_file_send", ["consent": consent, "path": path]); await readTools() }
            catch { self.error = AppModel.message(error) }
        }
    }

    /// Sends the file once the session's file channel is up (it opens a moment after the session; up to 6 s).
    func sendFileWhenConnected(path: String) async {
        for _ in 0..<30 where files?.connected != true {
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        sendFile(path: path)
    }

    /// The suggested name of a file the other side offers (for the Save panel).
    public func offeredName(_ id: String) async -> String? {
        guard let consent = remote.consent else { return nil }
        return try? await app.engine.run("remote_file_pending", ["consent": consent, "id": id])
    }

    public func acceptFile(_ id: String, path: String) {
        guard let consent = remote.consent, focused else { return }
        Task {
            do { try await app.engine.perform("remote_file_accept", ["consent": consent, "id": id, "path": path]); await readTools() }
            catch { self.error = AppModel.message(error) }
        }
    }

    public func cancelFile(_ id: String) {
        guard let consent = remote.consent, focused else { return }
        Task { try? await app.engine.perform("remote_file_cancel", ["consent": consent, "id": id]); await readTools() }
    }

    // MARK: Recording

    /// "The recording is in 2 files: ..." once a picture size change started `name-part2.mp4`; empty otherwise.
    public var recordingPartsText: String {
        let parts = remote.recordingParts ?? []
        return parts.count > 1 ? L("The recording is in %d files: name, then name-part2, ...", parts.count) : ""
    }

    public var recordingText: String {
        switch remote.recording?.phase {
        case "waiting_for_video": L("Waiting for a complete video frame…")
        case "recording": L("Recording video")
        case "saving": L("Saving recording…")
        case "saved":
            lastRecordingPath.isEmpty
                ? L("Recording saved")
                : L("Saved: %@", URL(fileURLWithPath: lastRecordingPath).lastPathComponent)
        case "saved_partial": L("The recording stopped early. The playable portion was saved.")
        case "error": Translator.shared.translate(remote.recording?.message ?? "")
        default: ""
        }
    }

    @Published public private(set) var lastRecordingPath: String = ""

    public func mayRecord() async -> Bool {
        guard let session, focused else { return false }
        return (try? await app.engine.run("remote_record_may_start", ["session": session], as: Bool.self)) ?? false
    }

    public func startRecording(path: String) {
        guard let session, focused else { return }
        lastRecordingPath = path
        recordingPending = true
        Task {
            do { try await app.engine.perform("remote_record_start", ["session": session, "path": path]) }
            catch { self.error = AppModel.message(error) }
            recordingPending = false
            await app.refreshRemote()
        }
    }

    public func stopRecording() {
        // W-3: Never drop Stop when the session window is unfocused or Stop is clicked repeatedly.
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
        guard let session, focused else { return }
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
        remote.phase == "active" && remote.peerFeatures.contains("switch") && (peer == nil || app.remote.viewerSessions.count <= 1)
    }

    public func switchSides() {
        guard let session, focused else { return }
        app.act { try await self.app.engine.perform("remote_switch_sides", ["session": session]) }
    }

    /// Show my screen (older computers without switching sides): a Mixel Meet room the other side is
    /// asked to watch.
    public func showMyScreen() {
        guard let session, focused else { return }
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
