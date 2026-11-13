import CoreGraphics
import CoreText
import Foundation

/// One session of the company's session report (SQL 029).
public struct SessionRow: Codable, Equatable, Sendable, Identifiable {
    public var sessionId: String
    public var technicianId: String?
    public var technicianName: String?
    public var technicianEmail: String?
    public var deviceId: String
    public var deviceName: String
    public var connectionId: UInt32
    public var startedAt: String
    public var endedAt: String?
    public var durationSeconds: Int
    /// `attended` or `unattended`.
    public var kind: String
    public var colleague: Bool
    public var note: String?
    public var id: String { sessionId }
}

/// Session reports of a company (plans with reports), as in the desktop app and the portal: period,
/// technician and device filters, a note per session, CSV and PDF export; and after a controlled
/// session with one of the company's devices the optional note for it.
@MainActor
public final class ReportsModel: ObservableObject {
    let app: AppModel
    @Published public var from: Date
    @Published public var to: Date
    @Published public var technician: String?
    @Published public var device: String?
    @Published public private(set) var rows: [SessionRow] = []
    @Published public private(set) var loading = false
    @Published public private(set) var needsPlan = false
    @Published public var error = ""
    /// A controlled session with a company device just ended: ask for a note (Remote ID).
    @Published public var noteFor: UInt32?
    private var current: (session: String, peer: UInt32)?

    init(app: AppModel) {
        self.app = app
        let now = Date()
        to = now
        from = Calendar.current.date(byAdding: .day, value: -30, to: now) ?? now
    }

    public var tenant: Tenant? { app.company.tenant }
    /// The company's plan includes reports.
    public var included: Bool { app.company.overview?.features?["reports"] ?? false }
    public var manager: Bool { ["owner", "admin"].contains(app.company.role) }

    /// Owners and admins edit any note; the others the notes of their own sessions.
    public func canEditNote(_ row: SessionRow) -> Bool {
        if manager { return true }
        guard let mine = app.account.account?.profile, let technician = row.technicianId else { return true }
        return technician == mine
    }

