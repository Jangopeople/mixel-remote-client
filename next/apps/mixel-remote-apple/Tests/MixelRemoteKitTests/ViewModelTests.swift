import XCTest
@testable import MixelRemoteKit

@MainActor
final class ViewModelTests: XCTestCase {
    override func setUp() {
        Translator.shared.choice = "en"
    }

    // MARK: Connecting

    func testConnectBringsThisComputerOnlineThenRequests() async throws {
        let engine = MockEngine()
        Fixtures.company(engine, phase: "offline")
        let app = await Fixtures.app(engine)
        // After remote_register the engine reports ready.
        engine.handle("remote_register") { _ in
            engine.reply("remote_status", RemoteStatus(phase: "ready", host: 482_913_557))
            return .success("null")
        }
        try await app.connectSteps(id: 731_204_118, password: "123456", unattended: false)
        let commands = engine.calls.map(\.command).filter { ["remote_register", "quick_phone_claim", "remote_request"].contains($0) }
        XCTAssertEqual(Array(commands.suffix(3)), ["remote_register", "quick_phone_claim", "remote_request"])
        let request = try XCTUnwrap(engine.calls("remote_request").last).object
        XCTAssertEqual(request["host"] as? Int, 731_204_118)
        XCTAssertEqual(request["password"] as? String, "123456")
        XCTAssertEqual(request["unattended"] as? Bool, false)
    }

    func testFirstConnectWithoutIdentityOffersAddThisComputer() async throws {
        let engine = MockEngine()
        Fixtures.company(engine, phase: "offline")
        let app = await Fixtures.app(engine)
        engine.fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.")
        do { try await app.connectSteps(id: 731_204_118, password: nil, unattended: true); XCTFail("must fail") } catch {
            let text = AppModel.message(error)
            XCTAssertTrue(text.contains("Add this computer"), text)
            XCTAssertFalse(text.contains("Create device identity"), text)
        }
        XCTAssertTrue(app.setupOffer, "the screen offers the Add this computer button")
        XCTAssertTrue(engine.calls("remote_request").isEmpty)
    }

    func testAStaleIdentityShowsTheRealReasonNotTheNetwork() async throws {
        let engine = MockEngine()
        Fixtures.company(engine, phase: "offline")
        let app = await Fixtures.app(engine)
        // The engine accepts the command and then reports why it cannot go online.
        engine.handle("remote_register") { _ in
            engine.reply("remote_status", RemoteStatus(phase: "offline", message: "This computer is not registered for the current account", host: 482_913_557))
            return .success("null")
        }
        do { try await app.connectSteps(id: 731_204_118, password: nil, unattended: true); XCTFail("must fail") } catch {
            let text = AppModel.message(error)
            XCTAssertFalse(text.contains("internet connection"), text)
            XCTAssertTrue(text.contains("Add this computer"), text)
        }
        XCTAssertTrue(app.setupOffer)
    }

    func testConnectWaitsWhileThisComputerIsStillRegistering() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        // The first status read says "connecting", every later one "ready".
        var reads = 0
        engine.handle("remote_status") { _ in
            reads += 1
            let phase = reads <= 1 ? "connecting" : "ready"
            return .success(#"{"phase":"\#(phase)","message":"","host":482913557,"online":[],"sessions":[]}"#)
        }
        try await app.connectSteps(id: 731_204_118, password: nil, unattended: true, checkPhone: false)
        XCTAssertEqual(engine.calls("remote_request").count, 1, "the request went out once this computer was ready")
        XCTAssertGreaterThanOrEqual(reads, 2)
    }

