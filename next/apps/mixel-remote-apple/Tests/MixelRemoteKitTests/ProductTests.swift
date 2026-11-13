import XCTest
@testable import MixelRemoteKit

/// Mixel Remote 2.0: what the native apps took over from the desktop app 0.1.22 — H.264 decoded on
/// the device, in-app updates on the signed channel, sign-in screen access (waiting for Apple),
/// Wake-on-LAN addresses.
@MainActor
final class ProductTests: XCTestCase {
    override func setUp() {
        Translator.shared.choice = "en"
        UserDefaults.standard.removeObject(forKey: "mixel.remote.autoupdate.v1")
        UserDefaults.standard.removeObject(forKey: DevicesModel.macsKey)
    }

    private func waitFor(_ condition: @escaping () -> Bool, timeout: TimeInterval = 5) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() > deadline { XCTFail("timed out"); return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    // MARK: H.264 on the device

    func testTheSessionPictureIsDecodedOnTheDevice() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        let packets = try VideoDecoderTests.encode(colors: [(200, 30, 40), (30, 180, 60)])
        packets.forEach(engine.queue(frame:))
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.decoding == .device && app.session.frame != nil }
        XCTAssertEqual(app.session.size, CGSize(width: 320, height: 240))
        XCTAssertEqual(engine.videoRequestsCompressed.first, true, "the app asks the engine for H.264")
    }

    func testAStreamThisDeviceCannotDecodeFallsBackToTheEngine() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        // A complete picture header with garbage parameter sets: VideoToolbox refuses it.
        var broken = Data("MRH1".utf8)
        broken.append(contentsOf: [1, 0, 0, 0] + [UInt8](repeating: 0, count: 8))
        withUnsafeBytes(of: UInt64(1).littleEndian) { broken.append(contentsOf: $0) }
        withUnsafeBytes(of: UInt64(0).littleEndian) { broken.append(contentsOf: $0) }
        broken.append(contentsOf: [0, 0, 0, 1, 0x67, 0xFF, 0xFF, 0, 0, 0, 1, 0x68, 0xFF, 0, 0, 0, 1, 0x65, 0x88, 0x80])
        engine.queue(frame: broken)
        engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 2))
        engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 3))
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.decoding == .engine && app.session.frame != nil }
        XCTAssertEqual(engine.videoRequestsCompressed.last, false, "after the failure the engine decodes")
        XCTAssertEqual(app.session.size, CGSize(width: 320, height: 200))
        XCTAssertTrue(app.session.error.isEmpty)
    }

    // MARK: Updates

    func testASilentUpdateInstallsByItselfAndQuits() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("update_check", json: #"{"version":"2.0.1","installable":true,"silent":true,"needs_password":false}"#)
        let app = await Fixtures.app(engine)
        var quit = false
        app.quit = { quit = true }
        await app.settings.periodicUpdateCheck()
        try await waitFor { quit }
        XCTAssertEqual(engine.calls("update_install").count, 1)
        // Checked at most every 30 minutes.
        await app.settings.periodicUpdateCheck()
        XCTAssertEqual(engine.calls("update_check").count, 1)
    }

    func testNoAutomaticInstallWhenTurnedOffOrNotSilent() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("update_check", json: #"{"version":"2.0.1","installable":true,"silent":false,"needs_password":false}"#)
        let app = await Fixtures.app(engine)
        await app.settings.periodicUpdateCheck()
        XCTAssertEqual(app.settings.update?.version, "2.0.1")
        XCTAssertTrue(engine.calls("update_install").isEmpty, "a running session is never interrupted")

        let second = MockEngine()
        Fixtures.company(second)
        second.reply("update_check", json: #"{"version":"2.0.1","installable":true,"silent":true,"needs_password":false}"#)
        let other = await Fixtures.app(second)
        other.settings.autoUpdate = false
        XCTAssertEqual(UserDefaults.standard.string(forKey: "mixel.remote.autoupdate.v1"), "off", "same setting name as the desktop app")
        await other.settings.periodicUpdateCheck()
        XCTAssertTrue(second.calls("update_install").isEmpty)
        // The person can still choose Update now.
        var quit = false
        other.quit = { quit = true }
        other.settings.installUpdate()
        try await waitFor { quit }
        XCTAssertEqual(second.calls("update_install").count, 1)
    }

    func testAFailedInstallIsShownAndNotRetriedAutomatically() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("update_check", json: #"{"version":"2.0.1","installable":true,"silent":true,"needs_password":false}"#)
        engine.fail("update_install", "The update is not signed by Mixel")
        let app = await Fixtures.app(engine)
        await app.settings.periodicUpdateCheck()
        try await waitFor { !app.settings.error.isEmpty }
        XCTAssertTrue(app.settings.error.contains("not signed by Mixel"))
        XCTAssertFalse(app.settings.installing)
    }

    // MARK: Sign-in screen

    func testSignInScreenWaitsForApple() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("signin_screen_status", json: #"{"supported":false,"availability":"waiting_for_apple","connection_id":null,"name":null}"#)
        let app = await Fixtures.app(engine)
        XCTAssertEqual(app.settings.signInScreen?.availability, .waitingForApple)
        XCTAssertFalse(app.settings.signInScreen?.supported ?? true)
    }

    func testSignInScreenEnableUsesTheCompanyAndName() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("signin_screen_status", json: #"{"supported":true,"availability":"ready","connection_id":null,"name":null}"#)
        engine.reply("signin_screen_enable", json: "512334901")
        let app = await Fixtures.app(engine)
        app.settings.enableSignInScreen(company: "c-1", name: "Reception Mac")
        try await waitFor { !app.settings.signInWorking && !app.settings.signInMessage.isEmpty }
        let call = try XCTUnwrap(engine.calls("signin_screen_enable").last).object
        XCTAssertEqual(call["company"] as? String, "c-1")
        XCTAssertEqual(call["name"] as? String, "Reception Mac")
        XCTAssertTrue(app.settings.signInMessage.contains("512 334 901"))
    }

    // MARK: Wake-on-LAN

    func testNetworkAddressesFromASessionEnableWake() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        var status = RemoteStatus(phase: "active", host: 482_913_557, session: Fixtures.session, consent: Fixtures.consent, peer: 731_204_118, control: true)
        status.hostMacs = ["aa:bb:cc:dd:ee:01", "not-a-mac"]
        engine.reply("remote_status", status)
        let app = await Fixtures.app(engine)
        let device = try XCTUnwrap(app.devices.devices.first { $0.connectionId == 731_204_118 })
        XCTAssertEqual(app.devices.knownMacs(device), ["aa:bb:cc:dd:ee:01"])
        app.devices.wake(device)
        try await waitFor { !engine.calls("wake_on_lan").isEmpty }
        XCTAssertEqual(engine.calls("wake_on_lan").last?.object["macs"] as? [String], ["aa:bb:cc:dd:ee:01"])
    }

    // MARK: Mixel team tools

    private func staff(_ engine: MockEngine) {
        engine.reply("account_status", AccountStatus(phase: "authenticated", message: "Signed in",
                                                     account: Account(remembered: true, profile: DemoFixtures.profile, staff: true, displayName: "Team")))
    }

    func testRolloutCreatesListsAndRevokes() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        staff(engine)
        engine.reply("rollout_create", json: #"{"token":"mrt_x","windows_command":"w","macos_command":"m","linux_command":"l"}"#)
        engine.reply("rollout_list", json: #"[{"id":"r1","label":"Office","created_at":"2026-11-13T08:00:00Z","expires_at":"2026-12-13T08:00:00Z","max_uses":50,"uses":2,"revoked_at":null}]"#)
        let app = await Fixtures.app(engine)
        app.enrollment.createRollout(company: "c-1", label: "Office", days: 30, computers: 50)
        try await waitFor { app.enrollment.rolloutCreated != nil && !app.enrollment.working }
        XCTAssertEqual(app.enrollment.rolloutCreated?.macosCommand, "m")
        XCTAssertEqual(app.enrollment.rollouts.first?.uses, 2)
        let create = try XCTUnwrap(engine.calls("rollout_create").last).object
        XCTAssertEqual(create["computers"] as? Int, 50)
        XCTAssertEqual(create["days"] as? Int, 30)
        app.enrollment.revokeRollout(app.enrollment.rollouts[0], company: "c-1")
        try await waitFor { !engine.calls("rollout_revoke").isEmpty && !app.enrollment.working }
        XCTAssertEqual(engine.calls("rollout_revoke").last?.object["id"] as? String, "r1")
    }

    func testAssistantSendsTheScreenAndSavesTheTicketNote() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        staff(engine)
        engine.reply("ai_connect", json: #"{"address":"http://127.0.0.1:11434","models":["qwen","llava"],"model":"llava"}"#)
        engine.reply("ai_ask", json: #""**Error** shown: printer offline""#)
        engine.reply("ticket_note", json: #""T-2026-0042 · Printer""#)
        engine.queue(frame: VideoFrame.sample(width: 640, height: 400, sequence: 1))
        let app = await Fixtures.app(engine)
        XCTAssertTrue(app.assistant.available)
        try await waitFor { app.session.frame != nil }
        app.assistant.connect()
        try await waitFor { app.assistant.connected }
        XCTAssertEqual(app.assistant.model, "llava")
        app.assistant.run(.ask)
        try await waitFor { !app.assistant.busy && app.assistant.answer.contains("printer") }
        XCTAssertEqual(app.assistant.answer, "Error shown: printer offline")
        let ask = try XCTUnwrap(engine.calls("ai_ask").last).object
        XCTAssertNotNil(ask["image"] as? String, "the remote screen goes along as JPEG")
        XCTAssertTrue((ask["system"] as? String ?? "").contains("English"))
        app.assistant.ticket = "T-2026-0042"
        app.assistant.saveTicket()
        try await waitFor { app.assistant.ticketStatus.contains("T-2026-0042") }
        XCTAssertEqual(engine.calls("ticket_note").last?.object["remote_id"] as? Int, 731_204_118)
    }
}

@MainActor
final class SignInProviderTests: XCTestCase {
    func testProviderButtonsFollowTheServiceAndStartWithTheChosenProvider() async throws {
        Translator.shared.choice = "en"
        let engine = MockEngine()
        Fixtures.signedOut(engine)
        engine.reply("account_providers", json: #"["keycloak","google","azure","apple"]"#)
        engine.reply("account_sign_in", json: #""https://ism-api.mixel.ch/auth/v1/authorize?provider=azure""#)
        let app = await Fixtures.app(engine)
        XCTAssertEqual(app.otherSignInProviders, ["google", "azure", "apple"])
        XCTAssertEqual(AppModel.providerTitle("azure"), "Continue with Microsoft")
        var opened: URL?
        app.openSignIn = { opened = $0 }
        app.signIn(provider: "azure")
        let deadline = Date().addingTimeInterval(3)
        while opened == nil, Date() < deadline { try await Task.sleep(nanoseconds: 20_000_000) }
        XCTAssertEqual(engine.calls("account_sign_in").last?.object["provider"] as? String, "azure")
        XCTAssertEqual(opened?.host, "ism-api.mixel.ch")

        let off = MockEngine()
        Fixtures.signedOut(off)
        off.reply("account_providers", json: #"["keycloak"]"#)
        let plain = await Fixtures.app(off)
        XCTAssertTrue(plain.otherSignInProviders.isEmpty, "no button for a provider the service does not offer")
    }
}
