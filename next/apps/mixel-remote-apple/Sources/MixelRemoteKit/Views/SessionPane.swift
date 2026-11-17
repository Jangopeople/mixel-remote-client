#if os(macOS)
import AppKit
import SwiftUI

/// One session in the "Pro Console" look, in its tab or in its own window: the session toolbar
/// (device, monitors, Ctrl+Alt+Del, picture quality, Files, Chat, Draw, Record, Report, more,
/// full screen, own window / back into a tab, End), the remote screen and the tool panel.
public struct SessionPane: View {
    @ObservedObject var app: AppModel
    @ObservedObject var workspace: WorkspaceModel
    let peer: UInt32
    let inWindow: Bool

    public init(app: AppModel, peer: UInt32, inWindow: Bool) {
        self.app = app
        workspace = app.workspace
        self.peer = peer
        self.inWindow = inWindow
    }

    public var body: some View {
        if let session = workspace.session(for: peer), let tab = workspace.tab(peer) {
            SessionStage(app: app, session: session, drawing: session.drawing, workspace: workspace, tab: tab, inWindow: inWindow)
        } else {
            VStack(spacing: 10) {
                Image(systemName: "macwindow.badge.plus").font(.system(size: 34)).foregroundStyle(.secondary)
                Text(verbatim: L("This session has ended.")).font(.title3.weight(.semibold))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.page)
        }
    }
}

struct SessionStage: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel
    @ObservedObject var drawing: DrawingModel
    @ObservedObject var workspace: WorkspaceModel
    let tab: WorkspaceModel.SessionTab
    let inWindow: Bool
    @State private var tool: SessionWorkspaceView.Tool?
    @State private var inviting = false

    var remote: RemoteStatus { session.remote }

    var body: some View {
        VStack(spacing: 0) {
            ViewThatFits(in: .horizontal) {
                SessionBar(app: app, session: session, drawing: drawing, workspace: workspace, tab: tab, inWindow: inWindow, compact: false, tool: $tool, inviting: $inviting)
                SessionBar(app: app, session: session, drawing: drawing, workspace: workspace, tab: tab, inWindow: inWindow, compact: true, tool: $tool, inviting: $inviting)
            }
            Rectangle().fill(Theme.hairline).frame(height: 1)
            if app.remote.isReconnecting {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(verbatim: L("Reconnecting… the session stays open")).font(.system(size: 12, weight: .medium))
                    Spacer()
                }
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(Theme.updateSoft)
                .accessibilityElement(children: .combine)
            }
            SessionNoticeBanner(app: app, session: session)
            HStack(spacing: 0) {
                screen
                if let tool {
                    Rectangle().fill(Theme.hairline).frame(width: 1)
                    VStack(spacing: 0) {
                        HStack {
                            Text(verbatim: tool == .chat ? L("Chat with %@", tab.name) : tool.title).font(.headline)
                            Spacer()
                            Button { self.tool = nil } label: { Image(systemName: "xmark") }
                                .buttonStyle(.plain).foregroundStyle(.secondary)
                                .help(L("Close"))
                                .accessibilityLabel(Text(verbatim: L("Close")))
                        }
                        .padding(.horizontal, 16).padding(.vertical, 12)
                        Divider()
                        ToolPanel(app: app, session: session, tool: tool, showsTitle: false)
                    }
                    .frame(width: 320)
                    .background(Theme.page)
                }
            }
        }
        .background(Theme.page)
        .sheet(isPresented: $inviting) { InviteColleagueSheet(app: app, session: session) }
    }

    @ViewBuilder private var screen: some View {
        ZStack {
            Theme.stage
            RemoteScreen(session: session)
            if session.frame != nil, session.focused { DrawingOverlay(drawing: drawing, pictureSize: session.size) }
            switch session.link {
            case .reconnecting:
                VStack(spacing: 12) {
                    ProgressView().controlSize(.large).tint(.white)
                    Text(verbatim: L("Reconnecting…")).font(.title3.weight(.semibold)).foregroundStyle(.white)
                    Text(verbatim: L("The session stays open while Mixel Remote asks that computer again.")).font(.callout).foregroundStyle(.white.opacity(0.75))
                }
                .accessibilityElement(children: .combine)
            case .failed:
                VStack(spacing: 12) {
                    Image(systemName: "wifi.exclamationmark").font(.system(size: 34)).foregroundStyle(.white.opacity(0.85))
                    Text(verbatim: L("Could not reconnect")).font(.title3.weight(.semibold)).foregroundStyle(.white)
                    HStack(spacing: 10) {
                        Button(L("Retry")) { session.retryReconnect() }
                            .buttonStyle(PrimaryButtonStyle())
                            .accessibilityLabel(Text(verbatim: L("Retry")))
                        Button(L("End session")) { session.end() }
                            .disabled(session.stopping)
                    }
                }
            case .live:
                if session.frame == nil {
                    VStack(spacing: 12) {
                        ProgressView().controlSize(.large).tint(.white)
                        Text(verbatim: L("Starting video")).foregroundStyle(.white.opacity(0.85))
                    }
                }
            }
            VStack {
                HStack(alignment: .top) {
                    if drawing.on {
                        DrawingControls(drawing: drawing)
                            .padding(8)
                            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    Spacer()
                    if let since = session.recordingSince, remote.recording?.running == true {
                        RecordingBadge(since: since)
                    }
                }
                .padding(14)
                Spacer()
                if !session.notice.isEmpty || !session.error.isEmpty || !session.meetingNotice.isEmpty || !session.recordingText.isEmpty && remote.recording?.running != true {
                    VStack(alignment: .leading, spacing: 4) {
                        if !session.notice.isEmpty { Text(verbatim: session.notice) }
                        if !session.meetingNotice.isEmpty { Text(verbatim: session.meetingNotice) }
                        if !session.recordingText.isEmpty, remote.recording?.running != true { Text(verbatim: session.recordingText) }
                        if !session.error.isEmpty { Text(verbatim: session.error).foregroundStyle(Color(light: 0xFFB4BD, dark: 0xFFB4BD)) }
                    }
                    .font(.callout)
                    .foregroundStyle(.white)
                    .padding(10)
                    .background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 10))
                    .padding(12)
                }
            }
        }
        .frame(minWidth: 480, minHeight: 320)
    }
}

