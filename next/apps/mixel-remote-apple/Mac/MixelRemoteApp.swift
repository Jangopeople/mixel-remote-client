import AppKit
import MixelCore
import MixelRemoteKit
import SwiftUI

/// Mixel Remote for Mac (native preview, `ch.mixel.remote.native`): installed next to the current
/// app, with its own device identity and sign-in.
@main
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
        do {
            let engine = try CoreEngine(dataDirectory: CoreEngine.defaultDirectory())
            let app = AppModel(engine: engine)
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
