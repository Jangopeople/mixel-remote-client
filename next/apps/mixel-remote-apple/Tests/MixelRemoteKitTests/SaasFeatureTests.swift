import XCTest
@testable import MixelRemoteKit

/// Drawing on the remote screen, favorite and recent devices, session reports (SQL 029 / engine).
@MainActor
final class SaasFeatureTests: XCTestCase {
    override func setUp() { Translator.shared.choice = "en" }

    private func waitFor(_ condition: @escaping () -> Bool, timeout: TimeInterval = 4) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() > deadline { XCTFail("timed out"); return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    private func drawMessages(_ engine: MockEngine) -> [[String: Any]] {
        engine.calls("remote_draw").compactMap { $0.object["message"] as? [String: Any] }
    }

    func testDrawingSendsStrokesInTheEngineFormatOnlyWhenTheHostShowsThem() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        var status = RemoteStatus(phase: "active", host: 482_913_557, session: Fixtures.session, consent: Fixtures.consent, peer: 731_204_118, control: false, peerFeatures: ["draw"])
        engine.reply("remote_status", status)
        engine.reply("remote_draw", json: "true")
        let app = await Fixtures.app(engine)
        XCTAssertTrue(app.drawing.available, "view-only sessions can draw too")
        app.drawing.on = true
        app.drawing.tool = .arrow
        app.drawing.color = 3
        app.drawing.width = 4
        app.drawing.begin(at: CGPoint(x: 0, y: 0))
        app.drawing.add(CGPoint(x: 0.5, y: 0.25))
        app.drawing.add(CGPoint(x: 1.2, y: 1))
        app.drawing.end()
        try await waitFor { self.drawMessages(engine).contains { $0["done"] as? Bool == true } }
        let messages = drawMessages(engine)
        let first = try XCTUnwrap(messages.first)
        XCTAssertEqual(first["kind"] as? String, "stroke")
        XCTAssertEqual(first["tool"] as? String, "arrow")
        XCTAssertEqual(first["color"] as? Int, 3)
        XCTAssertEqual(first["width"] as? Int, 4)
        XCTAssertEqual((first["points"] as? [[Int]])?.first, [0, 0])
        let all = messages.flatMap { $0["points"] as? [[Int]] ?? [] }
        XCTAssertTrue(all.contains([65535, 65535]), "points are clamped to the picture")
        XCTAssertTrue(messages.allSatisfy { ($0["points"] as? [Any])?.count ?? 0 <= 64 })
        app.drawing.undo()
        app.drawing.clear()
        try await waitFor { self.drawMessages(engine).contains { $0["kind"] as? String == "clear" } }
        XCTAssertTrue(drawMessages(engine).contains { $0["kind"] as? String == "undo" })
        // The local drawing fades like on the other computer.
        app.drawing.begin(at: CGPoint(x: 0.1, y: 0.1)); app.drawing.end()
        XCTAssertEqual(app.drawing.opacity(at: Date()), 1)
        XCTAssertEqual(app.drawing.opacity(at: Date().addingTimeInterval(10)), 0)

        status.peerFeatures = []
        engine.reply("remote_status", status)
        await app.refreshRemote()
        XCTAssertFalse(app.drawing.available, "no drawing for a host that cannot show it")
    }

    func testFavoritesAndRecentDevicesComeFirstAndToggle() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("saas_favorites", json: #"[{"device_id":"d1000000-0000-4000-8000-000000000003","created_at":"2026-11-13T08:00:00Z"}]"#)
        engine.reply("saas_recent_devices", json: #"[{"device_id":"d1000000-0000-4000-8000-000000000001","last_connected_at":"2026-11-13T09:00:00Z"},{"device_id":"d1000000-0000-4000-8000-000000000003","last_connected_at":"2026-11-12T09:00:00Z"}]"#)
        let app = await Fixtures.app(engine)
        XCTAssertEqual(app.devices.favoriteDevices.map(\.name), ["Warehouse laptop"])
        XCTAssertEqual(app.devices.recentDevices.map(\.name), ["Reception PC"], "a favorite is not listed again under recent")
        let reception = try XCTUnwrap(app.devices.devices.first { $0.name == "Reception PC" })
        app.devices.toggleFavorite(reception)
        XCTAssertTrue(app.devices.isFavorite(reception))
        try await waitFor { !engine.calls("saas_set_favorite").isEmpty }
        let call = try XCTUnwrap(engine.calls("saas_set_favorite").last).object
        XCTAssertEqual(call["favorite"] as? Bool, true)
        XCTAssertEqual(call["device"] as? String, reception.device)
    }

    func testSessionReportLoadsExportsAndSavesNotes() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("saas_tenant_overview", json: #"{"role":"owner","status":"active","plan":"business","features":{"groups":true,"reports":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}"#)
        engine.reply("saas_session_report", json: #"[{"session_id":"s1","technician_id":"t1","technician_name":"Anna Muster","technician_email":"anna@muster.test","device_id":"d1","device_name":"Reception PC","connection_id":731204118,"started_at":"2026-11-12T08:00:00Z","ended_at":"2026-11-12T08:12:30Z","duration_seconds":750,"kind":"unattended","colleague":false,"note":"=SUM(A1) printer"}]"#)
        let app = await Fixtures.app(engine)
        await app.company.load()
        XCTAssertTrue(app.reports.included)
        await app.reports.load()
        XCTAssertEqual(app.reports.rows.count, 1)
        let args = try XCTUnwrap(engine.calls("saas_session_report").last).object
        XCTAssertNotNil(args["from"] as? String)
        XCTAssertNotNil(args["to"] as? String)
        let csv = app.reports.csv()
        XCTAssertTrue(csv.hasPrefix("\u{FEFF}Start,End,Duration"))
        XCTAssertTrue(csv.contains("12:30"))
        XCTAssertTrue(csv.contains("'=SUM(A1) printer"), "no formula from a note")
        XCTAssertTrue(app.reports.pdf().starts(with: Data("%PDF".utf8)))
        let saved = await app.reports.setNote(app.reports.rows[0], "Done")
        XCTAssertTrue(saved)
        XCTAssertEqual(engine.calls("saas_session_note").last?.object["session"] as? String, "s1")

        engine.fail("saas_session_report", "Session reports need a paid plan")
        await app.reports.load()
        XCTAssertTrue(app.reports.needsPlan)
    }

    func testAfterAControlledSessionWithACompanyDeviceANoteIsAsked() async throws {
        let engine = MockEngine()
        Fixtures.viewing(engine)
        engine.reply("saas_tenant_overview", json: #"{"role":"owner","status":"active","plan":"business","features":{"groups":true,"reports":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}"#)
        let app = await Fixtures.app(engine)
        await app.company.load()
        await app.refreshRemote()
        engine.reply("remote_status", RemoteStatus(phase: "ready", host: 482_913_557))
        await app.refreshRemote()
        try await waitFor({ app.reports.noteFor == 731_204_118 }, timeout: 4)
        let noted = await app.reports.saveNote("Printer fixed", for: 731_204_118)
        XCTAssertTrue(noted)
        let call = try XCTUnwrap(engine.calls("saas_session_note").last).object
        XCTAssertEqual(call["connection"] as? Int, 731_204_118)
    }
}
