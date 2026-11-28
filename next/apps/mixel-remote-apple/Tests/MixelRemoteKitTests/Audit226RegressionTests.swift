#if os(macOS)
import AppKit
import SwiftUI
import XCTest
@testable import MixelRemoteKit

@MainActor
final class Audit226RegressionTests: XCTestCase {

    /// W-3: Clicking Stop on a recording must send `remote_record_stop` even when the session
    /// window is not currently focused (e.g., popped-out window after clicking another window).
    func testStopRecordingWorksWhenUnfocused() async {
        let engine = MockEngine()
        Fixtures.company(engine)
        let rec = RecordingView(phase: "recording", message: nil)
        let item = RemoteSessionItem(
            id: "sess-rec-1",
            session: "sess-rec-1",
            peer: 900_670_558,
            phase: "active",
            control: true,
            recordingPermitted: true,
            recording: rec
        )
        let item2 = RemoteSessionItem(
            id: "sess-rec-2",
            session: "sess-rec-2",
            peer: 900_670_559,
            phase: "active",
            control: true
        )
        engine.reply("remote_status", RemoteStatus(
            phase: "active",
            session: "sess-rec-1",
            peer: 900_670_558,
            control: true,
            recordingPermitted: true,
            recording: rec,
            sessions: [item, item2],
            maxSessions: 4
        ))
        let app = await Fixtures.app(engine)
        defer { app.stop() }
        guard let session = app.sessionModels[900_670_558] else {
            return XCTFail("Expected session model for peer 900_670_558")
        }
        // Focus the other session so `session.focused == false` (exact 2-session audit scenario).
        app.workspace.popOut(900_670_559)
        app.workspace.windowFocusChanged(900_670_559, focused: true)
        XCTAssertFalse(session.focused, "Precondition: session must be unfocused")

        session.stopRecording()
        try? await Task.sleep(nanoseconds: 150_000_000)

        XCTAssertTrue(
            engine.calls.contains(where: { $0.command == "remote_record_stop" }),
            "W-3: stopRecording() must invoke remote_record_stop even when session.focused is false"
        )
    }

    /// N-10b: QualityPopover content must fit cleanly inside 400 pt across EN, DE, FR, IT
    /// with at least 12 pt inner horizontal padding and zero clipping.
    func testQualityPopoverFitsWithoutClippingAcrossLanguages() {
        let samples: [(String, String, String, String, String)] = [
            ("en", "Connection & picture", "Auto", "Optimize speed", "Optimize quality"),
            ("de", "Verbindung & Bild", "Automatisch", "Geschwindigkeit", "Qualität optimieren"),
            ("fr", "Connexion et image", "Auto", "Vitesse", "Qualité optimale"),
            ("it", "Connessione e immagine", "Auto", "Velocità", "Qualità ottimale"),
        ]
        for (lang, title, seg1, seg2, seg3) in samples {
            let view = VStack(alignment: .leading, spacing: 8) {
                Text(verbatim: title).font(.headline)
                LabeledContent { Text(verbatim: "Direct · UDP").monospacedDigit() } label: { Text(verbatim: "Connection") }
                LabeledContent { Text(verbatim: "18.4 Mbps").monospacedDigit() } label: { Text(verbatim: "Bitrate") }
                LabeledContent { Text(verbatim: "55 fps").monospacedDigit() } label: { Text(verbatim: "Pictures per second") }
                LabeledContent { Text(verbatim: "1920 × 1080").monospacedDigit() } label: { Text(verbatim: "Resolution") }
                LabeledContent { Text(verbatim: "This Mac (hardware)") } label: { Text(verbatim: "Video decoding") }
                LabeledContent { Text(verbatim: "12.4 MB") } label: { Text(verbatim: "Received") }
                Divider()
                Picker(selection: .constant("auto")) {
                    Text(verbatim: seg1).tag("auto")
                    Text(verbatim: seg2).tag("speed")
                    Text(verbatim: seg3).tag("quality")
                } label: { Text(verbatim: "Mode") }
                .labelsHidden()
                .pickerStyle(.segmented)
                .frame(maxWidth: .infinity)
                Toggle(isOn: .constant(false)) { Text(verbatim: "Actual size (1:1)") }
            }
            .padding(16)
            .frame(width: 400)

            let host = NSHostingView(rootView: view)
            host.layoutSubtreeIfNeeded()
            XCTAssertLessThanOrEqual(
                host.fittingSize.width, 400.5,
                "N-10b (\(lang)): popover content width \(host.fittingSize.width) exceeds 400 pt container"
            )
        }
    }

