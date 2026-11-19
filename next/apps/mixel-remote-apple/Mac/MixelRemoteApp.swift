import AppKit
import AuthenticationServices
import MixelCore
import MixelRemoteKit
import SwiftUI

/// Mixel Remote for Mac 2.0. It replaces the desktop app 0.1.x in place: the same bundle identifier
/// (`ch.mixel.remote.next.qa`), executable name, Keychain items, launch agents and data folder, so an
/// enrolled Mac keeps its Remote ID, always-on access and settings after the update.
@main
enum Entry {
    static func main() {
        // Headless modes first, while the process has one thread (the rollout reads its token then):
        // the always-on host, the rollout enrolment, the sign-in screen host and the install checks.
        if let code = CoreEngine.runCommandLine(CommandLine.arguments) { exit(code) }
        #if DEBUG
        // LAB builds only: `--live-check <qa-session.json> <suffix>` runs the engine checks against the
        // live service without any window, then exits (see LiveCheck.swift).
        let arguments = CommandLine.arguments
        if arguments.count >= 4, arguments[1] == "--live-check" {
            LiveCheck.run(sessionFile: arguments[2], suffix: arguments[3])
        }
        // `--live-session <qa-session.json> <suffix> <folder>`: a real session to a lab computer.
        if arguments.count >= 5, arguments[1] == "--live-session" {
            LiveCheck.run(sessionFile: arguments[2], suffix: arguments[3], sessionFolder: arguments[4])
        }
        // `--live-technician <qa-session.json> <suffix> <folder> [last]`: the live session shared by every
        // app (the engine's `qa_live_technician`): early input and clipboard, pictures, input, clipboard
        // both ways, chat, drawing, end. `last` also deletes the QA company.
        if arguments.count >= 5, arguments[1] == "--live-technician" {
            LiveCheck.technician(sessionFile: arguments[2], suffix: arguments[3], folder: arguments[4], last: arguments.count > 5 && arguments[5] == "last")
        }
        // `--live-be-host <qa-session.json> <suffix> <folder>`: this Mac is the computer being helped (the
        // engine's `qa_live_host`); only where screen recording is already allowed, it never asks.
        if arguments.count >= 5, arguments[1] == "--live-be-host" {
            LiveCheck.technician(sessionFile: arguments[2], suffix: arguments[3], folder: arguments[4], last: false, command: "qa_live_host")
        }
        // `--live-multi <qa-session.json> <suffix> <folder>`: two sessions at once to two lab computers (host-id-a.txt,
        // host-id-b.txt in the folder): pictures of both, input to one, ending one leaves the other.
        if arguments.count >= 5, arguments[1] == "--live-multi" {
            LiveCheck.multi(sessionFile: arguments[2], suffix: arguments[3], folder: arguments[4])
        }
        // `--live-restart <qa-session.json> <suffix> <folder> first|second`: a computer that restarts (killed, then started again).
        if arguments.count >= 6, arguments[1] == "--live-restart" {
            LiveCheck.restart(sessionFile: arguments[2], suffix: arguments[3], folder: arguments[4], phase: arguments[5])
        }
        // `--live-providers`: every sign-in button reaches its provider's login page (no sign-in).
        if arguments.count >= 2, arguments[1] == "--live-providers" { LiveCheck.providers() }
        // `--live-prepare <qa-session.json> <suffix> <folder>`: QA company + deploy token for the update test.
        if arguments.count >= 5, arguments[1] == "--live-prepare" {
            LiveCheck.run(sessionFile: arguments[2], suffix: arguments[3], sessionFolder: arguments[4], prepare: true)
        }
        // `--live-identity <qa-session.json> <suffix> <remote-id>`: after the in-place update from 0.1.22.
        if arguments.count >= 5, arguments[1] == "--live-identity", let id = UInt32(arguments[4]) {
            LiveCheck.run(sessionFile: arguments[2], suffix: arguments[3], expectedId: id)
        }
        #endif
        MixelRemoteMacApp.main()
    }
}

/// The main window: the console, with the session-window hooks of the workspace.
struct MainWindow: View {
    @ObservedObject var app: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        RootView(app: app)
            .ignoresSafeArea(.container, edges: .top)
            .onAppear {
                app.workspace.openWindow = { peer in openWindow(id: "session", value: peer) }
                app.workspace.focusWindow = { peer in NotificationCenter.default.post(name: .mixelFocusSessionWindow, object: peer) }
                app.start()
            }
    }
}

