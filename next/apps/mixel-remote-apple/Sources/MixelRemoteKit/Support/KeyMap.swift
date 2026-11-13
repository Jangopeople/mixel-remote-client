import Foundation

/// Keys travel as USB HID usages (page 7), the same numbers the desktop app sends. The Mac reports
/// its own key codes (positions, independent of the layout); iPhone and iPad hardware keyboards
/// report HID usages already.
public enum KeyMap {
    /// macOS virtual key code → HID usage.
    public static let mac: [UInt16: UInt16] = [
        0x00: 4, 0x0B: 5, 0x08: 6, 0x02: 7, 0x0E: 8, 0x03: 9, 0x05: 10, 0x04: 11, 0x22: 12, 0x26: 13,
        0x28: 14, 0x25: 15, 0x2E: 16, 0x2D: 17, 0x1F: 18, 0x23: 19, 0x0C: 20, 0x0F: 21, 0x01: 22, 0x11: 23,
        0x20: 24, 0x09: 25, 0x0D: 26, 0x07: 27, 0x10: 28, 0x06: 29,
        0x12: 30, 0x13: 31, 0x14: 32, 0x15: 33, 0x17: 34, 0x16: 35, 0x1A: 36, 0x1C: 37, 0x19: 38, 0x1D: 39,
        0x24: 40, 0x35: 41, 0x33: 42, 0x30: 43, 0x31: 44, 0x1B: 45, 0x18: 46, 0x21: 47, 0x1E: 48, 0x2A: 49,
        0x29: 51, 0x27: 52, 0x32: 53, 0x2B: 54, 0x2F: 55, 0x2C: 56, 0x39: 57,
        0x7A: 58, 0x78: 59, 0x63: 60, 0x76: 61, 0x60: 62, 0x61: 63, 0x62: 64, 0x64: 65, 0x65: 66, 0x6D: 67, 0x67: 68, 0x6F: 69,
        0x72: 73, 0x73: 74, 0x74: 75, 0x75: 76, 0x77: 77, 0x79: 78, 0x7C: 79, 0x7B: 80, 0x7D: 81, 0x7E: 82,
        0x47: 83, 0x4B: 84, 0x43: 85, 0x4E: 86, 0x45: 87, 0x4C: 88, 0x53: 89, 0x54: 90, 0x55: 91, 0x56: 92,
        0x57: 93, 0x58: 94, 0x59: 95, 0x5B: 96, 0x5C: 97, 0x52: 98, 0x41: 99, 0x0A: 100, 0x6E: 101,
        0x3B: 224, 0x38: 225, 0x3A: 226, 0x37: 227, 0x3E: 228, 0x3C: 229, 0x3D: 230, 0x36: 231,
    ]

    /// Modifier keys (HID usage) in the order their flags are checked.
    public static let modifiers: Set<UInt16> = [224, 225, 226, 227, 228, 229, 230, 231]

    public static func usage(macKeyCode: UInt16) -> UInt16? { mac[macKeyCode] }

    /// Special keys offered on the phone's key bar.
    public enum Special: String, CaseIterable, Identifiable, Sendable {
        case escape, tab, enter, backspace, delete, up, down, left, right, home, end, pageUp, pageDown
        public var id: String { rawValue }
        public var usage: UInt16 {
            switch self {
            case .escape: 41
            case .tab: 43
            case .enter: 40
            case .backspace: 42
            case .delete: 76
            case .up: 82
            case .down: 81
            case .left: 80
            case .right: 79
            case .home: 74
            case .end: 77
            case .pageUp: 75
            case .pageDown: 78
            }
        }
        public var label: String {
            switch self {
            case .escape: "Esc"
            case .tab: "Tab"
            case .enter: "⏎"
            case .backspace: "⌫"
            case .delete: "⌦"
            case .up: "↑"
            case .down: "↓"
            case .left: "←"
            case .right: "→"
            case .home: "Home"
            case .end: "End"
            case .pageUp: "PgUp"
            case .pageDown: "PgDn"
            }
        }
    }

    public static let control: UInt16 = 224
    public static let shift: UInt16 = 225
    public static let alt: UInt16 = 226
    public static let command: UInt16 = 227
}
