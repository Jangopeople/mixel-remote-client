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
    }
}

public final class RemoteScreenView: NSView {
    weak var session: SessionModel?
    var image: CGImage? { didSet { if image !== oldValue { layer?.contents = image; needsLayout = true } } }
    var controllable = false { didSet { if !controllable, oldValue { releaseAll() } } }
    var pointerMode = false { didSet { window?.invalidateCursorRects(for: self) } }
    private var pressed: [Int: UInt8] = [:]
    private var heldKeys: Set<UInt16> = []
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
    public override func resetCursorRects() {
        addCursorRect(bounds, cursor: pointerMode ? .crosshair : .arrow)
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
    }

    public override func mouseDown(with event: NSEvent) { down(event) }
    public override func mouseUp(with event: NSEvent) { up(event) }
    public override func rightMouseDown(with event: NSEvent) { down(event) }
    public override func rightMouseUp(with event: NSEvent) { up(event) }
    public override func otherMouseDown(with event: NSEvent) { down(event) }
    public override func otherMouseUp(with event: NSEvent) { up(event) }

    public override func scrollWheel(with event: NSEvent) {
        guard controllable, picturePoint(event) != nil else { return }
        let scale: CGFloat = event.hasPreciseScrollingDeltas ? 1.0 / 40 : 1
        wheel.x = max(-120, min(120, wheel.x + event.scrollingDeltaX * scale))
        wheel.y = max(-120, min(120, wheel.y + event.scrollingDeltaY * scale))
        let horizontal = Int16(wheel.x.rounded(.towardZero)), vertical = Int16(wheel.y.rounded(.towardZero))
        wheel.x -= CGFloat(horizontal)
        wheel.y -= CGFloat(vertical)
        if horizontal != 0 || vertical != 0 { session?.send(.wheel(horizontal: horizontal, vertical: vertical)) }
    }

    public override func keyDown(with event: NSEvent) {
        guard controllable, let usage = KeyMap.usage(macKeyCode: event.keyCode) else { return super.keyDown(with: event) }
        heldKeys.insert(usage)
        session?.send(.key(usage: usage, down: true))
    }

    public override func keyUp(with event: NSEvent) {
        guard controllable, let usage = KeyMap.usage(macKeyCode: event.keyCode) else { return super.keyUp(with: event) }
        heldKeys.remove(usage)
        session?.send(.key(usage: usage, down: false))
    }

    public override func flagsChanged(with event: NSEvent) {
        guard controllable, let usage = KeyMap.usage(macKeyCode: event.keyCode) else { return }
        let down = !heldKeys.contains(usage)
        if down { heldKeys.insert(usage) } else { heldKeys.remove(usage) }
        session?.send(.key(usage: usage, down: down))
    }

    /// Shortcuts like ⌘Q stay with this app; everything else reaches the remote computer.
    public override func performKeyEquivalent(with event: NSEvent) -> Bool {
        guard controllable, window?.firstResponder === self else { return super.performKeyEquivalent(with: event) }
        let keep: Set<String> = ["q", "w", ",", "h"]
        if event.modifierFlags.contains(.command), let key = event.charactersIgnoringModifiers?.lowercased(), keep.contains(key) {
            return super.performKeyEquivalent(with: event)
        }
        // With ⌘ held, AppKit sends no key-up: press and release at once.
        guard let usage = KeyMap.usage(macKeyCode: event.keyCode) else { return super.performKeyEquivalent(with: event) }
        session?.send(.key(usage: usage, down: true))
        session?.send(.key(usage: usage, down: false))
        return true
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
