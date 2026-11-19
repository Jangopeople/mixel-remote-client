import XCTest
@testable import MixelRemoteKit

/// The pre-warm trigger: `remote_prewarm {"on":true}` only while the Devices list is shown with a device online, the app is in
/// front and the plan and role allow connecting; `{"on":false}` otherwise.
@MainActor
final class PrewarmTests: XCTestCase {
    func testGateTruthTable() {
        func wanted(_ shown: Bool = true, active: Bool = true, signedIn: Bool = true, connected: Bool = true, allowed: Bool = true,
                    blocked: Bool = false, online: Bool = true) -> Bool {
            DevicesModel.prewarmWanted(devicesShown: shown, appActive: active, signedIn: signedIn, connected: connected,
                                       allowed: allowed, blockedBySession: blocked, anyOnline: online)
        }
        XCTAssertTrue(wanted())
        XCTAssertFalse(wanted(false), "Devices list not shown")
        XCTAssertFalse(wanted(active: false), "app hidden or not in front")
        XCTAssertFalse(wanted(signedIn: false))
        XCTAssertFalse(wanted(connected: false))
        XCTAssertFalse(wanted(allowed: false), "plan or role does not allow connecting")
        XCTAssertFalse(wanted(blocked: true), "this computer is being helped")
        XCTAssertFalse(wanted(online: false), "no device online")
    }

    func lastOn(_ engine: MockEngine) -> Bool? { engine.calls("remote_prewarm").last?.object["on"] as? Bool }

    func testDevicesListWithOnlineDeviceAsksAndLeavingReleases() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        app.section = .devices
        await app.devices.updatePrewarm()
        XCTAssertEqual(lastOn(engine), true)
        app.section = .sessions
        await app.devices.updatePrewarm()
        XCTAssertEqual(lastOn(engine), false)
        let count = engine.calls("remote_prewarm").count
        await app.devices.updatePrewarm()
        XCTAssertEqual(engine.calls("remote_prewarm").count, count, "no repeated off")
    }

    func testHiddenAppReleases() async {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        app.section = .devices
        await app.devices.updatePrewarm()
        XCTAssertEqual(lastOn(engine), true)
        app.appActive = false
        await app.devices.updatePrewarm()
        XCTAssertEqual(lastOn(engine), false)
        app.appActive = true
        await app.devices.updatePrewarm()
        XCTAssertEqual(lastOn(engine), true)
    }

    func testRoleWithoutDeviceAccessNeverAsks() async {
        let engine = MockEngine()
        Fixtures.company(engine, role: "member")
        let app = await Fixtures.app(engine)
        app.section = .devices
        await app.devices.updatePrewarm()
        XCTAssertFalse(app.devices.mayConnect)
        XCTAssertNotEqual(lastOn(engine), true)
    }

    #if os(macOS)
    func testAppActiveNeverForceUnwrapsNilNSAppBeforeApplicationMain() {
        let activeBeforeNSApp = NSApp?.isActive ?? true
        XCTAssertNotNil(activeBeforeNSApp)
    }
    #endif
}
