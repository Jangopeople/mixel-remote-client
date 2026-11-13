import Foundation

/// Settings: stay signed in, start with this computer, this computer (remove it), remote printing,
/// permissions, language, updates, About and legal.
@MainActor
public final class SettingsModel: ObservableObject {
    // The app model lives as long as the app; its parts keep it (no reference cycle matters there).
    let app: AppModel
    @Published public private(set) var startup: StartupSettings?
    @Published public private(set) var printing: PrintingStatus?
    @Published public private(set) var update: UpdateInfo?
    @Published public private(set) var updateChecked = false
    @Published public private(set) var removalPending = false
    @Published public private(set) var screenAccess = false
    @Published public var language: String = Translator.shared.choice
    @Published public var error = ""
    @Published public var notice = ""

    init(app: AppModel) { self.app = app }

    public func load() async {
        startup = try? await app.engine.run("startup_settings")
        removalPending = (try? await app.engine.run("device_removal_pending", as: Bool.self)) ?? false
        if !app.isPhone {
            printing = try? await app.engine.run("remote_printing_status")
            screenAccess = (try? await app.engine.run("screen_access", as: Bool.self)) ?? false
        }
    }

    /// Sends a removal of this computer that was saved while offline.
    func flushPendingRemoval() async {
        if let status = try? await app.engine.run("device_self_remove_flush", as: SelfRemovalStatus?.self) ?? nil, status != .queued {
            app.enrollment.forget()
        }
        removalPending = (try? await app.engine.run("device_removal_pending", as: Bool.self)) ?? false
    }

    public func setStaySignedIn(_ enabled: Bool) {
        change { try await self.app.engine.perform("set_stay_signed_in", ["enabled": enabled]) }
    }

    public func setAutostart(_ enabled: Bool) {
        change {
            if enabled { try await self.app.engine.perform("set_stay_signed_in", ["enabled": true]) }
            try await self.app.engine.perform("set_autostart", ["enabled": enabled])
        }
    }

    public func setPrinting(_ enabled: Bool) {
        change { try await self.app.engine.perform("remote_printing_set", ["enabled": enabled]) }
    }

    public func setLanguage(_ value: String) {
        language = value
        Translator.shared.choice = value
        // Screens read texts when drawn: redraw everything.
        app.objectWillChange.send()
    }

    public func checkForUpdate() {
        change {
            self.update = try await self.app.engine.run("update_check", ["variant": self.app.isPhone ? "testflight" : "native-preview"])
            self.updateChecked = true
        }
    }

    public func openLegal(_ page: String) {
        change {
            let url: String = try await self.app.engine.run("legal_url", ["page": page, "lang": Translator.shared.language])
            if let link = URL(string: url) { self.app.openURL(link) }
        }
    }

    public func openStorePage() {
        if let url = URL(string: "https://store.mixel.ch/app/mixel-remote") { app.openURL(url) }
    }

    public func requestScreenAccess() {
        change {
            self.screenAccess = try await self.app.engine.run("request_screen_access", as: Bool.self)
        }
    }

    public func requestInputAccess() {
        change { _ = try await self.app.engine.run("request_input_access", as: Bool.self) }
    }

    private func change(_ work: @escaping () async throws -> Void) {
        error = ""
        Task {
            do { try await work() } catch { self.error = AppModel.message(error) }
            await self.load()
        }
    }
}
