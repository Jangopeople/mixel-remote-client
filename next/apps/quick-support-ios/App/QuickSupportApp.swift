import QuickHost
import ReplayKit
import SwiftUI

@main
struct QuickSupportApp: App {
    var body: some Scene {
        WindowGroup { MainView() }
    }
}

/// Registers this iPhone with the technician's support code. The session itself runs in the
/// screen-broadcast extension, because only that keeps running while the customer uses other apps.
final class Registration: ObservableObject {
    @Published var remoteId: String = ""
    @Published var busy = false
    @Published var problem = ""

    init() { load() }

    private func load() {
        guard let json = Shared.text("enrollment"), let data = json.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = object["connection_id"] as? Int else { remoteId = ""; return }
        remoteId = Self.grouped(String(id))
    }
    private static func grouped(_ digits: String) -> String {
        stride(from: 0, to: digits.count, by: 3).map { start in
            let from = digits.index(digits.startIndex, offsetBy: start)
            return String(digits[from..<(digits.index(from, offsetBy: 3, limitedBy: digits.endIndex) ?? digits.endIndex)])
        }.joined(separator: " ")
    }

    func connect(code: String) {
        guard !busy else { return }
        guard code.filter({ $0.isLetter || $0.isNumber }).count == 12 else { problem = Texts.of("invalid"); return }
        busy = true; problem = ""
        DispatchQueue.global(qos: .userInitiated).async {
            let seed = Shared.data("seed")
            let host: OpaquePointer? = seed.map { stored in stored.withUnsafeBytes { mqs_create($0.bindMemory(to: UInt8.self).baseAddress, { _, _ in }, nil) } } ?? mqs_create(nil, { _, _ in }, nil)
            var outcome = Texts.of("failed")
            if let host {
                if seed == nil {
                    var fresh = [UInt8](repeating: 0, count: 32)
                    mqs_seed(host, &fresh)
                    Shared.set("seed", Data(fresh))
                }
                let name = UIDevice.current.userInterfaceIdiom == .pad ? "iPad" : "iPhone"
                if let answer = mqs_enroll(host, code, name) {
                    let json = String(cString: answer)
                    mqs_free(answer)
                    if let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] {
                        // The host core reports in English; show the reason in the phone's language.
                        if let reason = object["error"] as? String { outcome = Texts.of(reason.contains("expired") ? "invalid" : reason.contains("reached") ? "unreachable" : "failed") } else if object["connection_id"] != nil { Shared.set("enrollment", json); outcome = "" }
                    }
                }
                mqs_destroy(host)
            }
            DispatchQueue.main.async {
                self.busy = false; self.problem = outcome; self.load()
            }
        }
    }
    func forget() {
        Shared.set("enrollment", nil as String?)
        load()
    }
}

/// Apple's own button that opens the "Start Broadcast" sheet, set to this app's extension.
struct BroadcastButton: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 280, height: 52))
        picker.preferredExtension = "ch.mixel.quicksupport.broadcast"
        picker.showsMicrophoneButton = false
        // Make Apple's small round button fill the whole area, so the label below it is the button.
        for case let button as UIButton in picker.subviews {
            button.frame = picker.bounds
            button.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            button.setImage(nil, for: .normal)
            button.setTitle(Texts.of("share"), for: .normal)
            button.titleLabel?.font = .systemFont(ofSize: 17, weight: .semibold)
            button.setTitleColor(.white, for: .normal)
            button.backgroundColor = UIColor(red: 0.70, green: 0.086, blue: 0.184, alpha: 1)
            button.layer.cornerRadius = 10
        }
        return picker
    }
    func updateUIView(_ view: RPSystemBroadcastPickerView, context: Context) {}
}

struct MainView: View {
    @StateObject private var registration = Registration()
    @State private var code = ""
    private let brand = Color(red: 0.70, green: 0.086, blue: 0.184)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Mixel Quick Support").font(.title.bold())
                if registration.remoteId.isEmpty {
                    card {
                        Text(Texts.of("intro")).foregroundColor(.secondary)
                        TextField(Texts.of("code"), text: $code)
                            .textInputAutocapitalization(.characters).disableAutocorrection(true)
                            .font(.title2.monospaced()).padding(.vertical, 6)
                            .submitLabel(.go).onSubmit { registration.connect(code: code) }
                        Divider()
                        Button(action: { registration.connect(code: code) }) {
                            Text(registration.busy ? Texts.of("connecting") : Texts.of("connect")).font(.headline).frame(maxWidth: .infinity).padding(.vertical, 14)
                        }
                        .foregroundColor(.white).background(brand).cornerRadius(10).disabled(registration.busy)
                    }
                } else {
                    card {
                        // The technician's app shows this phone by itself once the code is accepted,
                        // so the next step is sharing; the number is only a fallback.
                        Text(Texts.of("ready")).font(.headline)
                        BroadcastButton().frame(height: 52)
                        Text(Texts.of("share_hint")).font(.footnote).foregroundColor(.secondary)
                        Divider()
                        Text(Texts.of("number")).font(.footnote).foregroundColor(.secondary)
                        Text(registration.remoteId).font(.system(size: 22, weight: .semibold).monospacedDigit()).kerning(1)
                        Button(Texts.of("forget")) { code = ""; registration.forget() }.font(.footnote).padding(.top, 4)
                    }
                }
                if !registration.problem.isEmpty { Text(registration.problem).foregroundColor(Color(UIColor { $0.userInterfaceStyle == .dark ? UIColor(red: 1.0, green: 0.42, blue: 0.47, alpha: 1) : UIColor(red: 0.70, green: 0.086, blue: 0.184, alpha: 1) })) }
                Text(Texts.of("privacy")).font(.footnote).foregroundColor(.secondary)
            }
            .padding(16)
        }
        .background(Color(UIColor.systemGroupedBackground).ignoresSafeArea())
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12, content: content)
            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(UIColor.secondarySystemGroupedBackground)).cornerRadius(14)
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(UIColor.separator)))
    }
}