/// The banner under the toolbar for a session opened with a one-time password (with its remaining time and
/// the 5 minute and 1 minute warnings) or without anyone at the other computer. Nothing for other sessions.
struct SessionNoticeBanner: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel

    var body: some View {
        let status = session.remote
        if status.passwordSession != nil || session.lastRequestPassword || status.unattendedSession {
            TimelineView(.periodic(from: Date(), by: 1)) { context in
                let ends = status.passwordSession?.endsAt.flatMap(Format.parseDate)
                if let notice = SessionNotice.make(passwordSession: status.passwordSession != nil, sentPassword: session.lastRequestPassword,
                                                   unattended: status.unattendedSession, endsAt: ends, now: context.date) {
                    SessionNoticeBar(notice: notice)
                }
            }
        }
    }
}

struct SessionNoticeBar: View {
    let notice: SessionNotice

    private var text: String {
        switch notice.kind {
        case .unattended:
            return L("Unattended session: nobody at that computer was asked.")
        case .password(let seconds):
            guard let seconds else { return L("Opened with a one-time password. Such a session ends by itself after at most 2 hours.") }
            if seconds == 0 { return L("The time limit of this password session is reached. The session ends now.") }
            if notice.level == .urgent { return L("This session ends in less than a minute (%@).", SessionNotice.clock(seconds)) }
            if notice.level == .warning { return L("This session ends in %@. Ask for a new password to continue.", SessionNotice.clock(seconds)) }
            return L("Opened with a one-time password. The session ends by itself in %@.", SessionNotice.clock(seconds))
        }
    }