    func testPhoneIsClaimedAndWaitedForBeforeTheRequest() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("quick_phone_claim", json: "true")
        // The phone is online at once.
        engine.reply("remote_status", RemoteStatus(phase: "ready", host: 482_913_557, online: [648_493_658]))
        let app = await Fixtures.app(engine)
        try await app.connectSteps(id: 648_493_658, password: nil, unattended: true)
        let request = try XCTUnwrap(engine.calls("remote_request").last).object
        XCTAssertEqual(request["unattended"] as? Bool, false, "phones are always attended")
        XCTAssertTrue(app.progress.contains("Waiting for the other person"))
    }

    func testDeviceDirectorySendsUnattendedRequestWhenChosen() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        app.section = .devices
        await app.devices.load()
        let device = try XCTUnwrap(app.devices.devices.first { $0.connectionId != app.remote.host })
        app.devices.connect(device, unattended: true)
        XCTAssertEqual(app.section, .devices, "connecting from Devices stays in Devices until the session opens")
        try await waitFor { !engine.calls("remote_request").isEmpty }
        XCTAssertTrue(engine.calls("quick_phone_claim").isEmpty, "devices in the directory are not phones")
        let request = try XCTUnwrap(engine.calls("remote_request").last).object
        XCTAssertEqual(request["host"] as? Int, Int(device.connectionId))
        XCTAssertEqual(request["unattended"] as? Bool, true)
        XCTAssertEqual(app.progress, "Requesting unattended access…")
    }

    func testStaffOnlyPhoneClaimFailureExplainsTheSecondStep() async {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.fail("quick_phone_claim", "permission denied: 42501 staff")
        let app = await Fixtures.app(engine)
        do {
            try await app.connectSteps(id: 648_493_658, password: nil, unattended: false)
            XCTFail("must fail")
        } catch {
            XCTAssertTrue(AppModel.message(error).contains("second confirmation step"))
        }
        XCTAssertTrue(engine.calls("remote_request").isEmpty)
    }

    func testRemoteIdParsing() {
        XCTAssertEqual(Format.parseRemoteId("731 204 118"), 731_204_118)
        XCTAssertNil(Format.parseRemoteId("031204118"))
        XCTAssertNil(Format.parseRemoteId("73120411"))
        XCTAssertNil(Format.parseRemoteId("73120411x"))
        XCTAssertEqual(Format.remoteId(731_204_118), "731 204 118")
        XCTAssertEqual(Format.remoteId(nil), "— — —")
    }

    func testGoesOnlineBySelfButNotAfterGoOffline() async {
        let engine = MockEngine()
        Fixtures.company(engine, phase: "offline")
        let app = await Fixtures.app(engine)
        XCTAssertEqual(engine.calls("remote_register").count, 1, "a signed-in, set-up computer goes online by itself")
        app.wentOffline = true
        await app.goOnlineIfNeeded()
        XCTAssertEqual(engine.calls("remote_register").count, 1)
    }

    func testMissingIdentityAsksForSetupInsteadOfAnError() async {
        let engine = MockEngine()
        Fixtures.company(engine, phase: "offline")
        engine.fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.")
        let app = await Fixtures.app(engine)
        XCTAssertTrue(app.enrollment.needsSetup)
        XCTAssertEqual(app.error, "")
        XCTAssertEqual(app.enrollment.path, .company)
    }

    // MARK: Devices

    func testDeviceStatusAndReasons() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        let devices = app.devices
        XCTAssertEqual(devices.devices.count, 4)
        let reception = try XCTUnwrap(devices.devices.first { $0.name == "Reception PC" })
        let warehouse = try XCTUnwrap(devices.devices.first { $0.name == "Warehouse laptop" })
        let me = try XCTUnwrap(devices.devices.first { $0.name == "This Mac" })
        XCTAssertEqual(devices.presence(of: reception), .online)
        XCTAssertEqual(devices.presence(of: warehouse), .offline, "checked once while connected and not online")
        XCTAssertTrue(devices.reason(for: warehouse).contains("Last seen 3 hours ago"))
        XCTAssertEqual(devices.presence(of: me), .this)
        XCTAssertEqual(devices.connectBlocked(me), "This is the computer you are using.")
        XCTAssertEqual(devices.connectBlocked(warehouse), "This device is offline.")
        XCTAssertEqual(devices.connectBlocked(reception), "")
        XCTAssertTrue(devices.manages)
        XCTAssertTrue(devices.groupsOn)
        XCTAssertEqual(devices.groups.count, 1)
        let presence = try XCTUnwrap(engine.calls("remote_presence").last).object
        XCTAssertEqual((presence["hosts"] as? [Int])?.contains(482_913_557), false, "this computer is not asked about")
    }

    func testDevicesWithoutConnectionUseTheServiceRecord() async throws {
        let engine = MockEngine()
        Fixtures.company(engine, phase: "error")
        engine.fail("remote_register", "Network unreachable")
        let app = await Fixtures.app(engine)
        let warehouse = try XCTUnwrap(app.devices.devices.first { $0.name == "Warehouse laptop" })
        let reception = try XCTUnwrap(app.devices.devices.first { $0.name == "Reception PC" })
        XCTAssertEqual(app.devices.presence(of: warehouse), .offline)
        XCTAssertEqual(app.devices.presence(of: reception), .unknown)
        XCTAssertEqual(app.devices.reason(for: reception), "Shown once this computer is connected.")
    }

    func testManageRenameMovesAndRemovesWithTheTenant() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("saas_rename_device", json: "\"Front desk\"")
        let app = await Fixtures.app(engine)
        let device = try XCTUnwrap(app.devices.devices.first)
        let renamed = await app.devices.rename(device, to: "Front desk")
        XCTAssertTrue(renamed)
        let args = try XCTUnwrap(engine.calls("saas_rename_device").last).object
        XCTAssertEqual(args["tenant"] as? String, Fixtures.tenant)
        XCTAssertEqual(args["device"] as? String, device.device)
        XCTAssertEqual(args["name"] as? String, "Front desk")
        _ = await app.devices.move(device, to: nil)
        XCTAssertTrue(engine.calls("saas_move_device").last!.args.contains("\"group\":null"))
        _ = await app.devices.remove(device)
        XCTAssertEqual(engine.calls("saas_remove_device").count, 1)
    }

    func testReadOnlyCompanyBlocksAdding() async {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("saas_tenant_overview", json: #"{"role":"owner","status":"read_only","plan":"business","features":{"groups":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}"#)
        let app = await Fixtures.app(engine)
        XCTAssertEqual(app.devices.addBlocked, "This company is read-only")
        XCTAssertTrue(app.company.readOnly)
    }

    // MARK: Consent (this computer is helped)

    func testConsentSendsExactlyWhatWasAllowed() async throws {
        let engine = MockEngine()
        Fixtures.incoming(engine)
        let app = await Fixtures.app(engine)
        let host = app.host
        await host.loadSources()
        XCTAssertTrue(host.asking)
        XCTAssertEqual(host.source, "display:1")
        XCTAssertTrue(host.canAccept)
        XCTAssertTrue(host.allowControl)
        host.allowFiles = true
        host.allowChat = true
        host.decide(accept: true)
        try await waitFor { !engine.calls("remote_decide").isEmpty }
        let decision = try XCTUnwrap(engine.calls("remote_decide").last?.object["decision"] as? [String: Any])
        XCTAssertEqual(decision["accept"] as? Bool, true)
        XCTAssertEqual(decision["confirm"] as? Bool, true)
        XCTAssertEqual(decision["source"] as? String, "display:1")
        XCTAssertEqual(decision["control"] as? Bool, true)
        XCTAssertEqual(decision["files"] as? Bool, true)
        XCTAssertEqual(decision["chat"] as? Bool, true)
        XCTAssertEqual(decision["clipboard"] as? Bool, true)
        XCTAssertEqual(decision["recording"] as? Bool, false)
    }

    func testOneClickControlWithMoreThanOneDisplay() async throws {
        let engine = MockEngine()
        Fixtures.incoming(engine)
        engine.reply("capture_targets", json: #"[{"id":"display:1","name":"Main display","full_display":true},{"id":"display:2","name":"Second display","full_display":true}]"#)
        let app = await Fixtures.app(engine)
        let host = app.host
        await host.loadSources()
        XCTAssertTrue(host.canAccept)
        host.source = "display:2"
        host.decide(accept: true)
        try await waitFor { !engine.calls("remote_decide").isEmpty }
        let decision = try XCTUnwrap(engine.calls("remote_decide").last?.object["decision"] as? [String: Any])
        XCTAssertEqual(decision["source"] as? String, "display:2")
        XCTAssertEqual(decision["control"] as? Bool, true)
        XCTAssertEqual(decision["confirm"] as? Bool, true)
        XCTAssertEqual(decision["clipboard"] as? Bool, true)
        for option in ["files", "chat", "recording"] {
            XCTAssertEqual(decision[option] as? Bool, false, option)
        }
    }

    func testDeclineAllowsNothing() async throws {
        let engine = MockEngine()
        Fixtures.incoming(engine)
        let app = await Fixtures.app(engine)
        await app.host.loadSources()
        app.host.allowFiles = true
        app.host.decide(accept: false)
        try await waitFor { !engine.calls("remote_decide").isEmpty }
        let decision = try XCTUnwrap(engine.calls("remote_decide").last?.object["decision"] as? [String: Any])
        XCTAssertEqual(decision["accept"] as? Bool, false)
        XCTAssertEqual(decision["files"] as? Bool, false)
        XCTAssertEqual(decision["control"] as? Bool, false)
    }

    // MARK: Session (technician side)

    func testSessionShowsPictureAndSendsInput() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 1))
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.frame != nil }
        XCTAssertEqual(app.session.size, CGSize(width: 320, height: 200))
        XCTAssertTrue(app.session.controllable)
        app.session.move(to: CGPoint(x: 0.5, y: 0.25))
        app.session.send(.buttonClick(button: 1, down: true, clicks: 1))
        app.session.send(.buttonClick(button: 1, down: false, clicks: 1))
        app.session.press(6, modifiers: [KeyMap.control])
        try await waitFor { engine.sentInput.count >= 7 }
        let sent = Self.events(engine)
        XCTAssertEqual(sent.first, ["kind": "pointer", "x": 32768, "y": 16384])
        XCTAssertTrue(sent.contains(["kind": "key", "usage": 224, "down": true]))
        XCTAssertTrue(sent.contains(["kind": "key", "usage": 6, "down": false]))
    }

    func testToolbarActionsUseTheDesktopNames() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample())
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.frame != nil }
        app.session.togglePrivacy()
        app.session.toggleBlockInput()
        app.session.toggleLockWhenDone()
        app.session.lockNow()
        app.session.ctrlAltDel()
        app.session.toggleSound()
        app.session.selectDisplay(1)
        try await waitFor { engine.sentInput.count >= 7 }
        let sent = Self.events(engine)
        for name in ["privacy:on", "block:on", "lockend:on", "lock", "sas", "sound:off"] {
            XCTAssertTrue(sent.contains(["kind": "action", "name": name]), name)
        }
        XCTAssertTrue(sent.contains(["kind": "select_display", "index": 1]))
        XCTAssertTrue(app.session.canSwitchSides)
    }

    func testEndSessionClosesThatSessionByItsIdAndStopsThePicture() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample())
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.frame != nil }
        app.session.end()
        XCTAssertNil(app.session.frame)
        try await waitFor { !engine.calls("remote_close").isEmpty }
        XCTAssertEqual(engine.calls("remote_close").last?.object["session"] as? String, Fixtures.session)
    }

    func testPointerMovesCoalesce() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample())
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.frame != nil }
        for step in 0..<50 { app.session.move(to: CGPoint(x: Double(step) / 50, y: 0.5)) }
        try await Task.sleep(nanoseconds: 300_000_000)
        XCTAssertLessThan(engine.sentInput.count, 50, "unsent moves are replaced by the newest")
        XCTAssertEqual(SessionModel.normalized(1.5), 65535)
        XCTAssertEqual(SessionModel.normalized(-1), 0)
    }

    func testChatAndFilesAreRead() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        let app = await Fixtures.app(engine)
        await app.session.readTools()
        XCTAssertEqual(app.session.chat.count, 2)
        XCTAssertEqual(app.session.files?.outgoing?.name, "driver-setup.exe")
        XCTAssertEqual(app.session.files?.outgoing?.progress ?? 0, 31.0 / 48.0, accuracy: 0.001)
        app.session.chatDraft = "  Done  "
        app.session.sendChat()
        try await waitFor { !engine.calls("remote_chat_send").isEmpty }
        XCTAssertEqual(engine.calls("remote_chat_send").last?.object["text"] as? String, "Done")
    }

    // MARK: Company

    func testCompanyPlanUsageAndInvite() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("saas_invite_member", json: "\"44444444-4444-4444-8444-444444444444\"")
        let app = await Fixtures.app(engine)
        XCTAssertEqual(app.company.tenant?.name, "Muster Treuhand AG")
        XCTAssertTrue(app.company.planLine.hasPrefix("Business trial until"))
        XCTAssertEqual(app.company.usageLine, "4 of 2,000 devices · 2 of 15 technicians")
        XCTAssertEqual(app.company.members.count, 2)
        XCTAssertEqual(app.company.invites.count, 1)
        let refused = await app.company.invite(email: "not-an-address", role: "technician")
        XCTAssertFalse(refused)
        let sent = await app.company.invite(email: " lea@muster.test ", role: "admin")
        XCTAssertTrue(sent)
        let args = try XCTUnwrap(engine.calls("saas_invite_member").last).object
        XCTAssertEqual(args["email"] as? String, "lea@muster.test")
        XCTAssertEqual(args["role"] as? String, "admin")
        XCTAssertEqual(args["locale"] as? String, "en")
    }

    func testCreatingACompanyNeedsTheTerms() async {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        let refused = await app.company.create(name: "New AG", acceptedTerms: false)
        XCTAssertFalse(refused)
        XCTAssertTrue(engine.calls("saas_signup").isEmpty)
    }

    // MARK: Sign-in

    func testSecondStepValidatesTheCode() async throws {
        let engine = MockEngine()
        Fixtures.mfa(engine)
        let app = await Fixtures.app(engine)
        XCTAssertEqual(app.account.phase, "mfa_required")
        app.verificationCode = "12 34"
        app.verify(factor: "f")
        XCTAssertEqual(app.error, "Enter the 6-digit code")
        app.verificationCode = "123 456"
        app.verify(factor: "f0000000-0000-4000-8000-000000000001")
        try await waitFor { !engine.calls("account_verify").isEmpty }
        XCTAssertEqual(engine.calls("account_verify").last?.object["code"] as? String, "123456")
    }

    // MARK: Pictures, translations

    func testVideoFrameChecksHeaderAndSequence() throws {
        let frame = try VideoFrame.decode(VideoFrame.sample(width: 64, height: 32, sequence: 5))
        XCTAssertEqual(frame.width, 64)
        XCTAssertEqual(frame.sequence, 5)
        XCTAssertThrowsError(try VideoFrame.decode(VideoFrame.sample(width: 64, height: 32, sequence: 5), after: 5))
        var broken = VideoFrame.sample(width: 64, height: 32)
        broken[0] = 0
        XCTAssertThrowsError(try VideoFrame.decode(broken))
        XCTAssertThrowsError(try VideoFrame.decode(Data("MRH1".utf8) + Data(count: 40))) { error in
            XCTAssertEqual(error as? VideoFrame.Problem, .compressed)
        }
    }

    func testEngineAndScreenTextsAreTranslated() {
        Translator.shared.choice = "de"
        defer { Translator.shared.choice = "en" }
        XCTAssertEqual(Translator.shared.translate("Accept connection"), "Verbindung annehmen")
        XCTAssertEqual(L("Devices"), "Geräte")
        XCTAssertEqual(Translator.shared.translate("A name nobody translated"), "A name nobody translated")
        XCTAssertEqual(EngineFailure("Accept connection").errorDescription, "Verbindung annehmen")
        Translator.shared.choice = "fr"
        XCTAssertNotEqual(L("Remote control"), "Remote control")
        Translator.shared.choice = "it"
        XCTAssertNotEqual(L("Devices"), "Devices")
    }

    func testInputEventsEncodeLikeTheDesktopApp() throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        func json(_ event: InputEvent) throws -> String { String(decoding: try encoder.encode(event), as: UTF8.self) }
        XCTAssertEqual(try json(.releaseAll), #"{"kind":"release_all"}"#)
        XCTAssertEqual(try json(.wheel(horizontal: 0, vertical: -3)), #"{"horizontal":0,"kind":"wheel","vertical":-3}"#)
        XCTAssertEqual(try json(.buttonClick(button: 3, down: true, clicks: 1)), #"{"button":3,"clicks":1,"down":true,"kind":"button_click"}"#)
        XCTAssertEqual(KeyMap.usage(macKeyCode: 0x00), 4)
        XCTAssertEqual(KeyMap.usage(macKeyCode: 0x24), 40)
        XCTAssertEqual(KeyMap.usage(macKeyCode: 0x37), 227)
    }

    #if os(macOS)
    func testModifierFlagsNeverInvertCommandAfterCmdTabRelease() throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample())
        XCTAssertEqual(RemoteScreenView.modifierIsDown(usage: 227, flags: [.command]), true)
        XCTAssertEqual(RemoteScreenView.modifierIsDown(usage: 227, flags: []), false)
        XCTAssertEqual(RemoteScreenView.modifierIsDown(usage: 224, flags: [.control]), true)
        XCTAssertEqual(RemoteScreenView.modifierIsDown(usage: 225, flags: [.shift]), true)
        XCTAssertEqual(RemoteScreenView.modifierIsDown(usage: 226, flags: [.option]), true)
    }

    func testPasteToRemotePushesClipboardBeforeSendingKeystrokeAndCopyPullsAfter() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample())
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.frame != nil }

        // Paste (⌘V) pushes the local clipboard first, then sends the V keystroke.
        app.session.pasteToRemote(usage: 25, modifiers: [KeyMap.command], forcePush: true)
        try await waitFor { !engine.calls("remote_clipboard").isEmpty && engine.sentInput.count >= 4 }
        let pushCall = try XCTUnwrap(engine.calls("remote_clipboard").first).object
        XCTAssertEqual(pushCall["push"] as? Bool, true)
        let sent = Self.events(engine)
        XCTAssertTrue(sent.contains(["kind": "key", "usage": 227, "down": true]))
        XCTAssertTrue(sent.contains(["kind": "key", "usage": 25, "down": true]))
        XCTAssertTrue(sent.contains(["kind": "key", "usage": 25, "down": false]))
        XCTAssertTrue(sent.contains(["kind": "key", "usage": 227, "down": false]))

        // Copy (⌘C) sends the C keystroke and automatically pulls the remote clipboard shortly after.
        app.session.copyFromRemote(usage: 6, modifiers: [KeyMap.command])
        try await waitFor { engine.calls("remote_clipboard").contains { ($0.object["push"] as? Bool) == false } }
        let pullCall = try XCTUnwrap(engine.calls("remote_clipboard").last).object
        XCTAssertEqual(pullCall["push"] as? Bool, false)
    }

    func testNonSaasAdminCanRemoveAndRenameSavedDevice() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("saas_my_tenants", json: "[]")
        engine.reply("remote_devices", json: #"{"devices":[{"device":"d1000000-0000-4000-8000-000000000099","connection_id":615951910,"name":"Michael z16","os":"windows"}]}"#)
        engine.reply("remote_admin_devices", json: #"[{"device_id":"d1000000-0000-4000-8000-000000000099","connection_id":615951910,"name":"Michael z16","os":"windows","can_admin":true,"favorite":false}]"#)
        engine.reply("remote_admin_remove_device", json: #"{"removed":true}"#)
        let app = await Fixtures.app(engine)
        app.devices.admin = EngineDeviceAdminService(engine: engine)
        await app.devices.load()
        let device = try XCTUnwrap(app.devices.devices.first { $0.connectionId == 615951910 })
        XCTAssertNil(app.devices.tenant)
        XCTAssertTrue(app.devices.canAdmin(device))
        XCTAssertTrue(app.devices.writable)
        let removed = await app.devices.remove(device)
        XCTAssertTrue(removed)
        let call = try XCTUnwrap(engine.calls("remote_admin_remove_device").last).object
        XCTAssertEqual(call["device"] as? String, "d1000000-0000-4000-8000-000000000099")
        XCTAssertEqual(call["confirm_connection_id"] as? Int, 615951910)
    }
    #endif

    /// The input events the engine received, as dictionaries (key order does not matter).
    static func events(_ engine: MockEngine) -> [[String: AnyHashable]] {
        engine.sentInput.compactMap { (try? JSONSerialization.jsonObject(with: Data($0.utf8))) as? [String: AnyHashable] }
    }

    private func waitFor(_ condition: @escaping () -> Bool, timeout: TimeInterval = 3) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() > deadline { XCTFail("timed out"); return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }
}