struct MixelRemoteMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        Window("Mixel Remote", id: "main") {
            if let app = delegate.model {
                MainWindow(app: app)
            } else {
                StartupFailure(message: delegate.failure)
            }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1280, height: 800)
        .commands { AppCommands(model: delegate.model) }

        // A session popped out of its tab ("Window"); keyed by the other computer's Remote ID.
        WindowGroup(L("Session"), id: "session", for: UInt32.self) { $peer in
            if let app = delegate.model, let peer {
                SessionWindowView(app: app, peer: peer)
            }
        }
        .defaultSize(width: 1180, height: 760)

        Settings {
            if let app = delegate.model {
                SettingsView(app: app).frame(width: 560, height: 620).tint(Theme.brand)
            }
        }

        MenuBarExtra {
            if let app = delegate.model {
                MenuBarContent(app: app, open: {
                    NSApp?.activate(ignoringOtherApps: true)
                    openWindow(id: "main")
                }, quit: { NSApp?.terminate(nil) })
            }
        } label: {
            if let app = delegate.model { MenuBarIcon(app: app) } else { Image(systemName: "arrow.left.arrow.right.circle") }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, ObservableObject {
    @Published var model: AppModel?
    var failure = ""
    private var quitting = false

    override init() {
        super.init()
        LegacySettings.migrateOnce(bundle: Bundle.main.bundleIdentifier ?? "")
        do {
            let engine = try CoreEngine(dataDirectory: CoreEngine.defaultDirectory())
            let app = AppModel(engine: engine)
            app.devices.admin = EngineDeviceAdminService(engine: engine)
            app.section = .devices
            // A ready transport is kept only while the app is in front and visible (`remote_prewarm`).
            // AppDelegate.init() runs inside MixelRemoteMacApp.init() before NSApplicationMain sets NSApp,
            // so NSApp is nil here and must never be force-unwrapped.
            let center = NotificationCenter.default
            for (name, active) in [(NSApplication.didBecomeActiveNotification, true), (NSApplication.didResignActiveNotification, false),
                                   (NSApplication.didHideNotification, false), (NSApplication.didUnhideNotification, true)] {
                center.addObserver(forName: name, object: nil, queue: .main) { _ in MainActor.assumeIsolated { app.appActive = active && (NSApp?.isActive ?? true) } }
            }
            app.appActive = NSApp?.isActive ?? true
            app.checksForUpdates = true
            app.quit = { [weak self] in
                self?.quitting = true
                NSApp?.terminate(nil)
            }
            app.openURL = { NSWorkspace.shared.open($0) }
            app.openSignIn = { [weak self] url in self?.presentSignIn(url) }
            app.chooseFile = { await Panels.open() }
            app.chooseSaveLocation = { await Panels.save(suggested: $0) }
            model = app
        } catch {
            failure = AppModel.message(error)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        #if DEBUG
        // Lab only: MIXEL_QA_APPEARANCE=dark|light shows the app in that appearance without touching the system setting.
        if let mode = ProcessInfo.processInfo.environment["MIXEL_QA_APPEARANCE"] {
            NSApp?.appearance = NSAppearance(named: mode == "dark" ? .darkAqua : .aqua)
        }
        // Lab only: MIXEL_QA_COMMANDS=<file> reads lines from that file about once a second and deletes it:
        // `connect <id> [attended]`, `end <id>`, `window <id>`, `tab <id>`, `sessions-focus <id>`. A way to drive the app without
        // taking the screen from the person at the Mac.
        if let path = ProcessInfo.processInfo.environment["MIXEL_QA_COMMANDS"] {
            Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.runLabCommands(path) }
            }
        }
        // Lab only: MIXEL_QA_STATUSLOG=<file> appends one line per second: the engine phase and, per session, its stable id, current
        // transport id, phase, reconnecting/failed, pictures received and the quality line (for resume / swap timings).
        if let path = ProcessInfo.processInfo.environment["MIXEL_QA_STATUSLOG"] {
            Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.writeStatusLine(path) }
            }
        }
        #endif
        model?.appActive = NSApp?.isActive ?? true
        model?.start()
    }

    #if DEBUG
    private func writeStatusLine(_ path: String) {
        guard let model else { return }
        let remote = model.remote
        var line = "\(Int(Date().timeIntervalSince1970 * 1000)) phase=\(remote.phase) msg=\(remote.message.prefix(60))"
        for item in remote.sessions ?? [] {
            line += " | \(item.id.prefix(8)) t=\((item.session ?? "-").prefix(8)) \(item.phase) rc=\(item.reconnecting) fail=\(item.reconnectFailed) rx=\(item.received) fps=\(model.sessionModels[item.peer ?? 0]?.fps ?? 0)"
        }
        line += "\n"
        if let data = line.data(using: .utf8) {
            if let handle = FileHandle(forWritingAtPath: path) { handle.seekToEndOfFile(); handle.write(data); try? handle.close() }
            else { FileManager.default.createFile(atPath: path, contents: data) }
        }
    }

    private func runLabCommands(_ path: String) {
        guard let model, let text = try? String(contentsOfFile: path, encoding: .utf8) else { return }
        try? FileManager.default.removeItem(atPath: path)
        for line in text.split(separator: "\n") {
            let words = line.split(separator: " ").map(String.init)
            // `burst <id> <id> ...`: asks the engine for all these sessions at once (the UI starts one at a time); the answers
            // (or the refusal text) go to /tmp/labmac/cmd.log.
            if words.first == "burst" {
                let ids = words.dropFirst().compactMap { UInt32($0) }
                Task { @MainActor in
                    for id in ids {
                        struct Request: Encodable { var host: UInt32; var password: String?; var unattended: Bool }
                        var out = "burst \(id): ok"
                        do { _ = try await model.engine.run("remote_request", Request(host: id, password: nil, unattended: true), as: JSONValue.self) } catch { out = "burst \(id): \(AppModel.message(error))" }
                        let data = (String(Int(Date().timeIntervalSince1970 * 1000)) + " " + out + "\n").data(using: .utf8)!
                        if let handle = FileHandle(forWritingAtPath: "/tmp/labmac/cmd.log") { handle.seekToEndOfFile(); handle.write(data); try? handle.close() } else { FileManager.default.createFile(atPath: "/tmp/labmac/cmd.log", contents: data) }
                    }
                }
                continue
            }
            guard words.count >= 2, let id = UInt32(words[1].replacingOccurrences(of: "-", with: "")) else { continue }
            // `chat <id> <text>` / `chats <id>`: the chat of that session through the engine (windows in the background are never "focused").
            if words[0] == "chat" || words[0] == "chats", let stable = model.sessionModels[id]?.sessionId {
                let text = words.dropFirst(2).joined(separator: " ")
                Task { @MainActor in
                    var out = ""
                    if words[0] == "chat" {
                        do { try await model.engine.perform("remote_chat_send", ["session": stable, "text": text]); out = "chat sent: ok" } catch { out = "chat send failed: \(AppModel.message(error))" }
                    } else {
                        let list: [ChatMessage]? = (try? await model.engine.run("remote_chat_messages", ["session": stable], as: [ChatMessage]?.self)) ?? nil
                        out = "chats: \((list ?? []).map { ($0.mine ? "me: " : "them: ") + $0.text })"
                    }
                    let data = (String(Int(Date().timeIntervalSince1970 * 1000)) + " " + out + "\n").data(using: .utf8)!
                    if let handle = FileHandle(forWritingAtPath: "/tmp/labmac/cmd.log") { handle.seekToEndOfFile(); handle.write(data); try? handle.close() } else { FileManager.default.createFile(atPath: "/tmp/labmac/cmd.log", contents: data) }
                }
                continue
            }
            switch words[0] {
            case "connect": model.workspace.connect(peer: id, name: nil, os: "", unattended: !words.contains("attended"), checkPhone: false)
            case "end": model.workspace.end(id)
            case "window": model.workspace.popOut(id)
            case "tab": model.workspace.dock(id)
            case "show": model.workspace.show(id)
            case "retry": model.sessionModels[id]?.retryReconnect()
            case "select": model.workspace.selected = id == 0 ? .console : .session(id)
            default: break
            }
        }
    }
    #endif

    /// Ending sessions and going offline takes a moment: quit once the engine is done.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, !quitting else { return .terminateNow }
        quitting = true
        Task { @MainActor in
            await model.shutdown()
            NSApp?.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    // MARK: Sign-in (Mixel ID, Google, Microsoft, Apple)

    private var signIn: ASWebAuthenticationSession?
    private var signInWatch: Task<Void, Never>?

    /// The provider's page opens in a secure sign-in sheet (PKCE; the engine keeps the verifier). Its
    /// last step returns to this app's private address on 127.0.0.1, which only the engine answers;
    /// the sheet closes once the engine has the code. "Use another browser" still shows the link.
    func presentSignIn(_ url: URL) {
        signIn?.cancel()
        let session = ASWebAuthenticationSession(url: url, callbackURLScheme: "ch.mixel.remote") { [weak self] _, _ in
            Task { @MainActor in
                guard let self, let model = self.model else { return }
                self.signIn = nil
                await model.refreshAccount()
                if ["starting", "waiting_browser"].contains(model.account.phase) { model.cancelSignIn() }
            }
        }
        session.presentationContextProvider = self
        session.prefersEphemeralWebBrowserSession = false
        signIn = session
        if !session.start() { signIn = nil; NSWorkspace.shared.open(url); return }
        signInWatch?.cancel()
        signInWatch = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 500_000_000)
                guard let self, let session = self.signIn, let model = self.model else { return }
                if !["starting", "waiting_browser"].contains(model.account.phase) {
                    self.signIn = nil
                    session.cancel()
                    return
                }
            }
        }
    }
}

