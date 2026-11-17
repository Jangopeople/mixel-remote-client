import XCTest
@testable import MixelRemoteKit

/// Several technician sessions at once (engine 2.1, docs/plans/multi-session-engine-api.md): one tab per
/// session, input and toolbar actions only to the focused session, closing one session ends only that one.
@MainActor
final class MultiSessionTests: XCTestCase {
    override func setUp() { Translator.shared.choice = "en" }

    private let tooMany = #"{"hint":"too_many_sessions","max":4,"message":"You already have 4 sessions open."}"#

    private func peer(_ n: Int) -> UInt32 { UInt32(700_000_000 + n) }

    private func item(_ n: Int, phase: String = "active", transport: String? = nil) -> RemoteSessionItem {
        RemoteSessionItem(id: "id-\(n)", session: transport ?? "transport-\(n)", peer: peer(n), phase: phase, consent: "consent-\(n)",
                          control: true, clipboard: true, files: true, recordingPermitted: true, displays: 2,
                          hostActions: ["sas", "lock", "privacy"], peerFeatures: ["join", "draw", "switch"])
    }

    /// What a 2.1 engine answers: `sessions`, and the single-session fields describing the oldest.
    private func status(_ items: [RemoteSessionItem]?, max: Int = 4) -> RemoteStatus {
        var status = RemoteStatus(phase: items?.isEmpty == false ? "active" : "ready", host: 482_913_557, sessions: items, maxSessions: items == nil ? nil : max)
        if let first = items?.first {
            status.session = first.session
            status.peer = first.peer
            status.consent = first.consent
            status.control = true
        }
        return status
    }

