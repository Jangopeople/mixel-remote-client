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
}
#endif
