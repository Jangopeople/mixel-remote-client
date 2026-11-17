import SwiftUI

/// The tokens of the redesign "Pro Console" (Mixel tokens, Mixel Remote red #BA2034). Every colour has
/// a light and a dark value; badge text on its soft background meets WCAG 2.2 AA (4.5:1) in both.
public extension Theme {
    /// The sidebar and the title bar.
    static let chrome = Color(light: 0xF4F4F6, dark: 0x1B1B1E)
    /// The table and page background.
    static let page = Color(light: 0xFFFFFF, dark: 0x141416)
    /// Group header rows of the device table.
    static let groupRow = Color(light: 0xF2F2F5, dark: 0x1F1F23)
    /// A row whose device is in a session with this app.
    static let activeRow = Color(light: 0xFBEFF1, dark: 0x2A171B)
    static let hoverRow = Color(light: 0xF7F7F9, dark: 0x1C1C20)
    static let hairline = Color(light: 0xE4E4E9, dark: 0x2C2C31)
    /// "Unattended": blue text on a soft blue.
    static let infoText = Color(light: 0x1D4ED8, dark: 0x8FB4FF)
    static let infoSoft = Color(light: 0xEAF1FF, dark: 0x16233D)
    /// "Update": amber text on a soft amber.
    static let updateText = Color(light: 0x8A4B00, dark: 0xF5C26B)
    static let updateSoft = Color(light: 0xFFF2D9, dark: 0x3A2A10)
    /// "In session": green text on a soft green.
    static let sessionText = Color(light: 0x1B6B3F, dark: 0x6FD69D)
    static let sessionSoft = Color(light: 0xE5F5EC, dark: 0x13301F)
    /// The session toolbar and the area around the remote picture.
    static let stage = Color(light: 0x111114, dark: 0x0B0B0D)

    /// A stable colour for a group's dot.
    static func groupColor(_ name: String) -> Color {
        let palette: [UInt32] = [0x5B5BD6, 0x12A594, 0xE5800E, 0xB14FD6, 0x2F80ED, 0xD6409F, 0x7A8B19]
        var hash: UInt32 = 5381
        for byte in name.utf8 { hash = (hash &* 33) &+ UInt32(byte) }
        return Color(light: palette[Int(hash % UInt32(palette.count))], dark: palette[Int(hash % UInt32(palette.count))])
    }
}

/// A small rounded badge ("Unattended", "Update", "In session").
public struct Pill: View {
    let text: String
    let symbol: String?
    let foreground: Color
    let background: Color
    public init(_ text: String, symbol: String? = nil, foreground: Color, background: Color) {
        self.text = text
        self.symbol = symbol
        self.foreground = foreground
        self.background = background
    }
    public var body: some View {
        HStack(spacing: 4) {
            if let symbol { Image(systemName: symbol).font(.caption2.weight(.semibold)) }
            Text(verbatim: text).font(.caption.weight(.semibold))
        }
        .foregroundStyle(foreground)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(background, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// The operating system's mark on a small tile, with the status dot in its corner.
public struct OSTile: View {
    let os: String
    let dot: Color?
    var size: CGFloat = 30
    public init(os: String, dot: Color?, size: CGFloat = 30) {
        self.os = os
        self.dot = dot
        self.size = size
    }
    public var body: some View {
        ZStack(alignment: .bottomTrailing) {
            Group {
                if os.lowercased() == "windows" {
                    WindowsMark().frame(width: size * 0.42, height: size * 0.42)
                } else {
                    Image(systemName: OSTile.symbol(os)).font(.system(size: size * 0.48, weight: .medium))
                }
            }
                .foregroundStyle(.primary)
                .frame(width: size, height: size)
                .background(Theme.page, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
            if let dot {
                Circle().fill(dot).frame(width: 8, height: 8)
                    .overlay(Circle().stroke(Theme.page, lineWidth: 1.5))
                    .offset(x: 2, y: 2)
            }
        }
        .accessibilityHidden(true)
    }

    /// The system's own mark where SF Symbols has one.
    public static func symbol(_ os: String) -> String {
        switch os.lowercased() {
        case "macos", "ios": "apple.logo"
        case "windows": "pc"
        case "linux": "terminal"
        case "android": "candybarphone"
        default: "display"
        }
    }
}

/// Four panes, as the Windows mark (SF Symbols has none).
struct WindowsMark: View {
    var body: some View {
        GeometryReader { geometry in
            let gap = max(1, geometry.size.width * 0.08)
            let side = (geometry.size.width - gap) / 2
            ZStack(alignment: .topLeading) {
                ForEach(0..<4, id: \.self) { index in
                    Rectangle()
                        .frame(width: side, height: side)
                        .offset(x: CGFloat(index % 2) * (side + gap), y: CGFloat(index / 2) * (side + gap))
                }
            }
        }
    }
}
