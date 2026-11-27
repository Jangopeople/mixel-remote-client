import QuickHost
import ReplayKit
import SwiftUI
import UIKit

@main
struct QuickSupportApp: App {
    var body: some Scene {
        WindowGroup { MainView() }
    }
}

/// This iPhone's fixed ID. The person reads it to the technician; the session itself runs in the
/// screen-broadcast extension, because only that keeps running while the person uses other apps.
final class Registration: ObservableObject {
    @Published var remoteId: String = ""
    @Published var problem = ""
    @Published var isLoading = false

    private var retryWorkItem: DispatchWorkItem?

    init() { load() }

    private static func grouped(_ digits: String) -> String {
        stride(from: 0, to: digits.count, by: 3).map { start in
            let from = digits.index(digits.startIndex, offsetBy: start)
            return String(digits[from..<(digits.index(from, offsetBy: 3, limitedBy: digits.endIndex) ?? digits.endIndex)])
        }.joined(separator: " ")
    }

    /// Gets (or creates) this device's key and asks Mixel for its ID. Retries until it has one.
    func load() {
        guard !isLoading else { return }
        retryWorkItem?.cancel()
        retryWorkItem = nil
        isLoading = true

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var outcome = ""
            var id = ""
            let seed = Shared.data("seed")
            let host: OpaquePointer? = seed.map { stored in stored.withUnsafeBytes { mqs_create($0.bindMemory(to: UInt8.self).baseAddress, { _, _ in }, nil) } } ?? mqs_create(nil, { _, _ in }, nil)
            if let host {
                if seed == nil {
                    var fresh = [UInt8](repeating: 0, count: 32)
                    mqs_seed(host, &fresh)
                    Shared.set("seed", Data(fresh))
                }
                if let answer = mqs_register(host, Shared.deviceName) {
                    let json = String(cString: answer)
                    mqs_free(answer)
                    if let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] {
                        if let number = object["connection_id"] as? Int { id = Self.grouped(String(number)) }
                        else if let reason = object["error"] as? String { outcome = Texts.of(reason.contains("reached") ? "unreachable" : "failed") }
                    }
                }
                mqs_destroy(host)
            } else { outcome = Texts.of("failed") }
            DispatchQueue.main.async {
                guard let self else { return }
                self.isLoading = false
                if !id.isEmpty { self.remoteId = id }
                self.problem = id.isEmpty ? outcome : ""
                if id.isEmpty {
                    let work = DispatchWorkItem { [weak self] in self?.load() }
                    self.retryWorkItem = work
                    DispatchQueue.main.asyncAfter(deadline: .now() + 4, execute: work)
                }
            }
        }
    }
}

/// Apple's own button that opens the "Start Broadcast" sheet, set to this app's extension.
struct BroadcastButton: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 280, height: 52))
        picker.preferredExtension = "ch.mixel.quicksupport.broadcast"
        picker.showsMicrophoneButton = false
        for case let button as UIButton in picker.subviews {
            button.frame = picker.bounds
            button.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            button.setImage(nil, for: .normal)
            button.setTitle(Texts.of("share"), for: .normal)
            button.titleLabel?.font = .systemFont(ofSize: 17, weight: .semibold)
            button.setTitleColor(.white, for: .normal)
            button.backgroundColor = UIColor(red: 0.70, green: 0.086, blue: 0.184, alpha: 1)
            button.layer.cornerRadius = 12
        }
        return picker
    }
    func updateUIView(_ view: RPSystemBroadcastPickerView, context: Context) {
        for case let button as UIButton in view.subviews {
            button.frame = view.bounds
        }
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

struct MainView: View {
    @StateObject private var registration = Registration()
    @Environment(\.scenePhase) private var scenePhase
    @State private var copied = false
    @State private var sharingId = false
    @State private var isScreenCaptured = UIScreen.main.isCaptured

    private let accent = Color(red: 0.70, green: 0.086, blue: 0.184)
    private let errorColor = Color(UIColor { $0.userInterfaceStyle == .dark ? UIColor(red: 1.0, green: 0.42, blue: 0.47, alpha: 1) : UIColor(red: 0.70, green: 0.086, blue: 0.184, alpha: 1) })

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .center) {
                    Text("Mixel Quick Support").font(.title.bold())
                    Spacer()
                    if isScreenCaptured {
                        HStack(spacing: 6) {
                            Circle().fill(Color.red).frame(width: 8, height: 8)
                            Text(Texts.of("sharing_active"))
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.red)
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.red.opacity(0.14), in: Capsule())
                    }
                }

                card {
                    Text(Texts.of("your_id")).font(.headline).foregroundColor(.secondary)
                    if registration.remoteId.isEmpty {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text(Texts.of("getting_id"))
                                .font(.title3.weight(.semibold))
                                .foregroundColor(.secondary)
                        }
                        .padding(.vertical, 8)
                    } else {
                        Text(registration.remoteId)
                            .font(.system(size: 44, weight: .bold).monospacedDigit())
                            .kerning(2)
                            .textSelection(.enabled)
                        HStack(spacing: 10) {
                            Button {
                                UIPasteboard.general.string = registration.remoteId.replacingOccurrences(of: " ", with: "")
                                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                                copied = true
                                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { copied = false }
                            } label: {
                                Label(copied ? Texts.of("copied") : Texts.of("copy_id"), systemImage: copied ? "checkmark" : "doc.on.doc")
                                    .font(.subheadline.weight(.semibold))
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                            .tint(copied ? .green : accent)

                            Button {
                                sharingId = true
                            } label: {
                                Label(Texts.of("share_id"), systemImage: "square.and.arrow.up")
                                    .font(.subheadline.weight(.semibold))
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                            .tint(accent)
                        }
                    }

                    Text(Texts.of("tell_id")).foregroundColor(.secondary)
                    BroadcastButton()
                        .frame(maxWidth: .infinity)
                        .frame(height: 52)
                        .opacity(registration.remoteId.isEmpty ? 0.55 : 1.0)
                        .allowsHitTesting(!registration.remoteId.isEmpty)
                    Text(Texts.of("share_hint")).font(.footnote).foregroundColor(.secondary)
                }

                if !registration.problem.isEmpty {
                    card {
                        HStack(spacing: 10) {
                            Image(systemName: "exclamationmark.triangle.fill").foregroundColor(errorColor)
                            Text(registration.problem).foregroundColor(errorColor).font(.subheadline)
                        }
                        Button {
                            registration.load()
                        } label: {
                            Label(Texts.of("retry"), systemImage: "arrow.clockwise")
                                .font(.subheadline.weight(.semibold))
                        }
                        .buttonStyle(.bordered)
                        .tint(accent)
                    }
                }

                Text(Texts.of("privacy")).font(.footnote).foregroundColor(.secondary)
            }
            .padding(16)
        }
        .background(Color(UIColor.systemGroupedBackground).ignoresSafeArea())
        .sheet(isPresented: $sharingId) {
            ShareSheet(items: ["Mixel Quick Support ID: \(registration.remoteId)"])
        }
        .onReceive(NotificationCenter.default.publisher(for: UIScreen.capturedDidChangeNotification)) { _ in
            isScreenCaptured = UIScreen.main.isCaptured
        }
        .onChange(of: scenePhase) { phase in
            isScreenCaptured = UIScreen.main.isCaptured
            if phase == .active, registration.remoteId.isEmpty { registration.load() }
        }
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12, content: content)
            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(UIColor.secondarySystemGroupedBackground)).cornerRadius(14)
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(UIColor.separator)))
    }
}