    private var colors: (Color, Color) {
        switch notice.level {
        case .info: (Theme.infoText, Theme.infoSoft)
        case .warning: (Theme.updateText, Theme.updateSoft)
        case .urgent: (Theme.brand, Theme.brandSoft)
        }
    }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: notice.level == .info ? "key.fill" : "clock.badge.exclamationmark")
            Text(verbatim: text).font(.system(size: 12, weight: .semibold)).monospacedDigit()
            Spacer()
        }
        .foregroundStyle(colors.0)
        .padding(.horizontal, 14).padding(.vertical, 6)
        .background(colors.1)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.hairline).frame(height: 1) }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(notice.level == .info ? AccessibilityTraits() : .updatesFrequently)
    }
}

/// "● REC 00:42" over the picture while a recording runs.
struct RecordingBadge: View {
    let since: Date
    var body: some View {
        TimelineView(.periodic(from: since, by: 1)) { context in
            let seconds = max(0, Int(context.date.timeIntervalSince(since)))
            HStack(spacing: 6) {
                Circle().fill(Color(light: 0xFF4D5E, dark: 0xFF4D5E)).frame(width: 8, height: 8)
                Text(verbatim: String(format: "REC %02d:%02d", seconds / 60, seconds % 60)).font(.system(size: 12, weight: .bold, design: .monospaced))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(.black.opacity(0.75), in: Capsule())
        }
        .accessibilityLabel(Text(verbatim: L("Recording video")))
    }
}

/// The session toolbar. `compact` shows icons only (narrow windows); every button keeps its label
/// for VoiceOver and as a tooltip. Only what the other computer offers is shown.
struct SessionBar: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel
    @ObservedObject var drawing: DrawingModel
    @ObservedObject var workspace: WorkspaceModel
    let tab: WorkspaceModel.SessionTab
    let inWindow: Bool
    let compact: Bool
    @Binding var tool: SessionWorkspaceView.Tool?
    @Binding var inviting: Bool
    @State private var quality = false

    var remote: RemoteStatus { session.remote }

    var body: some View {
        HStack(spacing: 8) {
            identity
            divider
            if remote.control, remote.displays > 1 { monitors }
            if remote.offers("sas") {
                button(L("Ctrl+Alt+Del"), "keyboard") { session.ctrlAltDel() }
            }
            qualityIndicator
            divider
            if remote.files { toggle(.files, L("Files"), "doc.badge.arrow.up") }
            toggle(.chat, L("Chat"), "bubble.left")
            if drawing.available {
                button(L("Draw"), "pencil.tip", on: drawing.on) { drawing.on.toggle() }
            }
            if let recording = remote.recording {
                button(recording.running ? L("Stop") : L("Record"), recording.running ? "stop.circle.fill" : "record.circle", on: recording.running, danger: recording.running) {
                    if recording.running { session.stopRecording() } else { record() }
                }
                .disabled(session.recordingPending || recording.phase == "saving" || session.frame == nil)
            }
            if app.assistant.available { toggle(.assistant, L("Report"), "doc.text") }
            more
            Spacer(minLength: 8)
            button(L("Full screen"), "arrow.up.left.and.arrow.down.right") { NSApp.keyWindow?.toggleFullScreen(nil) }
                .keyboardShortcut("f", modifiers: [.command, .control])
            if inWindow {
                button(L("Back into a tab"), "rectangle.stack") { workspace.dock(tab.peer) }
            } else {
                button(L("Window"), "arrow.up.forward.square") { workspace.popOut(tab.peer) }
            }
            Button(role: .destructive) { session.end() } label: {
                Label(L("End"), systemImage: "xmark").labelStyle(.titleAndIcon).font(.system(size: 13, weight: .semibold))
            }
            .buttonStyle(PrimaryButtonStyle())
            .keyboardShortcut(".", modifiers: .command)
            .disabled(session.stopping)
            .help(L("End session"))
            .accessibilityLabel(Text(verbatim: L("End session")))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Theme.chrome)
    }

