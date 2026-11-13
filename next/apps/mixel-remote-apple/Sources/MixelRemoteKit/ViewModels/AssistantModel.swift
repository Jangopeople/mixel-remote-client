import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Mixel team tools during a session, as in the desktop app: the company's own AI server explains
/// the remote screen and writes the ticket note, and the note is saved in the ISM ticket.
@MainActor
public final class AssistantModel: ObservableObject {
    let app: AppModel
    @Published public var address = ""
    @Published public var model = ""
    @Published public private(set) var models: [String] = []
    @Published public private(set) var connected = false
    @Published public var status = ""
    @Published public var question = ""
    @Published public var answer = ""
    @Published public private(set) var busy = false
    @Published public var ticket = ""
    @Published public var ticketStatus = ""
    private var log: [String] = []
    private var started = Date()
    private var sessionId: String?

    /// The desktop app's setting (`{"address","model"}`), kept under the same name.
    static let key = "mixel.remote.ai.v1"

    struct Server: Codable { var address: String; var models: [String]; var model: String }

    init(app: AppModel) {
        self.app = app
        if let data = UserDefaults.standard.string(forKey: Self.key)?.data(using: .utf8),
           let saved = try? JSONSerialization.jsonObject(with: data) as? [String: String] {
            address = saved["address"] ?? ""
            model = saved["model"] ?? ""
        }
    }

    /// Mixel team accounts only (the engine and ISM refuse everyone else as well).
    public var available: Bool { app.account.account?.staff == true }

    func sessionChanged(_ session: String?) {
        guard session != sessionId else { return }
        sessionId = session
        log = []
        started = Date()
        answer = ""
        ticketStatus = ""
    }

    public func connect(announce: Bool = true) {
        if announce { status = L("Connecting…") }
        Task {
            do {
                let server: Server = try await app.engine.run("ai_connect", ["address": address.trimmingCharacters(in: .whitespaces), "model": model])
                address = server.address
                models = server.models
                model = server.model
                connected = true
                if let data = try? JSONSerialization.data(withJSONObject: ["address": server.address, "model": server.model]) {
                    UserDefaults.standard.set(String(decoding: data, as: UTF8.self), forKey: Self.key)
                }
                if announce { status = L("Connected to your AI server.") }
            } catch {
                connected = false
                if announce { status = AppModel.message(error) }
            }
        }
    }

    var language: String {
        ["de": "German", "fr": "French", "it": "Italian"][Translator.shared.language] ?? "English"
    }

    /// The remote screen as JPEG (at most 1280 wide), base64, for the AI server.
    static func screenshot(_ image: CGImage?) -> String? {
        guard let image, image.width >= 16 else { return nil }
        let scale = min(1, 1280 / CGFloat(image.width))
        let width = Int(CGFloat(image.width) * scale), height = Int(CGFloat(image.height) * scale)
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
        else { return nil }
        context.interpolationQuality = .medium
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        guard let scaled = context.makeImage() else { return nil }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(destination, scaled, [kCGImageDestinationLossyCompressionQuality: 0.72] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return (data as Data).base64EncodedString()
    }

    public enum Kind { case ask, note }

    public func run(_ kind: Kind) {
        guard connected, !busy else { return }
        busy = true
        let typed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        answer = kind == .ask ? L("Looking at the screen…") : L("Writing the note…")
        let image = Self.screenshot(app.session.frame)
        let system: String, prompt: String
        switch kind {
        case .ask:
            system = "You are an IT support assistant helping a technician during a remote support session. The image is the remote computer's screen (a Mixel Remote window on it only means the session is running; ignore it). Be concrete and brief. No Markdown. Quote on-screen text exactly when you rely on it; say so if you cannot read something. Answer in \(language)."
            prompt = typed.isEmpty ? "Explain briefly what is shown on this screen, name any error or warning you can read, and suggest the next two or three steps for the technician." : typed
        case .note:
            let minutes = max(1, Int(Date().timeIntervalSince(started) / 60))
            system = "You write short, factual service-desk notes for an IT provider. Use only the facts given and what the final screenshot shows; never invent work. Format: one summary line, then three short sections for what was done, what is still open, and the time spent, with the section labels in the answer language. Write \"–\" for an empty section. No Markdown. Answer in \(language)."
            prompt = "Remote support session with computer ID \(app.remote.peer.map(String.init) ?? "unknown"), about \(minutes) minutes so far.\nTechnician's keywords: \(typed.isEmpty ? "(none)" : typed)\nAssistant exchanges during the session:\n\(log.isEmpty ? "(none)" : log.joined(separator: "\n---\n"))\nWrite the ticket note."
        }
        Task {
            defer { busy = false }
            do {
                var text: String = try await app.engine.run("ai_ask", ["address": address, "model": model, "system": system, "prompt": prompt, "image": image])
                text = Self.plain(text)
                answer = text
                if kind == .ask {
                    log.append("Question: \(typed.isEmpty ? "(explain the screen)" : typed)\nAssistant: \(text)")
                    if log.count > 12 { log.removeFirst() }
                }
            } catch {
                answer = AppModel.message(error)
            }
        }
    }

    /// Plain text: no Markdown emphasis or heading marks.
    static func plain(_ text: String) -> String {
        text.replacingOccurrences(of: #"\*\*(.+?)\*\*"#, with: "$1", options: .regularExpression)
            .replacingOccurrences(of: #"(?m)^#{1,4}\s*"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"(?m)^\s*\*\s+"#, with: "• ", options: .regularExpression)
    }

    /// Saves the note (written by the AI or typed) as an internal note in the ISM ticket.
    public func saveTicket() {
        let number = ticket.trimmingCharacters(in: .whitespaces), note = answer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard number.count >= 3 else { ticketStatus = L("Enter the ticket number."); return }
        guard !note.isEmpty else { ticketStatus = L("Write the note first, or let the AI write it."); return }
        ticketStatus = L("Saving…")
        Task {
            do {
                let saved: String = try await app.engine.run("ticket_note", ["ticket": number, "note": String(note.prefix(7800)), "remote_id": app.remote.peer])
                ticketStatus = L("Saved as internal note in %@", saved)
            } catch {
                ticketStatus = AppModel.message(error)
            }
        }
    }
}
