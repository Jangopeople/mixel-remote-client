import AppKit
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
        // `--live-identity <qa-session.json> <suffix> <remote-id>`: after the in-place update from 0.1.22.
        if arguments.count >= 5, arguments[1] == "--live-identity", let id = UInt32(arguments[4]) {
            LiveCheck.run(sessionFile: arguments[2], suffix: arguments[3], expectedId: id)
        }
        #endif
        MixelRemoteMacApp.main()
    }
}

struct MixelRemoteMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        Window("Mixel Remote", id: "main") {
            if let app = delegate.model {
                RootView(app: app)
                    .tint(Theme.brand)
                    .onAppear { app.start() }
            } else {
                StartupFailure(message: delegate.failure)
            }
        }
        .defaultSize(width: 1080, height: 720)
        .commands { AppCommands(model: delegate.model) }

        Settings {
            if let app = delegate.model {
                SettingsView(app: app).frame(width: 560, height: 620).tint(Theme.brand)
            }
        }

        MenuBarExtra {
            if let app = delegate.model {
                MenuBarContent(app: app, open: {
                    NSApp.activate(ignoringOtherApps: true)
                    openWindow(id: "main")
                }, quit: { NSApp.terminate(nil) })
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
            app.checksForUpdates = true
            app.quit = { [weak self] in
                self?.quitting = true
                NSApp.terminate(nil)
            }
            app.openURL = { NSWorkspace.shared.open($0) }
            app.chooseFile = { await Panels.open() }
            app.chooseSaveLocation = { await Panels.save(suggested: $0) }
            model = app
        } catch {
            failure = AppModel.message(error)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        model?.start()
    }

    /// Ending sessions and going offline takes a moment: quit once the engine is done.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, !quitting else { return .terminateNow }
        quitting = true
        Task { @MainActor in
            await model.shutdown()
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
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
                .keyboardShortcut(".", modifiers: .command)
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
