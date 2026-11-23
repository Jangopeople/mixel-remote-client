import Foundation

public enum Format {
    /// A Remote ID in groups of three: 123 456 789.
    public static func remoteId(_ id: UInt32?) -> String {
        guard let id else { return "— — —" }
        let digits = String(id)
        guard digits.count == 9 else { return digits }
        let characters = Array(digits)
        return "\(String(characters[0..<3])) \(String(characters[3..<6])) \(String(characters[6..<9]))"
    }

    /// A typed Remote ID: nine digits, the first not 0 (spaces allowed). `nil` otherwise.
    public static func parseRemoteId(_ text: String) -> UInt32? {
        let digits = text.filter { !$0.isWhitespace }
        guard digits.count == 9, digits.allSatisfy(\.isASCIIDigit), digits.first != "0" else { return nil }
        return UInt32(digits)
    }

    /// "Last seen 5 minutes ago" and similar, from an ISO 8601 time.
    public static func lastSeen(_ iso: String?, now: Date = Date()) -> String {
        guard let iso, let date = parseDate(iso) else { return "" }
        let minutes = max(0, Int((now.timeIntervalSince(date) / 60).rounded()))
        if minutes < 2 { return L("Last seen just now") }
        if minutes < 60 { return L("Last seen %d minutes ago", minutes) }
        let hours = Int((Double(minutes) / 60).rounded())
        if hours < 24 { return hours == 1 ? L("Last seen 1 hour ago") : L("Last seen %d hours ago", hours) }
        let days = Int((Double(hours) / 24).rounded())
        return days == 1 ? L("Last seen yesterday") : L("Last seen %d days ago", days)
    }

    private static let formatterLock = NSLock()
    private static let isoFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain = ISO8601DateFormatter()
    private static let mediumDay: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f
    }()

    public static func parseDate(_ iso: String) -> Date? {
        formatterLock.withLock {
            isoFractional.date(from: iso) ?? isoPlain.date(from: iso)
        }
    }

    public static func day(_ iso: String?) -> String {
        guard let iso, let date = parseDate(iso) else { return "" }
        let locale = Translator.shared.locale
        return formatterLock.withLock {
            if mediumDay.locale != locale { mediumDay.locale = locale }
            return mediumDay.string(from: date)
        }
    }

    public static func bytes(_ count: UInt64) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(clamping: count), countStyle: .file)
    }

    /// The operating system as people say it.
    public static func os(_ os: String) -> String {
        switch os.lowercased() {
        case "macos": "macOS"
        case "windows": "Windows"
        case "linux": "Linux"
        case "ios": "iPhone or iPad"
        case "android": "Android"
        case "other": L("phone or tablet")
        default: os
        }
    }

    public static func osSymbol(_ os: String) -> String {
        switch os.lowercased() {
        case "macos": "laptopcomputer"
        case "windows", "linux": "desktopcomputer"
        case "ios", "android", "other": "iphone"
        default: "display"
        }
    }

    /// The person's role in a company, translated.
    public static func role(_ role: String) -> String {
        switch role {
        case "owner": L("Owner")
        case "admin": L("Administrator")
        case "technician": L("Technician")
        case "member": L("Member")
        default: role
        }
    }

    public static func plan(_ plan: String) -> String {
        switch plan {
        case "personal": "Personal"
        case "solo": "Solo"
        case "team": "Team"
        case "business": "Business"
        case "enterprise": "Enterprise"
        default: plan.capitalized
        }
    }
}

extension Character {
    var isASCIIDigit: Bool { ("0"..."9").contains(self) }
}