    private func waitFor(_ condition: @escaping () -> Bool, timeout: TimeInterval = 4) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() > deadline { XCTFail("timed out"); return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    private func settle() async throws { try await Task.sleep(nanoseconds: 200_000_000) }

    /// An app with `count` ready sessions (a picture each), `count` tabs, nothing selected yet.
    private func app(sessions count: Int, engine: MockEngine = MockEngine()) async throws -> (AppModel, MockEngine) {
        Fixtures.company(engine)
        let items = (1...count).map { item($0) }
        engine.reply("remote_status", status(items))
        for n in 1...count { engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 1), session: "id-\(n)") }
        let app = await Fixtures.app(engine)
        try await waitFor { app.sessionModels.values.filter { $0.frame != nil }.count == count }
        app.workspace.refresh()
        return (app, engine)
    }

    private func show(_ items: [RemoteSessionItem], _ app: AppModel, _ engine: MockEngine) async {
        engine.reply("remote_status", status(items))
        await app.refreshRemote()
    }

    // MARK: Tabs

    func testTwoToFourSessionsProduceTwoToFourTabs() async throws {
        for count in 2...4 {
            let (app, _) = try await app(sessions: count)
            let ws = app.workspace
            XCTAssertEqual(ws.tabs.count, count, "\(count) sessions")
            XCTAssertEqual(ws.tabs.map(\.sessionId), (1...count).map { "id-\($0)" }, "each tab is bound to its session's stable id")
            XCTAssertEqual(ws.tabs.map(\.peer), (1...count).map { peer($0) })
            XCTAssertTrue(ws.tabs.allSatisfy { $0.state == "ready" })
            XCTAssertEqual(app.sessionModels.count, count)
            app.stop()
        }
    }

    func testStatusDecodesSessionsAndMaxSessions() throws {
        let json = """
        {"controller_only":false,"platform":"macos","phase":"active","message":"","host":482913557,"session":"t1","consent":"c1","peer":700000001,
         "received":3,"sharing":false,"control":true,"clipboard":true,"files":false,"recording_permitted":false,"recording":null,"previous_recording":null,
         "input_access":true,"unattended_enabled":false,"unattended_pending":null,"unattended_session":false,"pairing":null,"pairing_message":null,
         "code_required":false,"support_code":null,"online":[],"displays":1,"display":0,"display_all":false,"print_jobs":[],"host_actions":[],"signin_id":null,
         "host_macs":[],"meeting_offer":null,"colleagues":[],"join_request":null,"invitations":[],"invite_message":null,"joined_session":false,"switched":false,
         "peer_features":[],"max_sessions":4,
         "sessions":[{"id":"s-1","session":null,"peer":700000001,"device":700000001,"phase":"outgoing","message":"Waiting","consent":null,"received":0,
           "control":false,"clipboard":false,"files":false,"recording_permitted":false,"recording":null,"displays":0,"display":0,"display_all":false,
           "print_jobs":[],"host_actions":[],"signin_id":null,"host_macs":[],"meeting_offer":["x","room"],"peer_features":["draw"],"joined_session":false,
           "switched":false,"quality":{"pictures":9,"pictures_per_second":24.5,"picture_age_ms":40},"started_at":1789000000}]}
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(RemoteStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.maxSessions, 4)
        XCTAssertEqual(status.sessionLimit, 4)
        let item = try XCTUnwrap(status.sessions?.first)
        XCTAssertEqual(item.id, "s-1")
        XCTAssertNil(item.session)
        XCTAssertEqual(item.peer, 700_000_001)
        XCTAssertEqual(item.phase, "outgoing")
        XCTAssertEqual(item.meetingOffer, ["x", "room"])
        XCTAssertEqual(item.quality?.pictures, 9)
        XCTAssertEqual(item.quality?.picturesPerSecond, 24.5)
        XCTAssertEqual(item.startedAt, 1_789_000_000)
        XCTAssertTrue(status.requesting)
        // An item with only some fields still decodes (tolerant).
        let sparse = try decoder.decode(RemoteSessionItem.self, from: Data(#"{"id":"only-id","phase":"active","peer":700000002}"#.utf8))
        XCTAssertEqual(sparse.id, "only-id")
        XCTAssertEqual(sparse.peerFeatures, [])
        XCTAssertFalse(sparse.control)
    }

    // MARK: Focus

    func testFocusSwitchingRoutesInputClipboardAndToolbarToTheFocusedSessionOnly() async throws {
        let (app, engine) = try await app(sessions: 3)
        let ws = app.workspace
        ws.selected = .session(peer(2))
        XCTAssertEqual(ws.focusedPeer, peer(2))
        for n in 1...3 { XCTAssertEqual(ws.isFocused(peer(n)), n == 2) }

        // Every session's model is driven the way its screen would (all three), only the focused one acts.
        for n in 1...3 {
            let model = try XCTUnwrap(ws.session(for: peer(n)))
            model.send(.key(usage: 4, down: true))
            model.ctrlAltDel()
            model.selectDisplay(1)
            model.clipboard(push: true)
            model.chatDraft = "hello \(n)"
            model.sendChat()
            model.sendFile(path: "/tmp/f\(n).txt")
            model.startRecording(path: "/tmp/r\(n).mp4")
        }
        try await waitFor { engine.sentInputSessions.count >= 3 && engine.calls("remote_chat_send").count >= 1 && engine.calls("remote_record_start").count >= 1 }
        try await settle()
        XCTAssertEqual(Set(engine.sentInputSessions.map(\.session)), ["id-2"], "keyboard, Ctrl+Alt+Del and monitors reach only the focused session")
        XCTAssertEqual(engine.sentInputSessions.count, 3)
        for name in ["remote_clipboard", "remote_chat_send", "remote_record_start"] {
            XCTAssertEqual(engine.calls(name).map { $0.object["session"] as? String }, ["id-2"], name)
        }
        XCTAssertEqual(engine.calls("remote_file_send").map { $0.object["consent"] as? String }, ["consent-2"])

        // Focus moves to the third tab: it gets the next ones, the second none.
        ws.selected = .session(peer(3))
        for n in [2, 3] {
            let model = try XCTUnwrap(ws.session(for: peer(n)))
            model.send(.text("x\(n)"))
            model.clipboard(push: false)
        }
        try await waitFor { engine.sentInputSessions.count >= 4 && engine.calls("remote_clipboard").count >= 2 }
        try await settle()
        XCTAssertEqual(engine.sentInputSessions.last?.session, "id-3")
        XCTAssertEqual(engine.sentInputSessions.count, 4)
        XCTAssertEqual(engine.calls("remote_clipboard").map { $0.object["session"] as? String }, ["id-2", "id-3"])
        app.stop()
    }

    func testDrawingBelongsToOneSession() async throws {
        let (app, engine) = try await app(sessions: 2)
        engine.reply("remote_draw", json: "true")
        let ws = app.workspace
        ws.selected = .session(peer(1))
        let first = try XCTUnwrap(ws.session(for: peer(1))).drawing
        let second = try XCTUnwrap(ws.session(for: peer(2))).drawing
        XCTAssertFalse(first === second, "every session has its own board")
        first.on = true
        first.color = 3
        first.begin(at: CGPoint(x: 0.2, y: 0.2)); first.end()
        XCTAssertTrue(first.on)
        XCTAssertFalse(second.on, "Draw was never switched on in the second session")
        XCTAssertEqual(second.color, 0)
        XCTAssertTrue(second.strokes.isEmpty)
        ws.selected = .session(peer(2))
        XCTAssertTrue(first.on, "switching the tab does not touch the other session's board")
        XCTAssertFalse(app.drawing.on, "the focused session's palette is the second one's")
        XCTAssertFalse(first.available, "an unfocused session shows no palette")
        app.stop()
    }

    func testAnUnfocusedSessionSendsNothing() async throws {
        let (app, engine) = try await app(sessions: 2)
        let ws = app.workspace
        XCTAssertEqual(ws.selected, .console)
        XCTAssertNil(ws.focusedPeer, "two sessions and the console in front: nobody is focused")
        for n in 1...2 {
            let model = try XCTUnwrap(ws.session(for: peer(n)))
            model.send(.key(usage: 4, down: true))
            model.press(5)
            model.lockNow()
            model.clipboard(push: true)
            model.toggleSound()
        }
        try await settle()
        XCTAssertTrue(engine.sentInputSessions.isEmpty)
        XCTAssertTrue(engine.calls("remote_clipboard").isEmpty)

        // A session window that is the key window is focused; the one behind it is not.
        ws.openWindow = { _ in }
        ws.closeWindow = { _ in }
        ws.popOut(peer(1))
        ws.popOut(peer(2))
        ws.windowFocusChanged(peer(1), focused: true)
        XCTAssertTrue(ws.isFocused(peer(1)))
        XCTAssertFalse(ws.isFocused(peer(2)))
        try XCTUnwrap(ws.session(for: peer(2))).send(.key(usage: 9, down: true))
        try XCTUnwrap(ws.session(for: peer(1))).send(.key(usage: 9, down: true))
        try await waitFor { engine.sentInputSessions.count >= 1 }
        try await settle()
        XCTAssertEqual(engine.sentInputSessions.map(\.session), ["id-1"])
        ws.windowFocusChanged(peer(1), focused: false)
        XCTAssertFalse(ws.isFocused(peer(1)))
        try XCTUnwrap(ws.session(for: peer(1))).send(.key(usage: 10, down: true))
        try await settle()
        XCTAssertEqual(engine.sentInputSessions.count, 1)
        app.stop()
    }

    func testEachSessionFetchesItsOwnPicture() async throws {
        let (app, engine) = try await app(sessions: 3)
        try await waitFor { Set(engine.videoFetches) == ["id-1", "id-2", "id-3"] }
        for n in 1...3 { XCTAssertNotNil(app.sessionModels[peer(n)]?.frame, "session \(n) has its own picture") }
        app.stop()
    }

    // MARK: Closing and ending

    func testClosingOneTabEndsOnlyThatSession() async throws {
        let (app, engine) = try await app(sessions: 3)
        let ws = app.workspace
        ws.end(peer(2))
        try await waitFor { !engine.calls("remote_close").isEmpty }
        try await settle()
        XCTAssertEqual(engine.calls("remote_close").map { $0.object["session"] as? String }, ["id-2"])
        XCTAssertTrue(engine.calls("remote_stop_session").isEmpty)
        // The engine confirms: only that tab goes.
        await show([item(1), item(3)], app, engine)
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(1), peer(3)])
        XCTAssertNotNil(ws.session(for: peer(1))?.frame, "the other pictures stay")
        XCTAssertNotNil(ws.session(for: peer(3))?.frame)
        XCTAssertEqual(engine.calls("remote_close").count, 1, "the other sessions were not closed")
        app.stop()
    }

    func testASessionEndingRemovesOnlyItsTabAndWindow() async throws {
        let (app, engine) = try await app(sessions: 3)
        let ws = app.workspace
        var closed: [UInt32] = []
        ws.openWindow = { _ in }
        ws.closeWindow = { closed.append($0) }
        ws.popOut(peer(1))
        ws.selected = .session(peer(2))
        await show([item(2), item(3)], app, engine)   // the first session ended (other side left)
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(2), peer(3)])
        XCTAssertFalse(ws.isInWindow(peer(1)))
        XCTAssertEqual(closed, [peer(1)])
        XCTAssertEqual(ws.selected, .session(peer(2)), "the selected tab is not disturbed")
        XCTAssertNil(app.sessionModels[peer(1)])
        XCTAssertEqual(app.sessionModels.count, 2)
        app.stop()
    }

    func testEndingTheSelectedTabFallsBackToDevices() async throws {
        let (app, engine) = try await app(sessions: 2)
        let ws = app.workspace
        ws.selected = .session(peer(1))
        await show([item(2)], app, engine)
        XCTAssertEqual(ws.selected, .console)
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(2)])
        app.stop()
    }

    // MARK: Connecting

    func testConnectStaysPossibleBelowTheLimitAndRequestsWithoutPhaseReady() async throws {
        let (app, engine) = try await app(sessions: 2)
        engine.reply("remote_request", json: "\"id-3\"")
        XCTAssertFalse(app.remote.connectBlockedBySession)
        XCTAssertTrue(app.remote.canRequestSession)
        try await app.connectSteps(id: peer(3), password: nil, unattended: false, checkPhone: false)
        XCTAssertEqual(engine.calls("remote_request").count, 1, "phase is active, not ready, and Connect still works")
        XCTAssertEqual(engine.calls("remote_request").first?.object["host"] as? Int, Int(peer(3)))
        app.stop()
    }

    func testFifthRequestShowsTheTypedErrorAsAFriendlySentence() async throws {
        let (app, engine) = try await app(sessions: 4)
        XCTAssertFalse(app.remote.canRequestSession, "four open")
        engine.fail("remote_request", tooMany)
        app.connect(to: peer(5), unattended: false, checkPhone: false)
        try await waitFor { !app.error.isEmpty }
        XCTAssertEqual(app.error, "You already have 4 sessions open, the most this app can hold. End one to connect to another computer.")
        XCTAssertFalse(app.error.contains("{"), "no JSON in front of the person")
        XCTAssertEqual(engine.calls("remote_request").count, 1, "the engine decides; nothing was dropped")
        XCTAssertEqual(app.workspace.tabs.count, 4, "the four sessions are untouched")
        app.stop()
    }

    func testTypedErrorTextsInGermanFrenchAndItalian() {
        let failure = EngineFailure(tooMany)
        XCTAssertEqual(failure.hint, "too_many_sessions")
        for (language, word) in [("de", "Sitzungen"), ("fr", "sessions"), ("it", "sessioni")] {
            Translator.shared.choice = language
            let text = failure.errorDescription ?? ""
            XCTAssertTrue(text.contains(word) && !text.contains("{") && text != "You already have 4 sessions open, the most this app can hold. End one to connect to another computer.", "\(language): \(text)")
        }
        Translator.shared.choice = "en"
        XCTAssertEqual(EngineFailure(#"{"hint":"already_connected","max":4,"message":"x"}"#).errorDescription, "You are already connected to this computer. Switch to its tab.")
        XCTAssertEqual(EngineFailure("plain text error").errorDescription, "plain text error")
    }

    func testRequestingSessionIsNotATabYetAndTheAttemptFollowsIt() async throws {
        let (app, engine) = try await app(sessions: 1)
        let ws = app.workspace
        ws.connect(peer: peer(2), name: "Second", unattended: false, checkPhone: false)
        engine.reply("remote_request", json: "\"id-2\"")
        await show([item(1), item(2, phase: "outgoing")], app, engine)
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(1)], "a request waiting for Accept is not a tab")
        XCTAssertTrue(app.remote.requesting)
        XCTAssertEqual(ws.attempt?.step, 1)
        engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 1), session: "id-2")
        await show([item(1), item(2)], app, engine)
        try await waitFor { app.sessionModels[self.peer(2)]?.frame != nil }
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(1), peer(2)])
        XCTAssertNil(ws.attempt, "ready: the notice takes over")
        app.stop()
    }

    func testRefusedUnattendedRequestShowsTheKeptRefusalAsAdviceForTheAdmin() async throws {
        let (app, engine) = try await app(sessions: 1)
        let ws = app.workspace
        ws.connect(peer: peer(2), name: "Second", unattended: true, checkPhone: false)
        engine.reply("remote_request", json: "\"id-2\"")
        await show([item(1), item(2, phase: "outgoing")], app, engine)
        try await waitFor { !app.busy }
        // The request is refused; another session is still in front, so the top-level message is not the reason.
        var refused = status([item(1)])
        refused.message = "Something about the session in front"
        refused.lastRefusal = Refusal(peer: peer(2), message: "The remote device is not available to you. Ask the other person to make their computer available.",
                                      at: UInt64(Date().timeIntervalSince1970))
        engine.reply("remote_status", refused)
        await app.refreshRemote()
        let failure = try XCTUnwrap(ws.attempt?.failure)
        XCTAssertTrue(failure.contains("Ask an admin to allow you unattended access to this computer"), failure)
        app.stop()
    }

    func testRefusalReadsFromTheEngineStatusAndTheListOfWhoMayConnectIsBuilt() throws {
        var sent = RemoteStatus(phase: "ready")
        sent.lastRefusal = Refusal(peer: 482_913_557, message: "The remote device is busy.", at: 1_789_000_000)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let wire = try encoder.encode(sent)
        XCTAssertTrue(String(decoding: wire, as: UTF8.self).contains("\"last_refusal\""))
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(RemoteStatus.self, from: wire)
        XCTAssertEqual(status.lastRefusal, Refusal(peer: 482_913_557, message: "The remote device is busy.", at: 1_789_000_000))
        XCTAssertNil(DevicesModel.unattendedRefusalAdvice("The remote device is busy."))
        let members = [
            Member(userId: "u2", name: "", email: "zed@example.com", role: "technician", createdAt: ""),
            Member(userId: "u1", name: "Anna", email: "anna@example.com", role: "owner", createdAt: ""),
            Member(userId: "u3", name: "Bob", email: "bob@example.com", role: "viewer", createdAt: ""),
            Member(userId: "u4", name: "Cleo", email: "cleo@example.com", role: "admin", createdAt: ""),
        ]
        let grants = [UnattendedGrant(userId: "u2", source: "grant"), UnattendedGrant(userId: "u4", source: "company_setting")]
        let rows = DevicesModel.grantRows(members: members, grants: grants)
        XCTAssertEqual(rows.map(\.userId), ["u1", "u4", "u2"], "viewers are left out; sorted by name (an empty name shows the e-mail)")
        XCTAssertEqual(rows.map(\.granted), [false, false, true])
        XCTAssertEqual(rows.map(\.automatic), [false, true, false])
    }

    func testPasswordAndUnattendedFlagsBelongToTheirOwnSession() async throws {
        let engine = MockEngine()
        var first = item(1)
        first.unattendedSession = true
        var second = item(2)
        second.passwordSession = PasswordSessionView(endsAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(240)), secondsLeft: 240)
        var all = status([first, second, item(3)])
        all.reconnecting = true
        Fixtures.company(engine)
        engine.reply("remote_status", all)
        let app = await Fixtures.app(engine)
        XCTAssertTrue(app.remote.isReconnecting, "the registration-lost banner reads the whole status")
        let one = try XCTUnwrap(app.sessionModels[peer(1)]).remote
        let two = try XCTUnwrap(app.sessionModels[peer(2)]).remote
        let three = try XCTUnwrap(app.sessionModels[peer(3)]).remote
        XCTAssertTrue(one.unattendedSession)
        XCTAssertNil(one.passwordSession)
        XCTAssertFalse(two.unattendedSession)
        XCTAssertEqual(two.passwordSession?.secondsLeft, 240, "the password warning is this session's own")
        XCTAssertFalse(three.unattendedSession)
        XCTAssertNil(three.passwordSession, "a session without the flags does not borrow another's")
        app.stop()
    }

    func testPerSessionFlagsDecodeAndAreOptional() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let with = #"{"id":"a","phase":"active","password_session":{"ends_at":"2026-11-17T12:00:00Z","seconds_left":299},"unattended_session":true,"reconnecting":false,"reconnect_failed":false}"#
        let item = try decoder.decode(RemoteSessionItem.self, from: Data(with.utf8))
        XCTAssertEqual(item.passwordSession, PasswordSessionView(endsAt: "2026-11-17T12:00:00Z", secondsLeft: 299))
        XCTAssertTrue(item.unattendedSession)
        let without = try decoder.decode(RemoteSessionItem.self, from: Data(#"{"id":"a","phase":"active"}"#.utf8))
        XCTAssertNil(without.passwordSession)
        XCTAssertFalse(without.unattendedSession || without.reconnecting || without.reconnectFailed)
        let retry = try decoder.decode(RemoteSessionItem.self, from: Data(#"{"id":"a","phase":"outgoing","reconnecting":true}"#.utf8))
        XCTAssertFalse(retry.isNewRequest, "a session asking its computer again is not a new request")
    }

    func testAnOldEngineSingleSessionKeepsTheStatusFlags() {
        var old = RemoteStatus(phase: "active", host: 482_913_557, session: "s", consent: "c", peer: peer(1), control: true, unattendedSession: true)
        old.passwordSession = PasswordSessionView(endsAt: "2026-11-17T12:00:00Z")
        let item = old.viewerSessions.first
        XCTAssertTrue(item?.unattendedSession == true)
        XCTAssertEqual(item?.passwordSession?.endsAt, "2026-11-17T12:00:00Z")
        let seen = old.describing(item!)
        XCTAssertTrue(seen.unattendedSession)
        XCTAssertNotNil(seen.passwordSession)
    }

    // MARK: Reconnect (the computer went away, the engine asks again)

    /// What the engine reports while session 2's computer is gone: the item stays (phase `outgoing`), the
    /// top-level session is empty only when nothing else runs; here session 1 keeps running.
    private func retrying(_ n: Int, failed: Bool = false) -> RemoteSessionItem {
        var it = item(n, phase: "outgoing")
        it.reconnecting = !failed
        it.reconnectFailed = failed
        it.control = false
        it.message = failed ? "Could not reconnect" : "Reconnecting..."
        return it
    }

    func testAReconnectingSessionKeepsItsTabAndShowsReconnecting() async throws {
        let (app, engine) = try await app(sessions: 2)
        let ws = app.workspace
        ws.show(peer(2))
        await show([item(1), retrying(2)], app, engine)
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(1), peer(2)], "the tab stays")
        XCTAssertEqual(ws.tab(peer(2))?.state, "reconnecting")
        XCTAssertEqual(ws.tab(peer(1))?.state, "ready", "the other session is untouched")
        XCTAssertEqual(ws.selected, .session(peer(2)), "not sent back to Devices")
        XCTAssertEqual(app.sessionModels[peer(2)]?.link, .reconnecting)
        XCTAssertFalse(app.remote.requesting, "a retrying session is not a new request")
        XCTAssertNotNil(ws.session(for: peer(2)), "its model is still there for End and Retry")
        // The computer is back: the same tab is live again.
        await show([item(1), item(2)], app, engine)
        XCTAssertEqual(app.sessionModels[peer(2)]?.link, .live)
        XCTAssertEqual(ws.tabs.map(\.sessionId), ["id-1", "id-2"])
        app.stop()
    }

    func testEndWorksWhileRetryingWhenTheTopLevelSessionIsEmpty() async throws {
        let (app, engine) = try await app(sessions: 1)
        var only = RemoteStatus(phase: "outgoing", message: "Reconnecting...", host: 482_913_557, sessions: [retrying(1)], maxSessions: 4)
        only.session = nil
        engine.reply("remote_status", only)
        await app.refreshRemote()
        XCTAssertNil(app.remote.session)
        XCTAssertEqual(app.workspace.tab(peer(1))?.state, "reconnecting")
        app.workspace.end(peer(1))
        try await waitFor { !engine.calls("remote_close").isEmpty }
        XCTAssertEqual(engine.calls("remote_close").map { $0.object["session"] as? String }, ["id-1"], "the stable id, not the empty status session")
        app.stop()
    }

    func testCouldNotReconnectOffersRetryWhichSendsRemoteReconnect() async throws {
        let (app, engine) = try await app(sessions: 2)
        await show([item(1), retrying(2, failed: true)], app, engine)
        let ws = app.workspace
        XCTAssertEqual(ws.tab(peer(2))?.state, "reconnect_failed")
        XCTAssertEqual(app.sessionModels[peer(2)]?.link, .failed)
        XCTAssertEqual(app.sessionModels[peer(2)]?.remote.message, "Could not reconnect")
        // Retry only exists in that state: a live session sends nothing.
        app.sessionModels[peer(1)]?.retryReconnect()
        try await Task.sleep(nanoseconds: 150_000_000)
        XCTAssertTrue(engine.calls("remote_reconnect").isEmpty)
        app.sessionModels[peer(2)]?.retryReconnect()
        try await waitFor { !engine.calls("remote_reconnect").isEmpty }
        XCTAssertEqual(engine.calls("remote_reconnect").map { $0.object["session"] as? String }, ["id-2"])
        XCTAssertTrue(engine.calls("remote_close").isEmpty, "Retry does not end the session")
        // Retry restarts the minute: reconnecting again, same tab.
        await show([item(1), retrying(2)], app, engine)
        XCTAssertEqual(ws.tab(peer(2))?.state, "reconnecting")
        // End works from the failed state too.
        await show([item(1), retrying(2, failed: true)], app, engine)
        ws.end(peer(2))
        try await waitFor { !engine.calls("remote_close").isEmpty }
        XCTAssertEqual(engine.calls("remote_close").map { $0.object["session"] as? String }, ["id-2"])
        // The engine drops it: the tab goes.
        await show([item(1)], app, engine)
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(1)])
        app.stop()
    }

    func testCancelRequestsDoesNotEndARetryingSession() async throws {
        let (app, engine) = try await app(sessions: 2)
        await show([item(1), retrying(2), item(3, phase: "outgoing")], app, engine)
        app.cancelRequests()
        try await waitFor { !engine.calls("remote_close").isEmpty }
        try await settle()
        XCTAssertEqual(engine.calls("remote_close").map { $0.object["session"] as? String }, ["id-3"], "only the new request is cancelled")
        app.stop()
    }

    // MARK: Row menu "Send files..."

    private let filesConnected = #"{"connected":true,"incoming":null,"outgoing":null,"message":""}"#

    func testSendFilesFromTheRowUsesTheRunningSession() async throws {
        let (app, engine) = try await app(sessions: 2)
        engine.reply("remote_file_status", json: filesConnected)
        var picked = 0
        app.chooseFile = { picked += 1; return "/tmp/report.pdf" }
        app.workspace.sendFiles(to: peer(2))
        try await waitFor { !engine.calls("remote_file_send").isEmpty }
        XCTAssertEqual(picked, 1)
        XCTAssertTrue(engine.calls("remote_request").isEmpty, "no second connection to a device that has a session")
        XCTAssertEqual(engine.calls("remote_file_send").map { $0.object["consent"] as? String }, ["consent-2"])
        XCTAssertEqual(engine.calls("remote_file_send").first?.object["path"] as? String, "/tmp/report.pdf")
        XCTAssertEqual(app.workspace.selected, .session(peer(2)), "the session is brought forward")
        app.stop()
    }

    func testSendFilesConnectsFirstThenOpensThePicker() async throws {
        let (app, engine) = try await app(sessions: 1)
        engine.reply("remote_file_status", json: filesConnected)
        engine.reply("remote_request", json: "\"id-3\"")
        var picked = 0
        app.chooseFile = { picked += 1; return "/tmp/a.txt" }
        let ws = app.workspace
        ws.sendFiles(to: peer(3), name: "SRV-03")
        XCTAssertTrue(ws.isWaitingToSendFiles(peer(3)))
        try await waitFor { !engine.calls("remote_request").isEmpty }
        XCTAssertEqual(engine.calls("remote_request").first?.object["host"] as? Int, Int(peer(3)))
        XCTAssertEqual(picked, 0, "the picker waits for the session")
        engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 1), session: "id-3")
        await show([item(1), item(3)], app, engine)
        try await waitFor { app.sessionModels[self.peer(3)]?.frame != nil }
        ws.refresh()
        try await waitFor { !engine.calls("remote_file_send").isEmpty }
        XCTAssertEqual(picked, 1)
        XCTAssertFalse(ws.isWaitingToSendFiles(peer(3)))
        XCTAssertEqual(engine.calls("remote_file_send").map { $0.object["consent"] as? String }, ["consent-3"])
        app.stop()
    }

    func testSendFilesWhenTheSessionDoesNotAllowFilesSaysSoAndOpensNoPicker() async throws {
        let (app, engine) = try await app(sessions: 1)
        var noFiles = item(1)
        noFiles.files = false
        await show([noFiles], app, engine)
        var picked = 0
        app.chooseFile = { picked += 1; return "/tmp/a.txt" }
        app.workspace.sendFiles(to: peer(1))
        try await settle()
        XCTAssertEqual(picked, 0)
        XCTAssertEqual(app.sessionModels[peer(1)]?.error, "File transfer was not allowed for this session.")
        XCTAssertTrue(engine.calls("remote_file_send").isEmpty)
        app.stop()
    }

    func testACancelledConnectionForgetsThePendingFilePicker() async throws {
        let (app, engine) = try await app(sessions: 1)
        engine.reply("remote_request", json: "\"id-3\"")
        let ws = app.workspace
        ws.sendFiles(to: peer(3))
        try await waitFor { !engine.calls("remote_request").isEmpty }
        ws.cancelAttempt()
        XCTAssertFalse(ws.isWaitingToSendFiles(peer(3)))
        app.stop()
    }

    // MARK: Older engine, renewal

    func testAnEngineWithoutSessionsStillWorksWithOneSession() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 1))
        let app = await Fixtures.app(engine)
        try await waitFor { app.session.frame != nil }
        app.workspace.refresh()
        XCTAssertNil(app.remote.sessions, "an old engine sends no sessions")
        XCTAssertEqual(app.remote.viewerSessions.count, 1)
        XCTAssertEqual(app.workspace.tabs.map(\.peer), [731_204_118])
        XCTAssertEqual(app.workspace.tabs.first?.sessionId, Fixtures.session)
        XCTAssertEqual(app.workspace.tabs.first?.state, "ready")
        XCTAssertTrue(app.remote.connectBlockedBySession, "one session at a time against an old engine")
        app.session.send(.key(usage: 4, down: true))
        try await waitFor { !engine.sentInputSessions.isEmpty }
        XCTAssertEqual(engine.sentInputSessions.first?.session, Fixtures.session)
        app.session.end()
        try await waitFor { !engine.calls("remote_close").isEmpty }
        XCTAssertEqual(engine.calls("remote_close").first?.object["session"] as? String, Fixtures.session)
        app.stop()
    }

    func testTransportRenewalKeepsTheTabAndTheSession() async throws {
        let (app, engine) = try await app(sessions: 2)
        let ws = app.workspace
        ws.selected = .session(peer(1))
        let model = try XCTUnwrap(ws.session(for: peer(1)))
        let fetchesBefore = engine.videoFetches.filter { $0 == "id-1" }.count
        // The connection service's id of the transport changes (about every 8.5 minutes); the stable id does not.
        await show([item(1, transport: "transport-1-renewed"), item(2)], app, engine)
        XCTAssertEqual(ws.tabs.map(\.peer), [peer(1), peer(2)])
        XCTAssertEqual(ws.tabs.first?.sessionId, "id-1")
        XCTAssertTrue(ws.session(for: peer(1)) === model, "same session model")
        XCTAssertEqual(ws.selected, .session(peer(1)), "same tab stays in front")
        XCTAssertEqual(model.sessionId, "id-1")
        XCTAssertEqual(model.remote.session, "transport-1-renewed")
        XCTAssertNotNil(model.frame, "the picture was not thrown away")
        model.send(.key(usage: 4, down: true))
        try await waitFor { !engine.sentInputSessions.isEmpty }
        XCTAssertEqual(engine.sentInputSessions.last?.session, "id-1", "calls keep carrying the stable id")
        try await Task.sleep(nanoseconds: 150_000_000)
        XCTAssertGreaterThanOrEqual(engine.videoFetches.filter { $0 == "id-1" }.count, fetchesBefore, "still fetching pictures of the same session")
        app.stop()
    }
}