    private var divider: some View { Rectangle().fill(Theme.hairline).frame(width: 1, height: 24) }

    private var identity: some View {
        HStack(spacing: 10) {
            OSTile(os: tab.os, dot: nil, size: 32)
            VStack(alignment: .leading, spacing: 1) {
                Text(verbatim: tab.name).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                if !compact {
                    HStack(spacing: 4) {
                        Image(systemName: "lock.fill").foregroundStyle(Theme.success).font(.system(size: 9))
                        Text(verbatim: [Format.remoteId(tab.peer), L("E2E encrypted"), remote.unattendedSession ? L("Unattended") : nil, remote.control ? nil : L("View only")]
                            .compactMap { $0 }.joined(separator: " · "))
                            .lineLimit(1)
                    }
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }
        }
        .accessibilityElement(children: .combine)
        .fixedSize()
    }

    private var monitors: some View {
        HStack(spacing: 2) {
            ForEach(0..<Int(remote.displays), id: \.self) { index in
                let all = remote.displayAll && index == Int(remote.displays) - 1
                let selected = Int(remote.display) == index
                Button { session.selectDisplay(UInt16(index)) } label: {
                    HStack(spacing: 4) {
                        if index == 0 || all { Image(systemName: all ? "rectangle.on.rectangle" : "display").font(.system(size: 11)) }
                        Text(verbatim: all ? L("All") : "\(index + 1)")
                    }
                    .font(.system(size: 12, weight: selected ? .semibold : .regular))
                    .padding(.horizontal, 8).frame(height: 24)
                    .background(selected ? Theme.page : .clear, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(all ? L("All screens") : L("Display %d", index + 1))
                .accessibilityLabel(Text(verbatim: all ? L("All screens") : L("Display %d", index + 1)))
                .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
            }
        }
        .padding(2)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var bars: Int { session.fps >= 20 ? 3 : session.fps >= 8 ? 2 : session.fps > 0 ? 1 : 0 }

    private var qualityIndicator: some View {
        Button { quality.toggle() } label: {
            HStack(spacing: 6) {
                HStack(alignment: .bottom, spacing: 2) {
                    ForEach(1...3, id: \.self) { level in
                        RoundedRectangle(cornerRadius: 1)
                            .fill(level <= bars ? (bars == 3 ? Theme.success : bars == 2 ? Theme.warning : Theme.brand) : Color.secondary.opacity(0.35))
                            .frame(width: 3, height: CGFloat(4 + level * 3))
                    }
                }
                Text(verbatim: L("%d fps", session.fps)).font(.system(size: 12, weight: .semibold)).monospacedDigit()
                if !compact, session.size.width > 0 {
                    Text(verbatim: "\(Int(session.size.width)) × \(Int(session.size.height))").font(.system(size: 12)).foregroundStyle(.secondary).monospacedDigit()
                }
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold)).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8).frame(height: 26)
            .background(Theme.page, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(Theme.hairline))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(L("Picture quality"))
        .accessibilityLabel(Text(verbatim: L("Picture quality")))
        .accessibilityValue(Text(verbatim: L("%d fps", session.fps)))
        .popover(isPresented: $quality, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 8) {
                Text(verbatim: L("Picture quality")).font(.headline)
                LabeledContent { Text(verbatim: L("%d fps", session.fps)).monospacedDigit() } label: { Text(verbatim: L("Pictures per second")) }
                LabeledContent { Text(verbatim: session.size.width > 0 ? "\(Int(session.size.width)) × \(Int(session.size.height))" : "—").monospacedDigit() } label: { Text(verbatim: L("Resolution")) }
                LabeledContent {
                    Text(verbatim: session.decoding == .device ? L("This Mac (hardware)") : session.decoding == .engine ? L("Mixel engine") : "—")
                } label: { Text(verbatim: L("Video decoding")) }
                LabeledContent { Text(verbatim: Format.bytes(remote.received)) } label: { Text(verbatim: L("Received")) }
                Text(verbatim: L("The picture adapts to the line by itself.")).font(.caption).foregroundStyle(.secondary)
            }
            .padding(16)
            .frame(width: 280)
        }
    }

    private var more: some View {
        Menu {
            if remote.offers("privacy") { menuToggle(L("Privacy screen"), session.privacy) { session.togglePrivacy() } }
            if remote.offers("block") { menuToggle(L("Block input"), session.blockInput) { session.toggleBlockInput() } }
            if remote.offers("pointer") { menuToggle(L("Show pointer"), session.pointerMode) { session.togglePointer() } }
            if remote.offers("sound") { menuToggle(L("Sound"), session.sound) { session.toggleSound() } }
            if remote.offers("lock") { Button(L("Lock computer")) { session.lockNow() } }
            if remote.offers("lockend") { menuToggle(L("Lock when done"), session.lockWhenDone) { session.toggleLockWhenDone() } }
            if remote.offers("restart") {
                Button(session.restartArmed ? L("Click again to restart") : L("Restart computer")) { session.restartComputer() }
            }
            Divider()
            if remote.clipboard { Button(L("Send my clipboard")) { session.clipboard(push: true) } }
            if remote.clipboard || remote.control || !remote.printJobs.isEmpty {
                Button(L("Clipboard, send text and print jobs")) { tool = .clipboard }
            }
            if app.assistant.available { Button(L("Explain the screen (AI)")) { tool = .assistant } }
            if remote.phase == "active", !remote.switched {
                Button(L("Invite a colleague")) { inviting = true }
            }
            if session.canSwitchSides {
                Button(L("Switch sides")) { session.switchSides() }
            } else if remote.offers("meet") {
                Button(L("Show my screen")) { session.showMyScreen() }
            }
            Button(L("Renew connection")) { session.renew() }
        } label: {
            Image(systemName: "ellipsis")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .padding(.horizontal, 6)
        .help(L("More"))
        .accessibilityLabel(Text(verbatim: L("More")))
    }

    private func menuToggle(_ title: String, _ on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            if on { Label(title, systemImage: "checkmark") } else { Text(verbatim: title) }
        }
    }

    private func toggle(_ which: SessionWorkspaceView.Tool, _ title: String, _ symbol: String) -> some View {
        button(title, symbol, on: tool == which) { tool = tool == which ? nil : which }
    }

    private func button(_ title: String, _ symbol: String, on: Bool = false, danger: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: symbol).font(.system(size: 13))
                if !compact { Text(verbatim: title).font(.system(size: 13)) }
            }
            .foregroundStyle(danger ? Theme.brand : on ? Theme.brand : Color.primary)
            .padding(.horizontal, compact ? 7 : 9).frame(height: 28)
            .background(on ? Theme.brandSoft : .clear, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(title)
        .accessibilityLabel(Text(verbatim: title))
        .accessibilityAddTraits(on ? [.isSelected, .isButton] : .isButton)
    }

    private func record() {
        Task {
            guard await session.mayRecord() else { session.error = L("Start a permitted recording after the remote screen appears"); return }
            if let path = await app.chooseSaveLocation("Mixel Remote recording.mp4") { session.startRecording(path: path) }
        }
    }
}

/// A popped-out session: its own window, titled "<device> — Mixel Remote". Closing it (red button, Cmd-W) asks
/// "End session with <device>?" and ends the session; "Back into a tab" docks it. The window closes by itself
/// when the session ends.
public struct SessionWindowView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var workspace: WorkspaceModel
    let peer: UInt32
    @State private var window: NSWindow?
    @State private var closeGuard: WindowCloseGuard?