    static func day(_ date: Date) -> String {
        let parts = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 1, parts.day ?? 1)
    }

    struct ReportArgs: Encodable { var tenant: String; var from: String?; var to: String?; var technician: String?; var device: String? }

    public func load() async {
        guard let tenant else { rows = []; return }
        loading = true
        error = ""
        defer { loading = false }
        // The end of the period includes that whole day.
        let end = Calendar.current.date(byAdding: .day, value: 1, to: to) ?? to
        do {
            rows = try await app.engine.run("saas_session_report", ReportArgs(tenant: tenant.tenantId, from: Self.day(from), to: Self.day(end), technician: technician, device: device))
            needsPlan = false
        } catch {
            let message = (error as? EngineFailure)?.message ?? ""
            if message.range(of: "session reports need a paid plan", options: .caseInsensitive) != nil { needsPlan = true; rows = [] }
            else { self.error = AppModel.message(error) }
        }
    }

    public func setNote(_ row: SessionRow, _ text: String) async -> Bool {
        guard let tenant else { return false }
        do {
            try await app.engine.perform("saas_session_note", ["tenant": tenant.tenantId, "note": text, "session": row.sessionId])
            await load()
            return true
        } catch {
            self.error = AppModel.message(error)
            return false
        }
    }

    /// The note after a session (the session is found by its Remote ID on the server).
    public func saveNote(_ text: String, for host: UInt32) async -> Bool {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let tenant, !text.isEmpty else { return true }
        do {
            try await app.engine.perform("saas_session_note", ["tenant": tenant.tenantId, "note": text, "connection": host])
            return true
        } catch {
            self.error = AppModel.message(error)
            return false
        }
    }

    /// After a controlled session with a company device has ended, ask for a note (plans with reports).
    func statusChanged(from previous: RemoteStatus, to next: RemoteStatus) {
        let controlling = next.phase == "active" && !next.sharing && next.session != nil && next.peer != nil
        if let active = current, next.phase == "closing" || (controlling && next.session == active.session) { return }
        let ended = current
        current = controlling ? (next.session!, next.peer!) : nil
        guard let ended, ended.session != current?.session else { return }
        // A reconnect to the same computer is the same piece of work: ask when that one ends.
        if controlling, next.peer == ended.peer { return }
        guard tenant != nil, included, app.devices.devices.contains(where: { $0.connectionId == ended.peer }) || app.devices.isCompanyHost(ended.peer) else { return }
        let host = ended.peer
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            if self.noteFor == nil { self.noteFor = host }
        }
    }

    // MARK: Export

    public static func duration(_ seconds: Int) -> String {
        let hours = seconds / 3600, minutes = (seconds % 3600) / 60, rest = seconds % 60
        return hours > 0 ? String(format: "%d:%02d:%02d", hours, minutes, rest) : String(format: "%d:%02d", minutes, rest)
    }

    public static func stamp(_ iso: String?) -> String {
        guard let iso, !iso.isEmpty else { return "" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: iso) ?? ISO8601DateFormatter().date(from: iso)
        guard let date else { return iso }
        let format = DateFormatter()
        format.dateFormat = "yyyy-MM-dd HH:mm"
        return format.string(from: date)
    }

    public func kindLabel(_ row: SessionRow) -> String {
        (row.kind == "unattended" ? L("Unattended") : L("Attended")) + (row.colleague ? " · " + L("Colleague") : "")
    }

    var columns: [String] { [L("Start"), L("End"), L("Duration"), L("Technician"), L("Device"), L("Remote ID"), L("Kind"), L("Note")] }

    func cells(_ row: SessionRow) -> [String] {
        [Self.stamp(row.startedAt), Self.stamp(row.endedAt), Self.duration(row.durationSeconds), row.technicianName ?? row.technicianEmail ?? "",
         row.deviceName, Format.remoteId(row.connectionId), kindLabel(row), row.note ?? ""]
    }

    /// CSV (UTF-8 with BOM for spreadsheet apps, CRLF lines); cells that could start a formula are quoted with '.
    public func csv() -> String {
        func cell(_ value: String) -> String {
            var text = value
            if let first = text.first, "=+-@".contains(first) { text = "'" + text }
            return text.contains(where: { ",\"\n\r".contains($0) }) ? "\"" + text.replacingOccurrences(of: "\"", with: "\"\"") + "\"" : text
        }
        let lines = [columns.map(cell).joined(separator: ",")] + rows.map { cells($0).map(cell).joined(separator: ",") }
        return "\u{FEFF}" + lines.joined(separator: "\r\n") + "\r\n"
    }

    public var fileStem: String {
        let slug = (tenant?.slug ?? "mixel-remote").filter { $0.isLetter || $0.isNumber || $0 == "-" }
        return "sessions-\(slug.isEmpty ? "mixel-remote" : slug)-\(Self.day(from))-\(Self.day(to))"
    }

    /// A landscape A4 PDF of the report (title, period, totals, the table; paginated).
    public func pdf() -> Data {
        let data = NSMutableData()
        var box = CGRect(x: 0, y: 0, width: 842, height: 595)
        guard let consumer = CGDataConsumer(data: data as CFMutableData),
              let context = CGContext(consumer: consumer, mediaBox: &box, [kCGPDFContextTitle: L("Session reports")] as CFDictionary)
        else { return Data() }
        let margin: CGFloat = 36, rowHeight: CGFloat = 16
        let widths: [CGFloat] = [88, 88, 54, 120, 130, 78, 90, 122]
        func text(_ string: String, _ x: CGFloat, _ y: CGFloat, width: CGFloat, size: CGFloat = 8, bold: Bool = false) {
            let font = CTFontCreateWithName((bold ? "Helvetica-Bold" : "Helvetica") as CFString, size, nil)
            var shown = string.replacingOccurrences(of: "\n", with: " ")
            func line(_ value: String) -> CTLine { CTLineCreateWithAttributedString(NSAttributedString(string: value, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font])) }
            var built = line(shown)
            while CTLineGetTypographicBounds(built, nil, nil, nil) > Double(width - 4), shown.count > 1 {
                shown = String(shown.dropLast(2)) + "…"
                built = line(shown)
            }
            context.textPosition = CGPoint(x: x, y: y)
            CTLineDraw(built, context)
        }
        let total = rows.reduce(0) { $0 + $1.durationSeconds }
        var index = 0
        repeat {
            context.beginPDFPage(nil)
            var y = box.height - margin - 14
            text(L("Session reports") + " · " + (tenant?.name ?? ""), margin, y, width: 700, size: 14, bold: true)
            y -= 16
            text("\(Self.day(from)) – \(Self.day(to)) · \(L("%d sessions", rows.count)) · \(Self.duration(total))", margin, y, width: 700, size: 9)
            y -= 22
            var x = margin
            for (column, width) in zip(columns, widths) { text(column, x, y, width: width, bold: true); x += width }
            y -= rowHeight
            while index < rows.count, y > margin {
                x = margin
                for (value, width) in zip(cells(rows[index]), widths) { text(value, x, y, width: width); x += width }
                y -= rowHeight
                index += 1
            }
            context.endPDFPage()
        } while index < rows.count
        context.closePDF()
        return data as Data
    }
}
