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
    @Published public private(set) var installing = false
    @Published public var autoUpdate: Bool = UserDefaults.standard.string(forKey: SettingsModel.autoUpdateKey) != "off" {
        didSet { UserDefaults.standard.set(autoUpdate ? "on" : "off", forKey: Self.autoUpdateKey) }
    }
    @Published public private(set) var signInScreen: SignInScreenStatus?
    @Published public private(set) var signInWorking = false
    @Published public var signInMessage = ""
    @Published public var language: String = Translator.shared.choice
    @Published public var error = ""
    @Published public var notice = ""

    /// The desktop app's setting ("on"/"off"), kept under the same name.
    static let autoUpdateKey = "mixel.remote.autoupdate.v1"
    private var lastUpdateCheck = Date.distantPast
    private var installFailed = false

    init(app: AppModel) { self.app = app }

    public func load() async {
        if !app.isPhone { signInScreen = try? await app.engine.run("signin_screen_status") }
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
        change { try await self.check(automatic: false) }
    }

    /// At start and every 30 minutes (Mac): a newer signed version installs by itself when nothing
    /// would be interrupted and automatic updates are on, as in the desktop app.
    func periodicUpdateCheck() async {
        guard !app.isPhone, !installing, Date().timeIntervalSince(lastUpdateCheck) >= 30 * 60 else { return }
        try? await check(automatic: true)
    }

    private func check(automatic: Bool) async throws {
        lastUpdateCheck = Date()
        update = try await app.engine.run("update_check", as: UpdateInfo?.self)
        updateChecked = true
        if automatic, let update, update.installable, update.silent, autoUpdate, !installFailed { await install() }
    }

    /// Installs the newest signed version in place; the app quits and the new version starts.
    public func installUpdate() {
        guard !installing else { return }
        Task { await install() }
    }

    private func install() async {
        guard let update, update.installable, !installing else { return }
        installing = true
        error = ""
        notice = L("Installing Mixel Remote %@… The app restarts by itself.", update.version)
        do {
            try await app.engine.perform("update_install")
            notice = L("Restarting Mixel Remote…")
            await app.quitForUpdate()
        } catch {
            installing = false
            installFailed = true
            notice = ""
            self.error = L("The update could not be installed: %@", AppModel.message(error))
        }
    }

    // MARK: Sign-in screen (Mac)

    /// Mixel team: access at the sign-in screen of this Mac (two administrator prompts).
    public func enableSignInScreen(company: String, name: String) {
        signIn { let id: UInt32 = try await self.app.engine.run("signin_screen_enable", ["company": company, "name": name])
            self.signInMessage = L("The sign-in screen of this Mac can be reached with Remote ID %@.", Format.remoteId(id)) }
    }

    public func updateSignInScreen() {
        signIn { try await self.app.engine.perform("signin_screen_update"); self.signInMessage = L("Sign-in screen access is up to date.") }
    }

    public func disableSignInScreen() {
        signIn { try await self.app.engine.perform("signin_screen_disable"); self.signInMessage = L("Sign-in screen access is off.") }
    }

    private func signIn(_ work: @escaping () async throws -> Void) {
        guard !signInWorking else { return }
        signInWorking = true
        signInMessage = ""
        error = ""
        Task {
            do { try await work() } catch { self.error = AppModel.message(error) }
            self.signInWorking = false
            self.signInScreen = try? await self.app.engine.run("signin_screen_status")
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
