import XCTest
@testable import MixelRemoteKit

@MainActor
final class WorkspaceTests: XCTestCase {
    override func setUp() {
        Translator.shared.choice = "en"
        resetChoices()
    }

    override func tearDown() { resetChoices() }

    private func resetChoices() {
        UserDefaults.standard.removeObject(forKey: WorkspaceModel.whenReadyKey)
        UserDefaults.standard.removeObject(forKey: WorkspaceModel.openInKey)
    }

    private func waitFor(_ condition: @escaping () -> Bool, timeout: TimeInterval = 4) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() > deadline { XCTFail("timed out"); return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    private func viewingApp(openIn: WorkspaceModel.OpenIn = .tab, whenReady: WorkspaceModel.WhenReady = .stay) async throws -> (AppModel, MockEngine) {
        UserDefaults.standard.set(openIn.rawValue, forKey: WorkspaceModel.openInKey)
        UserDefaults.standard.set(whenReady.rawValue, forKey: WorkspaceModel.whenReadyKey)
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 1))
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.frame != nil }
        app.workspace.refresh()
        return (app, engine)
    }

    func testReadySessionBecomesATabAndStaysInDevicesWithANotice() async throws {
        let (app, _) = try await viewingApp()
        let ws = app.workspace
        XCTAssertEqual(ws.tabs.map(\.peer), [731_204_118])
        XCTAssertEqual(ws.tabs.first?.state, "ready")
        XCTAssertEqual(ws.selected, .console, "Connect must not jump away from Devices")
        XCTAssertEqual(ws.readyNotices, [731_204_118])
    }

    func testSwitchToSessionSettingOpensTheTab() async throws {
        let (app, _) = try await viewingApp(whenReady: .open)
        XCTAssertEqual(app.workspace.selected, .session(731_204_118))
        XCTAssertTrue(app.workspace.readyNotices.isEmpty)
    }

    func testPopOutAndDock() async throws {
        let (app, _) = try await viewingApp(whenReady: .open)
        let ws = app.workspace
        var opened: [UInt32] = [], closed: [UInt32] = []
        ws.openWindow = { opened.append($0) }
        ws.closeWindow = { closed.append($0) }
        ws.popOut(731_204_118)
        XCTAssertEqual(opened, [731_204_118])
        XCTAssertTrue(ws.isInWindow(731_204_118))
        XCTAssertEqual(ws.selected, .console)
        ws.dock(731_204_118)
        XCTAssertEqual(closed, [731_204_118])
        XCTAssertFalse(ws.isInWindow(731_204_118))
        XCTAssertEqual(ws.selected, .session(731_204_118))
    }

    func testClosingTheWindowDocksIt() async throws {
        let (app, _) = try await viewingApp(openIn: .window)
        let ws = app.workspace
        ws.openWindow = { _ in }
        ws.show(731_204_118)
        XCTAssertTrue(ws.isInWindow(731_204_118), "default own window")
        ws.windowClosed(731_204_118)
        XCTAssertFalse(ws.isInWindow(731_204_118))
        XCTAssertEqual(ws.selected, .session(731_204_118))
    }

    func testSessionEndRemovesTabWindowAndNotice() async throws {
        let (app, engine) = try await viewingApp(whenReady: .open)
        let ws = app.workspace
        ws.openWindow = { _ in }
        ws.closeWindow = { _ in }
        ws.popOut(731_204_118)
        engine.reply("remote_status", RemoteStatus(phase: "ready", host: 482_913_557))
        await app.refreshRemote()
        XCTAssertTrue(ws.tabs.isEmpty)
        XCTAssertFalse(ws.isInWindow(731_204_118))
        XCTAssertEqual(ws.selected, .console)
    }

    func testChoicesAreRemembered() {
        let suite = UserDefaults(suiteName: "ws-test-\(UUID())")!
        let engine = MockEngine()
        let a = WorkspaceModel(app: AppModel(engine: engine), defaults: suite)
        XCTAssertEqual(a.whenReady, .stay)
        XCTAssertEqual(a.openIn, .tab)
        a.whenReady = .open
        a.openIn = .window
        let b = WorkspaceModel(app: AppModel(engine: engine), defaults: suite)
        XCTAssertEqual(b.whenReady, .open)
        XCTAssertEqual(b.openIn, .window)
    }
}
