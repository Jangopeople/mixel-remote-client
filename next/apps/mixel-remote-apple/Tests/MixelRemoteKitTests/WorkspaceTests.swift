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

    /// Closing a popped-out window (red button) asks "End session with <device>?": cancel keeps window and session,
    /// confirm ends this session (remote_close with its stable id) and lets the window close.
    func testClosingTheWindowAsksThenEndsOrKeeps() async throws {
        let (app, engine) = try await viewingApp(openIn: .window)
        let ws = app.workspace
        ws.openWindow = { _ in }
        ws.show(731_204_118)
        XCTAssertTrue(ws.isInWindow(731_204_118), "default own window")
        var asked: [String] = []
        ws.confirmEnd = { tab in asked.append(ws.endQuestion(tab.peer)); return false }

        let keep = await ws.windowCloseRequested(731_204_118)
        XCTAssertFalse(keep, "cancel: the window stays")
        XCTAssertEqual(asked, [L("End session with %@?", ws.tab(731_204_118)!.name)])
        XCTAssertTrue(ws.isInWindow(731_204_118))
        XCTAssertNotNil(ws.tab(731_204_118))
        try await Task.sleep(nanoseconds: 150_000_000)
        XCTAssertTrue(engine.calls("remote_close").isEmpty, "cancel ends nothing")

        ws.confirmEnd = { _ in true }
        let close = await ws.windowCloseRequested(731_204_118)
        XCTAssertTrue(close, "confirm: the window may close")
        XCTAssertFalse(ws.isInWindow(731_204_118))
        let deadline = Date().addingTimeInterval(4)
        while engine.calls("remote_close").isEmpty, Date() < deadline { try await Task.sleep(nanoseconds: 20_000_000) }
        XCTAssertEqual(engine.calls("remote_close").map { $0.object["session"] as? String }, [Fixtures.session], "the session ended, not docked")
        XCTAssertNotEqual(ws.selected, .session(731_204_118), "it was not docked into its tab")
    }

    func testTheEndQuestionNamesTheDeviceOrItsRemoteId() async throws {
        let (app, _) = try await viewingApp(openIn: .window)
        let ws = app.workspace
        XCTAssertEqual(ws.endQuestion(731_204_118), L("End session with %@?", ws.tab(731_204_118)!.name))
        XCTAssertEqual(ws.endQuestion(123_456_789), L("End session with %@?", Format.remoteId(123_456_789)), "no tab: the Remote ID")
    }

    func testAWindowWithoutASessionClosesWithoutAsking() async throws {
        let (app, _) = try await viewingApp(openIn: .window)
        let ws = app.workspace
        ws.confirmEnd = { _ in XCTFail("no question for a window without a session"); return false }
        let closes = await ws.windowCloseRequested(123_456_789)
        XCTAssertTrue(closes)
    }

    func testDockStillKeepsTheSessionAndOtherCloseWaysDockIt() async throws {
        let (app, engine) = try await viewingApp(openIn: .window)
        let ws = app.workspace
        ws.openWindow = { _ in }
        ws.closeWindow = { _ in }
        ws.show(731_204_118)
        ws.dock(731_204_118)
        XCTAssertFalse(ws.isInWindow(731_204_118))
        XCTAssertEqual(ws.selected, .session(731_204_118))
        // The window went away without the red button (app quit, system): back into the tab, session kept.
        ws.popOut(731_204_118)
        ws.windowClosed(731_204_118)
        XCTAssertEqual(ws.selected, .session(731_204_118))
        XCTAssertTrue(engine.calls("remote_close").isEmpty)
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