extension AppDelegate: ASWebAuthenticationPresentationContextProviding {
    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated { NSApp?.keyWindow ?? NSApp?.windows.first ?? ASPresentationAnchor() }
    }
}

/// The Open and Save panels for files and recordings.
@MainActor
enum Panels {
    static func open() async -> String? {
        let panel = NSOpenPanel()
        panel.title = L("Choose a file to send")
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        return await panel.begin() == .OK ? panel.url?.path : nil
    }

    static func save(suggested: String) async -> String? {
        let panel = NSSavePanel()
        panel.title = L("Choose where to save the received file")
        panel.nameFieldStringValue = suggested
        panel.canCreateDirectories = true
        return await panel.begin() == .OK ? panel.url?.path : nil
    }
}

/// Menus and shortcuts.
struct AppCommands: Commands {
    let model: AppModel?

    var body: some Commands {
        CommandGroup(replacing: .newItem) {}
        CommandMenu(L("Connection")) {
            Button(L("Make this computer available")) { model?.goOnline() }
                .keyboardShortcut("o", modifiers: [.command, .shift])
            Button(L("Go offline")) { model?.goOffline() }
                .keyboardShortcut("o", modifiers: [.command, .option])
            Button(L("Copy ID")) { model?.host.copyId() }
                .keyboardShortcut("c", modifiers: [.command, .shift])
            Divider()
            Button(L("End session")) { model?.session.end() }
        }
        CommandGroup(after: .sidebar) {
            ForEach(Array(AppSection.allCases.enumerated()), id: \.element) { index, section in
                Button(section.title) { model?.section = section }
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: .command)
            }
            Divider()
        }
        CommandGroup(replacing: .help) {
            Button(L("Mixel Remote Help")) { NSWorkspace.shared.open(URL(string: "https://remote.mixel.ch")!) }
        }
    }
}

/// The menu bar icon: filled while a session runs.
struct MenuBarIcon: View {
    @ObservedObject var app: AppModel
    var body: some View {
        Image(systemName: app.remote.inSession ? "arrow.left.arrow.right.circle.fill" : "arrow.left.arrow.right.circle")
            .accessibilityLabel(Text(verbatim: "Mixel Remote"))
    }
}

struct StartupFailure: View {
    let message: String
    var body: some View {
        VStack(spacing: 12) {
            MixelMark(size: 56)
            Text(verbatim: "Mixel Remote could not start").font(.title2.weight(.semibold))
            Text(verbatim: message).foregroundStyle(.secondary)
        }
        .padding(40)
        .frame(minWidth: 480, minHeight: 300)
    }
}
