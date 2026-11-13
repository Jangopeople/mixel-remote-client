import SwiftUI
import XCTest
@testable import MixelRemoteKit

/// Pictures of every screen in light and dark, drawn offscreen (never shown on the screen).
/// Set SNAPSHOT_DIR to keep them; without it they are drawn and checked only.
@MainActor
final class ScreenshotTests: XCTestCase {
    var folder: URL? {
        ProcessInfo.processInfo.environment["SNAPSHOT_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? (try? String(contentsOfFile: "/tmp/mixel-snapshot-dir", encoding: .utf8)).map { URL(fileURLWithPath: $0.trimmingCharacters(in: .whitespacesAndNewlines), isDirectory: true) }
    }

    override func setUp() {
        Translator.shared.choice = "en"
    }

    func screens() async -> [(String, CGSize, AnyView)] {
        #if os(macOS)
        let wide = CGSize(width: 1080, height: 720)
        #else
        let wide = CGSize(width: 393, height: 852)
        #endif
        var list: [(String, CGSize, AnyView)] = []

        let signedOut = MockEngine(); Fixtures.signedOut(signedOut)
        let outApp = await Fixtures.app(signedOut)
        let mfaEngine = MockEngine(); Fixtures.mfa(mfaEngine)
        let mfaApp = await Fixtures.app(mfaEngine)
        let companyEngine = MockEngine(); Fixtures.company(companyEngine)
        let app = await Fixtures.app(companyEngine)
        let setupEngine = MockEngine(); Fixtures.company(setupEngine, phase: "offline")
        setupEngine.fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.")
        let setupApp = await Fixtures.app(setupEngine)
        let incomingEngine = MockEngine(); Fixtures.incoming(incomingEngine)
        let incomingApp = await Fixtures.app(incomingEngine)
        await incomingApp.host.loadSources()
        let viewEngine = MockEngine(); Fixtures.viewing(viewEngine)
        let viewApp = await Fixtures.app(viewEngine)
        viewApp.session.showSample(VideoFrame.sample(width: 1280, height: 800))
        await viewApp.session.readTools()
        let hostedEngine = MockEngine(); Fixtures.hostedSession(hostedEngine)
        let hostedApp = await Fixtures.app(hostedEngine)

        #if os(macOS)
        list.append(("mac-remote-signed-out", wide, AnyView(RootView(app: outApp))))
        outApp.section = .account
        list.append(("mac-account-signed-out", wide, AnyView(AccountView(app: outApp))))
        list.append(("mac-account-second-step", wide, AnyView(AccountView(app: mfaApp))))
        list.append(("mac-remote-control", wide, AnyView(RootView(app: app))))
        list.append(("mac-devices", wide, AnyView(DevicesView(app: app))))
        list.append(("mac-account-company", CGSize(width: 1080, height: 1100), AnyView(AccountView(app: app))))
        list.append(("mac-settings", CGSize(width: 760, height: 1100), AnyView(SettingsView(app: app))))
        list.append(("mac-setup-this-computer", wide, AnyView(RemoteControlView(app: setupApp))))
        list.append(("mac-consent-sheet", CGSize(width: 480, height: 560), AnyView(ConsentSheet(app: incomingApp))))
        list.append(("mac-session-workspace", wide, AnyView(SessionWorkspaceView(app: viewApp))))
        list.append(("mac-session-chat", wide, AnyView(SessionToolsPreview(app: viewApp, tool: .chat))))
        list.append(("mac-session-files", wide, AnyView(SessionToolsPreview(app: viewApp, tool: .files))))
        list.append(("mac-session-clipboard", wide, AnyView(SessionToolsPreview(app: viewApp, tool: .clipboard))))
        list.append(("mac-hosted-session", wide, AnyView(RemoteControlView(app: hostedApp))))
        list.append(("mac-invite-colleague", CGSize(width: 480, height: 420), AnyView(InviteColleagueSheet(app: viewApp, session: viewApp.session))))
        list.append(("mac-invite-person", CGSize(width: 440, height: 360), AnyView(InviteSheet(company: app.company))))
        list.append(("mac-deploy-link", CGSize(width: 560, height: 420), AnyView(DeployLinkSheet(model: app.devices))))
        list.append(("mac-menu-bar", CGSize(width: 300, height: 260), AnyView(VStack(alignment: .leading, spacing: 8) { MenuBarContent(app: app, open: {}, quit: {}) }.padding().buttonStyle(.borderless))))
        #else
        outApp.section = .account
        list.append(("ios-account-signed-out", wide, AnyView(PhoneRootView(app: outApp))))
        mfaApp.section = .account
        list.append(("ios-account-second-step", wide, AnyView(PhoneRootView(app: mfaApp))))
        app.section = .devices
        list.append(("ios-devices", wide, AnyView(PhoneRootView(app: app))))
        let connectEngine = MockEngine(); Fixtures.company(connectEngine)
        let connectApp = await Fixtures.app(connectEngine)
        connectApp.section = .remote
        list.append(("ios-connect", wide, AnyView(PhoneRootView(app: connectApp))))
        let accountEngine = MockEngine(); Fixtures.company(accountEngine)
        let accountApp = await Fixtures.app(accountEngine)
        accountApp.section = .account
        list.append(("ios-account-company", wide, AnyView(PhoneRootView(app: accountApp))))
        list.append(("ios-settings", wide, AnyView(NavigationStack { SettingsView(app: app) })))
        setupApp.section = .remote
        list.append(("ios-setup-this-phone", wide, AnyView(PhoneRootView(app: setupApp))))
        list.append(("ios-session", wide, AnyView(PhoneSessionView(app: viewApp))))
        list.append(("ios-session-landscape", CGSize(width: 852, height: 393), AnyView(PhoneSessionView(app: viewApp))))
        list.append(("ios-chat", wide, AnyView(PhoneChatSheet(session: viewApp.session))))
        list.append(("ios-phone-help", wide, AnyView(PhoneHelpSheet())))
        list.append(("ipad-devices", CGSize(width: 820, height: 1180), AnyView(PhoneRootView(app: app))))
        #endif
        return list
    }

    func testEveryScreenInLightAndDark() async throws {
        FileHandle.standardError.write(Data("building screens\n".utf8))
        let screens = await screens()
        XCTAssertGreaterThan(screens.count, 10)
        for (name, size, view) in screens {
            for dark in [false, true] {
                FileHandle.standardError.write(Data("render \(name) dark=\(dark)\n".utf8))
                let png = try Snapshot.render(view, size: size, dark: dark)
                XCTAssertGreaterThan(png.count, 5_000, name)
                if let folder {
                    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                    try png.write(to: folder.appendingPathComponent("\(name)-\(dark ? "dark" : "light").png"))
                }
            }
        }
    }

    func testScreensInGermanFrenchItalian() async throws {
        let engine = MockEngine(); Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        for language in ["de", "fr", "it"] {
            Translator.shared.choice = language
            #if os(macOS)
            let view = AnyView(RootView(app: app)); let size = CGSize(width: 1080, height: 720)
            #else
            let view = AnyView(PhoneRootView(app: app)); let size = CGSize(width: 393, height: 852)
            #endif
            let png = try Snapshot.render(view, size: size, dark: false)
            if let folder { try png.write(to: folder.appendingPathComponent("\(language)-main.png")) }
        }
        Translator.shared.choice = "en"
    }
}

/// The session with one tool panel open.
struct SessionToolsPreview: View {
    @ObservedObject var app: AppModel
    let tool: SessionWorkspaceView.Tool
    var body: some View {
        HStack(spacing: 0) {
            Color.black.overlay { if let frame = app.session.frame { Image(decorative: frame, scale: 1).resizable().aspectRatio(contentMode: .fit) } }
            Divider()
            ToolPanel(app: app, session: app.session, tool: tool).frame(width: 300)
        }
    }
}

@MainActor
enum Snapshot {
    struct Failed: Error {}

