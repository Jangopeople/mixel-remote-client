import SwiftUI
import XCTest
#if os(macOS)
import AppKit
#endif
@testable import MixelRemoteKit

/// The redesign's Mac console: session notices and the single pointer, device administration behind
/// its protocol, reports (period, search, numbers), settings (appearance, "stay signed in") and the
/// sign-in screen.
@MainActor
final class ConsoleRedesignTests: XCTestCase {
    override func setUp() {
        Translator.shared.choice = "en"
        UserDefaults.standard.removeObject(forKey: SettingsModel.appearanceKey)
        UserDefaults.standard.removeObject(forKey: SettingsModel.stayKey)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: SettingsModel.appearanceKey)
        UserDefaults.standard.removeObject(forKey: SettingsModel.stayKey)
        #if os(macOS)
        NSApp?.appearance = nil
        #endif
    }

    private func waitFor(_ condition: @escaping () -> Bool, timeout: TimeInterval = 4) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() > deadline { XCTFail("timed out"); return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    // MARK: Password-session notice

    func testPasswordSessionWarnsAtFiveMinutesAndOneMinute() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        func notice(_ left: TimeInterval) -> SessionNotice? {
            SessionNotice.make(passwordSession: true, sentPassword: false, unattended: false, endsAt: now.addingTimeInterval(left), now: now)
        }
        XCTAssertEqual(notice(3600)?.level, .info)
        XCTAssertEqual(notice(3600)?.kind, .password(seconds: 3600))
        XCTAssertEqual(notice(301)?.level, .info)
        XCTAssertEqual(notice(300)?.level, .warning, "5 minutes")
        XCTAssertEqual(notice(61)?.level, .warning)
        XCTAssertEqual(notice(60)?.level, .urgent, "1 minute")
        XCTAssertEqual(notice(0)?.kind, .password(seconds: 0))
        XCTAssertEqual(notice(-30)?.kind, .password(seconds: 0), "past the end never shows a negative time")
        XCTAssertEqual(notice(-30)?.level, .urgent)
        XCTAssertEqual(SessionNotice.clock(5025), "1:23:45")
        XCTAssertEqual(SessionNotice.clock(299), "4:59")
    }

    func testNoticeKindsAndOrdinarySessions() {
        let now = Date()
        XCTAssertNil(SessionNotice.make(passwordSession: false, sentPassword: false, unattended: false, endsAt: nil, now: now), "an ordinary session shows nothing")
        XCTAssertEqual(SessionNotice.make(passwordSession: false, sentPassword: false, unattended: true, endsAt: nil, now: now)?.kind, .unattended)
        // The app's own request carried a password but the engine reports no end yet: the banner is there, without a time.
        XCTAssertEqual(SessionNotice.make(passwordSession: false, sentPassword: true, unattended: false, endsAt: nil, now: now)?.kind, .password(seconds: nil))
        // A password wins over "unattended".
        XCTAssertEqual(SessionNotice.make(passwordSession: true, sentPassword: false, unattended: true, endsAt: nil, now: now)?.kind, .password(seconds: nil))
    }

    func testPasswordSessionFieldIsReadFromTheEngineStatus() throws {
        let json = #"{"controller_only":false,"platform":"macos","phase":"active","message":"","received":0,"sharing":false,"control":true,"clipboard":false,"files":false,"recording_permitted":false,"input_access":true,"unattended_enabled":false,"unattended_session":false,"code_required":false,"online":[],"displays":1,"display":0,"display_all":false,"print_jobs":[],"host_actions":[],"host_macs":[],"colleagues":[],"invitations":[],"joined_session":false,"switched":false,"peer_features":[],"password_session":{"ends_at":"2026-11-17T12:00:00Z"}}"#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(RemoteStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.passwordSession?.endsAt, "2026-11-17T12:00:00Z")
        let without = try decoder.decode(RemoteStatus.self, from: Data(json.replacingOccurrences(of: #","password_session":{"ends_at":"2026-11-17T12:00:00Z"}"#, with: "").utf8))
        XCTAssertNil(without.passwordSession, "engines that do not report it yet still decode")
    }

    func testTheAppRemembersThatItSentAPassword() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        try await app.connectSteps(id: 731_204_118, password: "123456", unattended: false, checkPhone: false)
        XCTAssertTrue(app.session.lastRequestPassword)
        try await app.connectSteps(id: 731_204_118, password: nil, unattended: true, checkPhone: false)
        XCTAssertFalse(app.session.lastRequestPassword)
    }

    // MARK: One pointer

    func testPointerPolicyHasOneLocalPointerAndNoOverlay() {
        XCTAssertTrue(PointerPolicy.showsLocalCursor)
        XCTAssertFalse(PointerPolicy.drawsOverlayPointer, "never a second pointer over the picture")
        XCTAssertEqual(PointerPolicy.cursor(pointerMode: false), .arrow)
        XCTAssertEqual(PointerPolicy.cursor(pointerMode: true), .crosshair)
    }

    #if os(macOS)
    func testTheRemoteScreenDrawsNoPointerOfItsOwn() throws {
        let view = RemoteScreenView(frame: NSRect(x: 0, y: 0, width: 640, height: 400))
        let sample = try XCTUnwrap(VideoFrame.sample(width: 64, height: 40, sequence: 1).count > 0 ? makeImage() : nil)
        view.image = sample
        view.controllable = true
        view.pointerMode = true
        XCTAssertTrue(view.subviews.isEmpty, "no pointer view over the picture")
        XCTAssertTrue(view.layer?.sublayers?.isEmpty ?? true, "no pointer layer over the picture")
        view.pointerMode = false
        XCTAssertTrue(view.subviews.isEmpty)
        XCTAssertTrue(view.layer?.sublayers?.isEmpty ?? true)
    }

    private func makeImage() -> CGImage? {
        let context = CGContext(data: nil, width: 64, height: 40, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        context?.setFillColor(CGColor(red: 0.2, green: 0.4, blue: 0.8, alpha: 1))
        context?.fill(CGRect(x: 0, y: 0, width: 64, height: 40))
        return context?.makeImage()
    }
    #endif

    // MARK: Device administration

    func testWithoutAdminCommandsTheConsoleShowsNothingInvented() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        XCTAssertFalse(app.devices.adminAvailable)
        XCTAssertFalse(app.devices.versionsKnown)
        XCTAssertTrue(app.devices.adminRows.isEmpty)
        XCTAssertEqual(app.devices.outdatedCount, 0)
        let device = try XCTUnwrap(app.devices.allDevices.first)
        XCTAssertEqual(app.devices.note(of: device), "")
        XCTAssertFalse(app.devices.isOutdated(device))
        do { _ = try await UnavailableDeviceAdminService().devices(company: nil, search: nil); XCTFail("must throw") }
        catch { XCTAssertEqual(error as? DeviceAdminError, .unavailable) }
        // Rename still works through the company commands the app has had all along.
        engine.reply("saas_rename_device", json: "\"Front desk\"")
        let renamed = await app.devices.rename(device, to: "Front desk")
        XCTAssertTrue(renamed)
        XCTAssertFalse(engine.calls("saas_rename_device").isEmpty)
    }

    private func adminJSON(version: (String, String), note: String? = "Desk 3") -> String {
        let n = note.map { "\"\($0)\"" } ?? "null"
        return """
        [{"device_id":"d1000000-0000-4000-8000-000000000001","connection_id":731204118,"name":"Reception PC","os":"windows","note":\(n),"unattended_setting":"on","unattended_default":false,"unattended_allowed":true,"status":"online","app_version":"\(version.0)","updated_at":"2026-11-17T08:00:00Z","can_admin":true},
         {"device_id":"d1000000-0000-4000-8000-000000000002","connection_id":268455901,"name":"Accounting Mac","os":"macos","note":null,"unattended_setting":"off","unattended_allowed":false,"status":"offline","app_version":"\(version.1)","can_admin":true}]
        """
    }

    func testAdminRowsGiveNotesUnattendedBadgesVersionsAndTheUpdateFilter() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("remote_admin_devices", json: adminJSON(version: ("2.0.18", "2.0.19")))
        let app = await Fixtures.app(engine)
        app.devices.admin = EngineDeviceAdminService(engine: engine)
        await app.devices.load()
        let model = app.devices
        XCTAssertTrue(model.adminAvailable)
        XCTAssertTrue(model.versionsKnown)
        XCTAssertEqual(model.newestVersion, "2.0.19")
        let reception = try XCTUnwrap(model.allDevices.first { $0.connectionId == 731_204_118 })
        let accounting = try XCTUnwrap(model.allDevices.first { $0.connectionId == 268_455_901 })
        XCTAssertEqual(model.note(of: reception), "Desk 3")
        XCTAssertEqual(model.adminRow(reception)?.unattendedOn, true)
        XCTAssertEqual(model.adminRow(accounting)?.unattendedOn, false)
        XCTAssertTrue(model.isOutdated(reception))
        XCTAssertFalse(model.isOutdated(accounting))
        XCTAssertEqual(model.outdatedCount, 1)
        model.updateOnly = true
        XCTAssertEqual(model.filtered.map(\.name), ["Reception PC"], "the Update filter lists the older ones only")
        model.updateOnly = false
        model.filterText = "desk 3"
        XCTAssertEqual(model.filtered.map(\.name), ["Reception PC"], "search finds a note")
    }

    func testNoteUnattendedRenameAndRemoveUseTheAdminCommands() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("remote_admin_devices", json: adminJSON(version: ("2.0.19", "2.0.19")))
        engine.reply("remote_admin_set_device_note", json: #"{"device_id":"d1000000-0000-4000-8000-000000000001","connection_id":731204118,"name":"Reception PC","os":"windows"}"#)
        engine.reply("remote_admin_set_device_unattended", json: #"{"device_id":"d1000000-0000-4000-8000-000000000001","connection_id":731204118,"name":"Reception PC","os":"windows"}"#)
        engine.reply("remote_admin_rename_device", json: #"{"device_id":"d1000000-0000-4000-8000-000000000001","connection_id":731204118,"name":"Front desk","os":"windows"}"#)
        engine.reply("remote_admin_remove_device", json: #"{"removed":true}"#)
        let app = await Fixtures.app(engine)
        app.devices.admin = EngineDeviceAdminService(engine: engine)
        await app.devices.load()
        let device = try XCTUnwrap(app.devices.allDevices.first { $0.connectionId == 731_204_118 })

        var saved = await app.devices.setNote(device, to: "  Behind the counter ")
        XCTAssertTrue(saved)
        var call = try XCTUnwrap(engine.calls("remote_admin_set_device_note").last).object
        XCTAssertEqual(call["note"] as? String, "Behind the counter")
        XCTAssertEqual(call["device"] as? String, device.device)
        saved = await app.devices.setNote(device, to: "   ")
        call = try XCTUnwrap(engine.calls("remote_admin_set_device_note").last).object
        XCTAssertTrue(call["note"] is NSNull, "an empty note clears it")
        let tooLong = await app.devices.setNote(device, to: String(repeating: "x", count: 501))
        XCTAssertFalse(tooLong)

        saved = await app.devices.setUnattended(device, on: false)
        XCTAssertTrue(saved)
        XCTAssertEqual(engine.calls("remote_admin_set_device_unattended").last?.object["setting"] as? String, "off")

        saved = await app.devices.rename(device, to: "Front desk")
        XCTAssertTrue(saved)
        call = try XCTUnwrap(engine.calls("remote_admin_rename_device").last).object
        XCTAssertEqual(call["device_name"] as? String, "Front desk")
        XCTAssertEqual(call["expected_update"] as? String, "2026-11-17T08:00:00Z", "the list's updated_at guards against a stale rename")

        saved = await app.devices.remove(device)
        XCTAssertTrue(saved)
        call = try XCTUnwrap(engine.calls("remote_admin_remove_device").last).object
        XCTAssertEqual(call["confirm_connection_id"] as? Int, 731_204_118, "the server checks the 9-digit ID")
        XCTAssertTrue(engine.calls("saas_remove_device").isEmpty, "not the older company command")
    }

    func testAnEngineWithoutTheCommandsMeansNotAvailableAndServerHintsAreKept() async throws {
        let engine = MockEngine()
        engine.fail("remote_admin_devices", "unknown command: remote_admin_devices")
        let service = EngineDeviceAdminService(engine: engine)
        do { _ = try await service.devices(company: nil, search: nil); XCTFail("must throw") }
        catch { XCTAssertEqual(error as? DeviceAdminError, .unavailable) }
        engine.fail("remote_admin_set_device_note", #"{"code":"42501","message":"not allowed","hint":"not_admin"}"#)
        do { _ = try await service.setNote(device: "d", note: "x"); XCTFail("must throw") }
        catch {
            XCTAssertEqual(error as? DeviceAdminError, .refused(hint: "not_admin", message: #"{"code":"42501","message":"not allowed","hint":"not_admin"}"#))
            XCTAssertEqual(error.localizedDescription, "Only an owner or administrator can change devices.")
        }
    }

    func testTheEnginesTypedFailureTextKeepsItsHint() async throws {
        let engine = MockEngine()
        let service = EngineDeviceAdminService(engine: engine)
        // The engine's failure text for a refused remote_admin_* call: {"hint", "message", "status"}.
        let wrong = #"{"status":400,"hint":"confirm_mismatch","message":"The ID does not match"}"#
        engine.fail("remote_admin_remove_device", wrong)
        do { try await service.remove(device: "d", confirmConnectionId: 123_456_789); XCTFail("must throw") }
        catch {
            XCTAssertEqual(error as? DeviceAdminError, .refused(hint: "confirm_mismatch", message: wrong))
            XCTAssertEqual(error.localizedDescription, "The ID does not match this device.")
        }
        engine.fail("remote_admin_devices", #"{"status":0,"hint":"offline","message":"Mixel Remote is unavailable. Please try again."}"#)
        do { _ = try await service.devices(company: nil, search: nil); XCTFail("must throw") }
        catch { XCTAssertEqual((error as? DeviceAdminError).map { if case .refused(let hint, _) = $0 { hint } else { "" } }, "offline") }
    }

    func testVersionOrder() {
        XCTAssertTrue(AppVersion.isOlder("2.0.9", than: "2.0.19"))
        XCTAssertFalse(AppVersion.isOlder("2.0.19", than: "2.0.19"))
        XCTAssertFalse(AppVersion.isOlder(nil, than: "2.0.19"), "unknown is not 'older'")
        XCTAssertTrue(AppVersion.isOlder("2.0", than: "2.0.1"))
        XCTAssertEqual(AppVersion.newest(["2.0.18", nil, "2.1.0", "2.0.19", "beta"]), "2.1.0")
    }

    // MARK: Reports

    private func reportApp() async -> (AppModel, MockEngine) {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("saas_tenant_overview", json: #"{"role":"owner","status":"active","plan":"business","features":{"groups":true,"reports":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}"#)
        engine.reply("saas_session_report", json: """
        [{"session_id":"s1","technician_id":"t1","technician_name":"Anna Muster","technician_email":"anna@muster.test","device_id":"d1","device_name":"Reception PC","connection_id":731204118,"started_at":"2026-11-12T08:00:00Z","ended_at":"2026-11-12T08:10:00Z","duration_seconds":600,"kind":"unattended","colleague":false,"note":"Printer driver updated","ticket":"MIX-4821","recording":true},
         {"session_id":"s2","technician_id":"t2","technician_name":"Marco Rossi","technician_email":"marco@muster.test","device_id":"d2","device_name":"Accounting Mac","connection_id":268455901,"started_at":"2026-11-13T09:30:00Z","ended_at":"2026-11-13T09:50:00Z","duration_seconds":1200,"kind":"attended","colleague":false,"note":null},
         {"session_id":"s3","technician_id":"t2","technician_name":"Marco Rossi","technician_email":"marco@muster.test","device_id":"d2","device_name":"Accounting Mac","connection_id":268455901,"started_at":"2026-11-14T09:30:00Z","ended_at":"2026-11-14T09:40:00Z","duration_seconds":600,"kind":"attended","colleague":false,"note":""}]
        """)
        let app = await Fixtures.app(engine)
        await app.company.load()
        await app.reports.load()
        return (app, engine)
    }

    func testReportNumbersAreCountedFromTheRowsOnly() async throws {
        let (app, _) = await reportApp()
        let sum = app.reports.summary
        XCTAssertEqual(sum.sessions, 3)
        XCTAssertEqual(sum.totalSeconds, 2400)
        XCTAssertEqual(sum.averageSeconds, 800)
        XCTAssertEqual(sum.unattended, 1)
        XCTAssertEqual(sum.unattendedPercent, 33)
        XCTAssertEqual(sum.withNote, 1, "an empty note is not a note")
        XCTAssertEqual(sum.notedPercent, 33)
        XCTAssertEqual(sum.withTicket, 1)
        XCTAssertEqual(ReportsModel.hoursMinutes(41 * 3600 + 12 * 60), "41 h 12 min")
        XCTAssertEqual(ReportsModel.hoursMinutes(19 * 60), "19 min")
        XCTAssertEqual(ReportsModel.hoursMinutes(45), "45 s")
        let none = ReportsModel.Summary(sessions: 0, totalSeconds: 0, unattended: 0, withNote: 0, withTicket: 0)
        XCTAssertEqual(none.unattendedPercent, 0)
        XCTAssertEqual(none.averageSeconds, 0)
    }

    func testReportSearchFiltersDevicesTechniciansTicketsAndNotesAndTheExport() async throws {
        let (app, _) = await reportApp()
        let reports = app.reports
        reports.search = "mix-4821"
        XCTAssertEqual(reports.visibleRows.map(\.sessionId), ["s1"], "ticket")
        reports.search = "accounting"
        XCTAssertEqual(reports.visibleRows.map(\.sessionId), ["s2", "s3"], "device")
        reports.search = "driver"
        XCTAssertEqual(reports.visibleRows.map(\.sessionId), ["s1"], "note")
        reports.search = "rossi"
        XCTAssertEqual(reports.visibleRows.count, 2, "technician")
        XCTAssertEqual(reports.summary.sessions, 2, "the KPI cards follow the filter")
        let csv = reports.csv()
        XCTAssertTrue(csv.contains("Accounting Mac"))
        XCTAssertFalse(csv.contains("Reception PC"), "the export holds what is shown")
        reports.search = ""
        XCTAssertEqual(reports.visibleRows.count, 3)
    }

    func testPeriodButtonsSetTheDatesAndReloadTheReport() async throws {
        let (app, engine) = await reportApp()
        let reports = app.reports
        XCTAssertEqual(reports.period, .week, "7 days by default")
        let now = Date()
        await reports.choose(.today, now: now)
        XCTAssertEqual(ReportsModel.day(reports.from), ReportsModel.day(now))
        XCTAssertEqual(ReportsModel.day(reports.to), ReportsModel.day(now))
        await reports.choose(.month, now: now)
        let thirty = Calendar.current.date(byAdding: .day, value: -29, to: now) ?? now
        XCTAssertEqual(ReportsModel.day(reports.from), ReportsModel.day(thirty))
        let last = try XCTUnwrap(engine.calls("saas_session_report").last).object
        XCTAssertEqual(last["from"] as? String, ReportsModel.day(thirty))
        reports.from = Calendar.current.date(byAdding: .day, value: -3, to: now) ?? now
        await reports.dateChanged()
        XCTAssertEqual(reports.period, .custom)
    }

    // MARK: Settings

    func testAppearanceIsRememberedAndAppliedToTheApp() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        XCTAssertEqual(app.settings.appearance, .system)
        app.settings.appearance = .dark
        XCTAssertEqual(UserDefaults.standard.string(forKey: SettingsModel.appearanceKey), "dark")
        #if os(macOS)
        if NSApp != nil {
            XCTAssertEqual(NSApp.appearance?.name, .darkAqua)
            app.settings.appearance = .light
            XCTAssertEqual(NSApp.appearance?.name, .aqua)
            app.settings.appearance = .system
            XCTAssertNil(NSApp.appearance, "System follows this Mac")
        }
        #endif
        app.settings.appearance = .dark
        let again = AppModel(engine: engine)
        XCTAssertEqual(again.settings.appearance, .dark, "the choice survives a restart")
    }

    func testStaySignedInUncheckedOnTheSignInScreenIsAppliedAfterSignIn() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        XCTAssertTrue(app.settings.staySignedIn, "on by default")
        await app.settings.applySignInChoice()
        XCTAssertTrue(engine.calls("set_stay_signed_in").isEmpty)
        app.settings.staySignedIn = false
        await app.settings.applySignInChoice()
        XCTAssertEqual(engine.calls("set_stay_signed_in").last?.object["enabled"] as? Bool, false)
    }

    // MARK: Sign-in screen

    func testTheSignInScreenStaysOutUntilTheEngineHasAnswered() async throws {
        let engine = MockEngine()
        Fixtures.signedOut(engine)
        let app = AppModel(engine: engine)
        XCTAssertFalse(app.accountChecked, "no flash of the sign-in screen at launch")
        await app.refreshAccount()
        XCTAssertTrue(app.accountChecked)
        XCTAssertFalse(app.signedIn)
    }

    #if os(macOS)
    func testSignInScreenAndNewPagesRenderInLightAndDark() async throws {
        let engine = MockEngine()
        Fixtures.signedOut(engine)
        engine.reply("account_providers", json: #"["keycloak","azure","google","apple"]"#)
        let out = await Fixtures.app(engine)
        await out.loadSignInProviders()
        XCTAssertEqual(out.otherSignInProviders.sorted(), ["apple", "azure", "google"])
        let (reportApp, _) = await reportApp()
        for dark in [false, true] {
            let views: [(String, AnyView)] = [
                ("sign-in", AnyView(RootView(app: out))),
                ("reports", AnyView(ReportsView(app: reportApp))),
                ("settings", AnyView(SettingsView(app: reportApp))),
                ("settings-tall", AnyView(SettingsView(app: reportApp))),
            ]
            for (name, view) in views {
                let png = try Snapshot.render(view, size: CGSize(width: 1080, height: name == "settings-tall" ? 1900 : 720), dark: dark)
                XCTAssertGreaterThan(png.count, 5_000, name)
                if let folder = ProcessInfo.processInfo.environment["SNAPSHOT_DIR"].map({ URL(fileURLWithPath: $0, isDirectory: true) }) {
                    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                    try png.write(to: folder.appendingPathComponent("redesign-\(name)-\(dark ? "dark" : "light").png"))
                }
            }
        }
    }
    #endif

    #if os(macOS)
    /// Devices with the administration data, the three dialogs and the session banners, in light and dark.
    func testDevicesDialogsAndSessionBannersRenderInLightAndDark() async throws {
        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("remote_admin_devices", json: adminJSON(version: ("2.0.18", "2.0.19")))
        let app = await Fixtures.app(engine)
        app.devices.admin = EngineDeviceAdminService(engine: engine)
        await app.devices.load()
        app.section = .devices
        let device = try XCTUnwrap(app.devices.allDevices.first { $0.connectionId == 731_204_118 })

        func session(_ endsIn: TimeInterval?, unattended: Bool = false) async -> AppModel {
            let e = MockEngine()
            Fixtures.viewing(e)
            var status = RemoteStatus(phase: "active", host: 482_913_557, session: DemoFixtures.session, consent: DemoFixtures.consent, peer: 731_204_118, received: 12,
                                      control: true, clipboard: true, files: true, recording: RecordingView(phase: "ready"), displays: 2,
                                      hostActions: ["privacy", "block", "pointer", "lock", "sas", "restart", "sound", "meet"], peerFeatures: ["join", "switch"], unattendedSession: unattended)
            if let endsIn {
                status.passwordSession = PasswordSessionView(endsAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(endsIn)))
            }
            e.reply("remote_status", status)
            e.queue(frame: VideoFrame.sample(width: 320, height: 200, sequence: 1))
            let a = await Fixtures.app(e)
            a.session.showSample(VideoFrame.sample(width: 1280, height: 800))
            a.workspace.refresh()
            a.workspace.selected = .session(731_204_118)
            return a
        }
        let password = await session(4 * 3600 / 4 * 1)
        let warning = await session(240)
        let urgent = await session(40)
        let unattended = await session(nil, unattended: true)

        for dark in [false, true] {
            let items: [(String, AnyView, CGSize)] = [
                ("devices-admin", AnyView(RootView(app: app)), CGSize(width: 1180, height: 720)),
                ("dialog-rename", AnyView(RenameDeviceDialog(model: app.devices, device: device, company: "Muster Treuhand AG")), CGSize(width: 420, height: 230)),
                ("dialog-notes", AnyView(DeviceNotesDialog(model: app.devices, device: device)), CGSize(width: 420, height: 320)),
                ("dialog-remove", AnyView(RemoveDeviceDialog(model: app.devices, device: device, inSession: true)), CGSize(width: 420, height: 270)),
                ("session-password", AnyView(RootView(app: password)), CGSize(width: 1180, height: 720)),
                ("session-password-5min", AnyView(RootView(app: warning)), CGSize(width: 1180, height: 720)),
                ("session-password-1min", AnyView(RootView(app: urgent)), CGSize(width: 1180, height: 720)),
                ("session-unattended", AnyView(RootView(app: unattended)), CGSize(width: 1180, height: 720)),
            ]
            for (name, view, size) in items {
                let png = try Snapshot.render(view, size: size, dark: dark)
                XCTAssertGreaterThan(png.count, 4_000, name)
                if let folder = ProcessInfo.processInfo.environment["SNAPSHOT_DIR"].map({ URL(fileURLWithPath: $0, isDirectory: true) }) {
                    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                    try png.write(to: folder.appendingPathComponent("redesign-\(name)-\(dark ? "dark" : "light").png"))
                }
            }
        }
    }
    #endif
}
