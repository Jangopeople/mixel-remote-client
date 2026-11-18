import XCTest
@testable import MixelRemoteKit

/// Fast first picture: the picture is up before the control channel is open. The line says "Connecting control…" then,
/// not "Connected in view-only mode."; a really view-only session and an open channel keep their own words.
@MainActor
final class ControlPendingTests: XCTestCase {
    override func setUp() { Translator.shared.choice = "en" }

    private func app(control: Bool, pending: Bool?) async -> AppModel {
        let e = MockEngine()
        Fixtures.viewing(e)
        var status = RemoteStatus(phase: "active", host: 482_913_557, session: DemoFixtures.session, consent: DemoFixtures.consent,
                                  peer: 731_204_118, received: 12, control: control)
        status.controlPending = pending
        e.reply("remote_status", status)
        let a = await Fixtures.app(e)
        await a.refreshRemote()
        return a
    }

    func testTheLineFollowsTheControlState() async {
        let viewOnly = await app(control: false, pending: false)
        XCTAssertEqual(viewOnly.connectionMessage, "Connected in view-only mode.")
        let pending = await app(control: false, pending: true)
        XCTAssertEqual(pending.connectionMessage, "Connecting control…")
        let open = await app(control: true, pending: false)
        XCTAssertEqual(open.connectionMessage, "Connected. Keyboard and mouse control is enabled.")
        let old = await app(control: false, pending: nil)
        XCTAssertEqual(old.connectionMessage, "Connected in view-only mode.", "an engine without the field")
    }

    func testTheEngineStatusCarriesThePendingStateIntoTheSessionItem() throws {
        let json = #"{"id":"s-1","peer":731204118,"phase":"active","control":false,"control_pending":true}"#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let item = try decoder.decode(RemoteSessionItem.self, from: Data(json.utf8))
        XCTAssertTrue(item.controlPending)
        let old = try decoder.decode(RemoteSessionItem.self, from: Data(#"{"id":"s-1","peer":1,"phase":"active"}"#.utf8))
        XCTAssertFalse(old.controlPending)
        XCTAssertEqual(RemoteStatus.offline.describing(item).controlPending, true)
    }

    func testTheTextIsTranslated() {
        for language in ["de", "fr", "it"] {
            Translator.shared.choice = language
            XCTAssertNotEqual(L("Connecting control…"), "Connecting control…", language)
        }
        Translator.shared.choice = "en"
    }
}