    public init(app: AppModel, peer: UInt32) {
        self.app = app
        workspace = app.workspace
        self.peer = peer
    }

    public var body: some View {
        SessionPane(app: app, peer: peer, inWindow: true)
            .frame(minWidth: 820, minHeight: 520)
            .background(WindowReader { found in
                window = found
                guard let found, closeGuard == nil else { return }
                let guardian = WindowCloseGuard(window: found) {
                    // Closed by the person: no session left means close; else ask, and close only after "End".
                    // (The workspace does not hold the guard, so a strong reference makes no cycle.)
                    guard workspace.tab(peer) != nil else { return true }
                    askToEnd(in: found)
                    return false
                }
                closeGuard = guardian
            })
            .navigationTitle(L("%@ — Mixel Remote", workspace.tab(peer)?.name ?? Format.remoteId(peer)))
            .tint(Theme.brand)
            .onChange(of: workspace.tab(peer) == nil) { ended in if ended { window?.close() } }
            .onChange(of: workspace.isInWindow(peer)) { inWindow in if !inWindow { window?.close() } }
            .onDisappear { workspace.windowFocusChanged(peer, focused: false); workspace.windowClosed(peer) }
            .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { note in
                if let window, (note.object as? NSWindow) === window { workspace.windowFocusChanged(peer, focused: true) }
            }
            .onReceive(NotificationCenter.default.publisher(for: NSWindow.didResignKeyNotification)) { note in
                if let window, (note.object as? NSWindow) === window { workspace.windowFocusChanged(peer, focused: false) }
            }
            .onReceive(NotificationCenter.default.publisher(for: .mixelFocusSessionWindow)) { note in
                if (note.object as? UInt32) == peer { window?.makeKeyAndOrderFront(nil) }
            }
            .onAppear {
                // A window restored at launch without its session closes at once.
                if workspace.tab(peer) == nil || !workspace.isInWindow(peer) {
                    DispatchQueue.main.async { window?.close() }
                }
            }
    }
}

