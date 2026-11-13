import AuthenticationServices
import MixelCore
import MixelRemoteKit
import SwiftUI
import UIKit

/// Mixel Remote for iPhone and iPad: a native controller on the shared engine.
@main
struct MixelRemotePhoneApp: App {
    @StateObject private var shell = PhoneShell()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            Group {
                if let app = shell.model {
                    PhoneRootView(app: app)
                        .onAppear { app.start() }
                        .onChange(of: app.account.phase) { phase in shell.signInPhase(phase) }
                } else {
                    VStack(spacing: 12) {
                        MixelMark(size: 56)
                        Text(verbatim: "Mixel Remote could not start").font(.title2.weight(.semibold))
                        Text(verbatim: shell.failure).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    .padding()
                }
            }
            .tint(Theme.brand)
        }
        .onChange(of: phase) { phase in
            // Back in front: read the engine at once (the session or sign-in may have moved on).
            if phase == .active { Task { await shell.model?.refreshAccount(); await shell.model?.refreshRemote() } }
        }
    }
}

@MainActor
final class PhoneShell: NSObject, ObservableObject, ASWebAuthenticationPresentationContextProviding {
    @Published var model: AppModel?
    var failure = ""
    private var signIn: ASWebAuthenticationSession?

    override init() {
        super.init()
        do {
            let engine = try CoreEngine(dataDirectory: CoreEngine.defaultDirectory())
            let app = AppModel(engine: engine)
            app.section = .devices
            app.openURL = { UIApplication.shared.open($0) }
            app.openSignIn = { [weak self] url in self?.present(url) }
            model = app
        } catch {
            failure = AppModel.message(error)
        }
    }

    /// Mixel ID opens inside the app; its last step returns to this app's private address on
    /// 127.0.0.1, which only the engine can answer. The sheet closes once the engine has the code.
    private func present(_ url: URL) {
        signIn?.cancel()
        let session = ASWebAuthenticationSession(url: url, callbackURLScheme: "ch.mixel.remote.controller") { [weak self] _, _ in
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
        session.start()
    }

    func signInPhase(_ phase: String) {
        if !["starting", "waiting_browser"].contains(phase), let session = signIn {
            signIn = nil
            session.cancel()
        }
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.keyWindow }.first ?? ASPresentationAnchor()
        }
    }
}
