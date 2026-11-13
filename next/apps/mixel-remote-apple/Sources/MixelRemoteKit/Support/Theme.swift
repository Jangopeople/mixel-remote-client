import SwiftUI

/// Mixel's colours: the Mixel red as accent, calm neutral surfaces, green for "online". Every colour
/// has a light and a dark value; text always uses the system label colours on system backgrounds, so
/// nothing ends up white on white.
public enum Theme {
    public static let brand = Color(light: 0xBA2034, dark: 0xE2485B)
    public static let brandSoft = Color(light: 0xFCEEF0, dark: 0x3A1A20)
    public static let success = Color(light: 0x258456, dark: 0x3DBA7C)
    public static let warning = Color(light: 0xA86400, dark: 0xF0A43A)
    public static let muted = Color.secondary
    #if os(macOS)
    public static let surface = Color(nsColor: .controlBackgroundColor)
    public static let background = Color(nsColor: .windowBackgroundColor)
    public static let line = Color(nsColor: .separatorColor)
    #else
    public static let surface = Color(uiColor: .secondarySystemGroupedBackground)
    public static let background = Color(uiColor: .systemGroupedBackground)
    public static let line = Color(uiColor: .separator)
    #endif
    public static let corner: CGFloat = 12
}

public extension Color {
    /// A colour with its own value in light and in dark mode.
    init(light: UInt32, dark: UInt32) {
        #if os(macOS)
        self.init(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        })
        #else
        self.init(uiColor: UIColor { traits in
            UIColor(hex: traits.userInterfaceStyle == .dark ? dark : light)
        })
        #endif
    }
}

#if os(macOS)
extension NSColor {
    convenience init(hex: UInt32) {
        self.init(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255, blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}
#else
extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255, blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}
#endif

/// A rounded panel on the window background (Mixel's card).
public struct Card<Content: View>: View {
    let content: Content
    public init(@ViewBuilder content: () -> Content) { self.content = content() }
    public var body: some View {
        VStack(alignment: .leading, spacing: 12) { content }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.corner, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).stroke(Theme.line.opacity(0.6), lineWidth: 0.5))
    }
}

/// The title of a card with an optional SF Symbol.
public struct CardTitle: View {
    let text: String
    let symbol: String?
    public init(_ text: String, symbol: String? = nil) {
        self.text = text
        self.symbol = symbol
    }
    public var body: some View {
        Label {
            Text(verbatim: text).font(.headline)
        } icon: {
            if let symbol { Image(systemName: symbol).foregroundStyle(Theme.brand) }
        }
        .accessibilityAddTraits(.isHeader)
    }
}

/// A coloured dot with a word, for online/offline and similar states.
public struct StatusBadge: View {
    public enum Kind { case online, offline, unknown, busy, attention }
    let kind: Kind
    let text: String
    public init(_ kind: Kind, _ text: String) {
        self.kind = kind
        self.text = text
    }
    var color: Color {
        switch kind {
        case .online: Theme.success
        case .offline: Color.secondary
        case .unknown: Color.secondary.opacity(0.6)
        case .busy: Theme.warning
        case .attention: Theme.brand
        }
    }
    public var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(verbatim: text).font(.callout).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The Mixel button style: filled Mixel red for the main action of a screen.
public struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    public init() {}
    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .foregroundStyle(.white)
            .background(Theme.brand.opacity(enabled ? (configuration.isPressed ? 0.8 : 1) : 0.4), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(Rectangle())
    }
}

/// An error or notice line.
public struct MessageLine: View {
    let text: String
    let isError: Bool
    public init(_ text: String, error: Bool = true) {
        self.text = text
        self.isError = error
    }
    public var body: some View {
        if !text.isEmpty {
            Label {
                Text(verbatim: text).fixedSize(horizontal: false, vertical: true)
            } icon: {
                Image(systemName: isError ? "exclamationmark.triangle.fill" : "info.circle.fill")
            }
            .font(.callout)
            .foregroundStyle(isError ? Theme.brand : .secondary)
            .accessibilityElement(children: .combine)
        }
    }
}
