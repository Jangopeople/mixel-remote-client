import CoreGraphics
import Foundation

/// A session opened with a one-time password or without anyone at the other computer says so on a
/// banner over the picture (the two cases are different promises to the person at that computer).
/// Pure: the banner view only draws what this decides, and the tests check it.
public struct SessionNotice: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        /// Opened with a one-time password; `seconds` is the time left when the end is known.
        case password(seconds: Int?)
        /// Unattended: nobody at the other computer accepted.
        case unattended
        /// Unattended was asked for, but that computer is not set up for it: the person there was asked.
        case unattendedRefused
    }
    public enum Level: Int, Equatable, Sendable { case info, warning, urgent }

    public var kind: Kind
    public var level: Level

    /// The password session ends in this many seconds or fewer: the 5 minute and 1 minute warnings.
    public static let warnAt = 300
    public static let urgentAt = 60

    /// What to show now, or nil for an ordinary session.
    /// - Parameters:
    ///   - passwordSession: the engine reports the session as opened with a password (`password_session`).
    ///   - sentPassword: this app's own request carried a password (the engine may not report it yet).
    ///   - endsAt: the end of a password session, when known.
    public static func make(passwordSession: Bool, sentPassword: Bool, unattended: Bool, unattendedRefused: Bool = false, endsAt: Date?, now: Date = Date()) -> SessionNotice? {
        if passwordSession || sentPassword {
            guard let endsAt else { return SessionNotice(kind: .password(seconds: nil), level: .info) }
            let left = max(0, Int(endsAt.timeIntervalSince(now).rounded(.up)))
            let level: Level = left <= urgentAt ? .urgent : left <= warnAt ? .warning : .info
            return SessionNotice(kind: .password(seconds: left), level: level)
        }
        if unattended { return SessionNotice(kind: .unattended, level: .info) }
        if unattendedRefused { return SessionNotice(kind: .unattendedRefused, level: .warning) }
        return nil
    }

    /// "1:24:10", "4:59", "0:42".
    public static func clock(_ seconds: Int) -> String {
        let hours = seconds / 3600, minutes = (seconds % 3600) / 60, rest = seconds % 60
        return hours > 0 ? String(format: "%d:%02d:%02d", hours, minutes, rest) : String(format: "%d:%02d", minutes, rest)
    }
}

/// Exactly one pointer is on the screen over the remote picture: this Mac's own cursor. The picture
/// carries no pointer of the other computer (the hosts leave it out since 2.0.17) and the app draws
/// none over it; a second one would trail the first. What changes is the shape of the local cursor.
public enum PointerPolicy {
    public enum Cursor: Equatable, Sendable { case arrow, crosshair }

    /// The shape of the local cursor over the picture: a crosshair while "Show pointer" points at things
    /// without clicking, the normal arrow otherwise.
    public static func cursor(pointerMode: Bool) -> Cursor { pointerMode ? .crosshair : .arrow }

    /// The app never draws a pointer of its own over the remote picture.
    public static let drawsOverlayPointer = false

    /// Whether the local cursor is shown over the picture (always: it is the one pointer).
    public static let showsLocalCursor = true
}