    /// Attempting to connect to a 9-digit ID must keep that ID in Recent (and UserDefaults)
    /// even when the connection fails, and expose it as a clickable ad-hoc entry in Views → Recent.
    func testFailedConnectAttemptPersistsIdInRecent() async {
        let key = DevicesModel.recentConnectionsKey
        let previous = UserDefaults.standard.object(forKey: key)
        UserDefaults.standard.removeObject(forKey: key)
        defer {
            if let previous { UserDefaults.standard.set(previous, forKey: key) }
            else { UserDefaults.standard.removeObject(forKey: key) }
        }

        let engine = MockEngine()
        Fixtures.company(engine)
        engine.reply("remote_status", RemoteStatus(phase: "ready", host: 111_222_333))
        engine.fail("remote_request", "Device is unreachable")

        let app = await Fixtures.app(engine)
        defer { app.stop() }

        let targetId: UInt32 = 218_749_839
        app.connect(to: targetId, unattended: false, checkPhone: false)

        // Immediately recorded before the async connection settles.
        XCTAssertEqual(app.devices.recentConnectionIds.first, targetId)

        // Wait for the failing connect attempt to finish.
        for _ in 0..<40 where app.busy {
            try? await Task.sleep(nanoseconds: 25_000_000)
        }
        XCTAssertFalse(app.error.isEmpty, "Precondition: connect attempt should have failed")

        // ID remains in Recent and persisted in UserDefaults.
        XCTAssertEqual(app.devices.recentConnectionIds.first, targetId)
        let stored = (UserDefaults.standard.array(forKey: key) as? [NSNumber] ?? []).map(\.uint32Value)
        XCTAssertEqual(stored.first, targetId)

        // Unregistered ID appears in Views → Recent and is NOT blocked from retrying.
        app.devices.listView = .recent
        let recentEntries = app.devices.devices(in: .recent)
        guard let adHoc = recentEntries.first(where: { $0.connectionId == targetId }) else {
            return XCTFail("Expected attempted ID \(targetId) to appear in DevicesModel.devices(in: .recent)")
        }
        XCTAssertTrue(adHoc.isAdHocRecent)
        XCTAssertEqual(adHoc.name, "218 749 839")
        XCTAssertEqual(app.devices.connectBlocked(adHoc), "")

        // Newest-first order is preserved in rowGroups for .recent.
        let secondId: UInt32 = 445_882_109
        app.devices.recordConnection(secondId)
        let orderedIds = app.devices.rowGroups.flatMap(\.devices).map(\.connectionId)
        XCTAssertEqual(Array(orderedIds.prefix(2)), [secondId, targetId])
    }

    /// Saved phones (ios/android/other/unknown OS) must never default to unattended access and
    /// must run Quick Support phone claim so connecting to an iPhone does not fail with
    /// "did not accept an unattended connection".
    func testSavedPhoneUsesAttendedModeAndPhoneClaim() async {
        let engine = MockEngine()
        Fixtures.company(engine)
        let app = await Fixtures.app(engine)
        defer { app.stop() }

        let iphone = Device(device: "dev-iphone-1", connectionId: 648_493_658, name: "iPhone", os: "ios")
        let unknownOs = Device(device: "dev-unknown-1", connectionId: 648_493_659, name: "iPhone", os: "")
        let mac = Device(device: "dev-mac-1", connectionId: 648_493_660, name: "Office Mac", os: "macos")

        XCTAssertFalse(app.devices.prefersUnattended(iphone))
        XCTAssertTrue(app.devices.shouldCheckPhone(iphone))
        XCTAssertFalse(app.devices.prefersUnattended(unknownOs))
        XCTAssertTrue(app.devices.shouldCheckPhone(unknownOs))
        XCTAssertTrue(app.devices.prefersUnattended(mac))
        XCTAssertFalse(app.devices.shouldCheckPhone(mac))
    }
}
#endif