extension SessionWindowView {
    /// The alert: "End session with <device>?" as a sheet on the window; End ends the session and closes it.
    @MainActor private func askToEnd(in window: NSWindow) {
        workspace.confirmEnd = { tab in
            await withCheckedContinuation { continuation in
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = workspace.endQuestion(tab.peer)
                alert.informativeText = L("The connection ends for both sides.")
                alert.addButton(withTitle: L("End session")).hasDestructiveAction = true
                alert.addButton(withTitle: L("Cancel"))
                alert.beginSheetModal(for: window) { continuation.resume(returning: $0 == .alertFirstButtonReturn) }
            }
        }
        Task { @MainActor in
            if await workspace.windowCloseRequested(peer) { window.close() }
        }
    }
}

/// Stands in front of the window's own delegate: the close button asks the view first; everything else
/// is forwarded to the delegate SwiftUI installed.
final class WindowCloseGuard: NSObject, NSWindowDelegate {
    private weak var original: NSWindowDelegate?
    private let shouldClose: () -> Bool

    init(window: NSWindow, shouldClose: @escaping () -> Bool) {
        original = window.delegate
        self.shouldClose = shouldClose
        super.init()
        window.delegate = self
        objc_setAssociatedObject(window, &WindowCloseGuard.key, self, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
    }
    private static var key = 0

    func windowShouldClose(_ sender: NSWindow) -> Bool { shouldClose() }
    override func responds(to selector: Selector!) -> Bool { super.responds(to: selector) || original?.responds(to: selector) == true }
    override func forwardingTarget(for selector: Selector!) -> Any? { original?.responds(to: selector) == true ? original : nil }
}

public extension Notification.Name {
    static let mixelFocusSessionWindow = Notification.Name("mixel.remote.focusSessionWindow")
}

/// Hands the hosting NSWindow to SwiftUI.
struct WindowReader: NSViewRepresentable {
    let found: (NSWindow?) -> Void
    final class Probe: NSView {
        var found: ((NSWindow?) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            let window = self.window
            DispatchQueue.main.async { [weak self] in self?.found?(window) }
        }
    }
    func makeNSView(context: Context) -> NSView {
        let view = Probe()
        view.found = found
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
#endif
