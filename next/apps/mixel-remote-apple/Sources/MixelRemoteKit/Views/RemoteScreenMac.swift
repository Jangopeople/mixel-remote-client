#if os(macOS)
import AppKit
import SwiftUI

/// The remote screen on the Mac: draws the picture (aspect fit) and sends keyboard, mouse and
/// scrolling while the session allows control. Keys travel by position (HID usage), so the remote
/// computer's own keyboard layout applies, as with a keyboard plugged into it.
public struct RemoteScreen: NSViewRepresentable {
    @ObservedObject var session: SessionModel

    public init(session: SessionModel) { self.session = session }

    public func makeNSView(context: Context) -> RemoteScreenView {
        let view = RemoteScreenView()
        view.session = session
        return view
    }

    public func updateNSView(_ view: RemoteScreenView, context: Context) {
        view.session = session
        view.image = session.frame
        view.pointerMode = session.pointerMode
        view.controllable = session.controllable
        view.claimFocusIfAppropriate()
    }
}

public final class RemoteScreenView: NSView {
    weak var session: SessionModel?
    var image: CGImage? { didSet { if image !== oldValue { layer?.contents = image; needsLayout = true } } }
    var controllable = false {
        didSet {
            if !controllable, oldValue { releaseAll() }
            else if controllable, !oldValue { claimFocusIfAppropriate() }
        }
    }
    var pointerMode = false { didSet { window?.invalidateCursorRects(for: self) } }
    private var pressed: [Int: UInt8] = [:]
    private(set) var heldKeys: Set<UInt16> = []
    private var wheel = CGPoint.zero
    private var tracking: NSTrackingArea?

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.contentsGravity = .resizeAspect
        layer?.backgroundColor = NSColor.black.cgColor
        layer?.magnificationFilter = .linear
        setAccessibilityElement(true)
        setAccessibilityRole(.image)
        setAccessibilityLabel(L("Remote screen"))
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    public override var acceptsFirstResponder: Bool { true }
    public override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    public override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        claimFocusIfAppropriate()
    }
    public override func mouseEntered(with event: NSEvent) {
        super.mouseEntered(with: event)
        claimFocusIfAppropriate()
    }

    func claimFocusIfAppropriate() {
        guard controllable, session?.focused != false, let window else { return }
        if window.firstResponder === self { return }
        if let textView = window.firstResponder as? NSTextView, textView.isEditable { return }
        if let textField = window.firstResponder as? NSTextField, textField.isEditable { return }
        window.makeFirstResponder(self)
    }
    public override func resetCursorRects() {
        addCursorRect(bounds, cursor: PointerPolicy.cursor(pointerMode: pointerMode) == .crosshair ? .crosshair : .arrow)
    }
    public override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: bounds, options: [.mouseMoved, .activeInKeyWindow, .inVisibleRect, .mouseEnteredAndExited], owner: self)
        addTrackingArea(area)
        tracking = area
    }
    public override func resignFirstResponder() -> Bool {
        releaseAll()
        return super.resignFirstResponder()
    }

    /// The point in the picture (0…1, top left origin), or nil outside it.
    func picturePoint(_ event: NSEvent, clamp: Bool = false) -> CGPoint? {
        guard let image else { return nil }
        let location = convert(event.locationInWindow, from: nil)
        let scale = min(bounds.width / CGFloat(image.width), bounds.height / CGFloat(image.height))
        guard scale > 0 else { return nil }
        let width = CGFloat(image.width) * scale, height = CGFloat(image.height) * scale
        let x = (location.x - (bounds.width - width) / 2) / width
        let y = 1 - (location.y - (bounds.height - height) / 2) / height
        if !clamp, x < 0 || x > 1 || y < 0 || y > 1 { return nil }
        return CGPoint(x: x, y: y)
    }

    private func move(_ event: NSEvent, clamp: Bool = false) -> Bool {
        guard let point = picturePoint(event, clamp: clamp) else { return false }
        session?.move(to: point)
        return true
    }

    public override func mouseMoved(with event: NSEvent) {
        if pointerMode || controllable { _ = move(with: event) }
    }
    private func move(with event: NSEvent) -> Bool { move(event, clamp: !pressed.isEmpty) }
    public override func mouseDragged(with event: NSEvent) { if controllable { _ = move(event, clamp: true) } }
    public override func rightMouseDragged(with event: NSEvent) { if controllable { _ = move(event, clamp: true) } }
    public override func otherMouseDragged(with event: NSEvent) { if controllable { _ = move(event, clamp: true) } }

    /// Buttons as the engine numbers them: 1 left, 2 middle, 3 right, 4 back, 5 forward.
    private func button(_ event: NSEvent) -> UInt8? {
        // Session protocol (as the desktop app): 1 left, 2 right, 3 middle.
        switch event.buttonNumber {
        case 0: 1
        case 1: 2
        case 2: 3
        case 3: 4
        case 4: 5
        default: nil
        }
    }

    private func down(_ event: NSEvent) {
        window?.makeFirstResponder(self)
        guard controllable, !pointerMode, let button = button(event), move(event) else { return }
        let clicks = UInt8(max(1, min(3, event.clickCount)))
        pressed[event.buttonNumber] = clicks
        session?.send(.buttonClick(button: button, down: true, clicks: clicks))
    }

    private func up(_ event: NSEvent) {
        guard let clicks = pressed.removeValue(forKey: event.buttonNumber), let button = button(event) else { return }
        _ = move(event, clamp: true)
        session?.send(.buttonClick(button: button, down: false, clicks: clicks))
        session?.pullClipboardSoon()
    }

    public override func mouseDown(with event: NSEvent) { down(event) }
    public override func mouseUp(with event: NSEvent) { up(event) }
    public override func rightMouseDown(with event: NSEvent) { down(event) }
    public override func rightMouseUp(with event: NSEvent) { up(event) }
    public override func otherMouseDown(with event: NSEvent) { down(event) }
    public override func otherMouseUp(with event: NSEvent) { up(event) }

    public override func scrollWheel(with event: NSEvent) {
        guard controllable, picturePoint(event) != nil else { return }
        if event.hasPreciseScrollingDeltas {
            wheel.x = max(-120, min(120, wheel.x + event.scrollingDeltaX / 12))
            wheel.y = max(-120, min(120, wheel.y + event.scrollingDeltaY / 12))
        } else {
            let dx: CGFloat = event.scrollingDeltaX == 0 ? 0 : (event.scrollingDeltaX > 0 ? max(1, event.scrollingDeltaX) : min(-1, event.scrollingDeltaX))
            let dy: CGFloat = event.scrollingDeltaY == 0 ? 0 : (event.scrollingDeltaY > 0 ? max(1, event.scrollingDeltaY) : min(-1, event.scrollingDeltaY))
            wheel.x = max(-120, min(120, wheel.x + dx))
            wheel.y = max(-120, min(120, wheel.y + dy))
        }
        let horizontal = Int16(wheel.x.rounded(.towardZero)), vertical = Int16(wheel.y.rounded(.towardZero))
        wheel.x -= CGFloat(horizontal)
        wheel.y -= CGFloat(vertical)
        if horizontal != 0 || vertical != 0 { session?.send(.wheel(horizontal: horizontal, vertical: vertical)) }
    }

    /// Whether a modifier key usage (224…231) is currently pressed according to `flags`.
    public static func modifierIsDown(usage: UInt16, flags: NSEvent.ModifierFlags) -> Bool? {
        switch usage {
        case 224, 228: return flags.contains(.control)
        case 225, 229: return flags.contains(.shift)
        case 226, 230: return flags.contains(.option)
        case 227, 231: return flags.contains(.command)
        default: return nil
        }
    }

    /// Reconciles `heldKeys` with the live modifier flags on `event` so modifiers pressed or
    /// released outside the window (such as ⌘Tab) can never become inverted.
    func syncModifiers(_ flags: NSEvent.ModifierFlags) {
        let cmdAsCtrl = remoteShortcutModifier == KeyMap.control
        let pairs: [(Bool, UInt16, UInt16)] = [
            (flags.contains(.control) || (cmdAsCtrl && flags.contains(.command)), KeyMap.control, 228),
            (flags.contains(.shift), KeyMap.shift, 229),
            (flags.contains(.option), KeyMap.alt, 230),
            (!cmdAsCtrl && flags.contains(.command), KeyMap.command, 231),
        ]
        for (isDown, left, right) in pairs {
            let wasDown = heldKeys.contains(left) || heldKeys.contains(right)
            if isDown && !wasDown {
                heldKeys.insert(left)
                session?.send(.key(usage: left, down: true))
            } else if !isDown && wasDown {
                for usage in [left, right] where heldKeys.remove(usage) != nil {
                    session?.send(.key(usage: usage, down: false))
                }
            }
        }
    }

    public override func keyDown(with event: NSEvent) {
        guard controllable, let usage = KeyMap.usage(macKeyCode: event.keyCode) else { return super.keyDown(with: event) }
        syncModifiers(event.modifierFlags)
        if event.modifierFlags.contains(.command) || event.modifierFlags.contains(.control) {
            let mod = event.modifierFlags.contains(.control) ? KeyMap.control : remoteShortcutModifier
            let needsWrap = !heldKeys.contains(mod) && !heldKeys.contains(mod + 4)
            if usage == 25 { // V -> Paste
                if session?.remote.clipboard == false {
                    releaseAll()
                }
                session?.pasteToRemote(usage: 25, modifiers: needsWrap ? [mod] : [])
                return
            } else if usage == 6 || usage == 27 { // C / X -> Copy / Cut
                heldKeys.insert(usage)
                if needsWrap { session?.send(.key(usage: mod, down: true)) }
                session?.send(.key(usage: usage, down: true))
                if needsWrap {
                    session?.send(.key(usage: usage, down: false))
                    session?.send(.key(usage: mod, down: false))
                    heldKeys.remove(usage)
                }
                session?.pullClipboardSoon()
                return
            }
        }
        heldKeys.insert(usage)
        session?.send(.key(usage: usage, down: true))
    }

    public override func keyUp(with event: NSEvent) {
        guard controllable, let usage = KeyMap.usage(macKeyCode: event.keyCode) else { return super.keyUp(with: event) }
        if heldKeys.remove(usage) != nil {
            session?.send(.key(usage: usage, down: false))
        }
    }

    public override func flagsChanged(with event: NSEvent) {
        guard controllable else { return }
        syncModifiers(event.modifierFlags)
    }

    /// Shortcuts like ⌘Q stay with this app; everything else reaches the remote computer.
    public override func performKeyEquivalent(with event: NSEvent) -> Bool {
        guard controllable, session?.focused != false else { return super.performKeyEquivalent(with: event) }
        if let window, window.firstResponder !== self {
            if let textView = window.firstResponder as? NSTextView, textView.isEditable { return super.performKeyEquivalent(with: event) }
            if let textField = window.firstResponder as? NSTextField, textField.isEditable { return super.performKeyEquivalent(with: event) }
            window.makeFirstResponder(self)
        }
        let keep: Set<String> = ["q", "w", ",", "h"]
        if event.modifierFlags.contains(.command), let key = event.charactersIgnoringModifiers?.lowercased(), keep.contains(key) {
            return super.performKeyEquivalent(with: event)
        }
        // With ⌘ held, AppKit sends no key-up: reconcile modifiers and press/release at once.
        guard let usage = KeyMap.usage(macKeyCode: event.keyCode) else { return super.performKeyEquivalent(with: event) }
        syncModifiers(event.modifierFlags)
        let mod = event.modifierFlags.contains(.control) ? KeyMap.control : remoteShortcutModifier
        let needsWrap = !heldKeys.contains(mod) && !heldKeys.contains(mod + 4)
        if event.modifierFlags.contains(.command) || event.modifierFlags.contains(.control) {
            if usage == 25 { // V -> Paste (sync local clipboard to remote first)
                if session?.remote.clipboard == false {
                    releaseAll()
                }
                session?.pasteToRemote(usage: 25, modifiers: needsWrap ? [mod] : [])
                return true
            } else if usage == 6 || usage == 27 { // C / X -> Copy / Cut (pull remote clipboard after)
                session?.copyFromRemote(usage: usage, modifiers: needsWrap ? [mod] : [])
                return true
            } else if needsWrap {
                session?.press(usage, modifiers: [mod])
                return true
            }
        }
        session?.send(.key(usage: usage, down: true))
        session?.send(.key(usage: usage, down: false))
        return true
    }

    private var remoteShortcutModifier: UInt16 {
        session?.remoteShortcutModifier ?? KeyMap.command
    }

    @objc public func copy(_ sender: Any?) {
        guard controllable else { return }
        let mod = remoteShortcutModifier
        let needsWrap = !heldKeys.contains(mod) && !heldKeys.contains(mod + 4)
        session?.copyFromRemote(usage: 6, modifiers: needsWrap ? [mod] : [])
    }

    @objc public func paste(_ sender: Any?) {
        guard controllable else { return }
        if session?.remote.clipboard == false {
            releaseAll()
        }
        let mod = remoteShortcutModifier
        let needsWrap = !heldKeys.contains(mod) && !heldKeys.contains(mod + 4)
        session?.pasteToRemote(usage: 25, modifiers: needsWrap ? [mod] : [])
    }

    @objc public func cut(_ sender: Any?) {
        guard controllable else { return }
        let mod = remoteShortcutModifier
        let needsWrap = !heldKeys.contains(mod) && !heldKeys.contains(mod + 4)
        session?.copyFromRemote(usage: 27, modifiers: needsWrap ? [mod] : [])
    }

    public override func selectAll(_ sender: Any?) {
        guard controllable else { return }
        let mod = remoteShortcutModifier
        let needsWrap = !heldKeys.contains(mod) && !heldKeys.contains(mod + 4)
        session?.press(4, modifiers: needsWrap ? [mod] : [])
    }

    func releaseAll() {
        guard !pressed.isEmpty || !heldKeys.isEmpty || wheel != .zero else { return }
        pressed.removeAll()
        heldKeys.removeAll()
        wheel = .zero
        session?.send(.releaseAll)
    }
}
#endif