    static func render(_ view: AnyView, size: CGSize, dark: Bool) throws -> Data {
        #if os(macOS)
        // An app object for AppKit drawing, without a Dock icon or windows on the screen.
        if NSApp == nil || NSApp.activationPolicy() != .prohibited { NSApplication.shared.setActivationPolicy(.prohibited) }
        let root = view.frame(width: size.width, height: size.height).environment(\.colorScheme, dark ? .dark : .light).tint(Theme.brand)
        let host = NSHostingView(rootView: root)
        host.frame = CGRect(origin: .zero, size: size)
        let appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = appearance
        host.appearance = appearance
        window.contentView = host
        // Never ordered front: drawn offscreen only.
        for _ in 0..<4 { host.layoutSubtreeIfNeeded(); RunLoop.current.run(until: Date().addingTimeInterval(0.08)) }
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { throw Failed() }
        NSAppearance.current = appearance
        host.cacheDisplay(in: host.bounds, to: rep)
        window.close()
        guard let png = rep.representation(using: .png, properties: [:]) else { throw Failed() }
        return png
        #else
        let root = view.frame(width: size.width, height: size.height).tint(Theme.brand)
        let controller = UIHostingController(rootView: root)
        controller.overrideUserInterfaceStyle = dark ? .dark : .light
        // A window on the test host's scene draws like on a device (a window without a scene draws nothing).
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        let window = scene.map { UIWindow(windowScene: $0) } ?? UIWindow(frame: CGRect(origin: .zero, size: size))
        window.frame = CGRect(origin: .zero, size: size)
        FileHandle.standardError.write(Data("scene: \(scene != nil)\n".utf8))
        window.overrideUserInterfaceStyle = dark ? .dark : .light
        window.rootViewController = controller
        window.isHidden = false
        controller.view.frame = window.bounds
        for _ in 0..<4 { controller.view.setNeedsLayout(); controller.view.layoutIfNeeded(); RunLoop.current.run(until: Date().addingTimeInterval(0.1)) }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 2
        let image = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
        }
        window.isHidden = true
        guard let png = image.pngData() else { throw Failed() }
        return png
        #endif
    }
}
