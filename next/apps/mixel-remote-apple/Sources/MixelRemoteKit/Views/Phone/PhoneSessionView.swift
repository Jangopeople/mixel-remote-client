#if os(iOS)
import SwiftUI
import UIKit

/// A session on the phone: the remote screen with zoom, touch control and the keyboard.
/// Tap = click, double tap = double click, touch and hold = right click, two fingers = scroll,
/// pinch = zoom (this phone only). The key bar adds Esc, Tab, arrows and held Ctrl/Alt/⌘.
public struct PhoneSessionView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var session: SessionModel
    @State private var keyboard = false
    @State private var held: Set<UInt16> = []
    @State private var showChat = false
    @State private var confirmEnd = false

    public init(app: AppModel) {
        self.app = app
        session = app.session
    }

    var remote: RemoteStatus { app.remote }

    public var body: some View {
        VStack(spacing: 0) {
            topBar
            ZStack {
                Color.black.ignoresSafeArea()
                TouchScreen(session: session, keyboard: $keyboard, held: $held)
                if session.frame == nil {
                    VStack(spacing: 12) {
                        ProgressView().tint(.white)
                        Text(verbatim: remote.phase == "outgoing" ? L("Waiting for the other person to accept your request.") : L("Connecting securely…"))
                            .foregroundStyle(.white.opacity(0.85)).multilineTextAlignment(.center).padding(.horizontal)
                        Button(L("Cancel")) { session.end() }.buttonStyle(.bordered).tint(.white)
                    }
                }
                VStack {
                    Spacer()
                    if !session.error.isEmpty || !session.notice.isEmpty {
                        Text(verbatim: session.error.isEmpty ? session.notice : session.error)
                            .font(.callout).foregroundStyle(.white)
                            .padding(10).background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 10)).padding()
                    }
                }
            }
            if remote.control { keyBar }
        }
        .background(Color.black)
        .sheet(isPresented: $showChat) { PhoneChatSheet(session: session) }
        .confirmationDialog(L("End session?"), isPresented: $confirmEnd) {
            Button(L("End session"), role: .destructive) { session.end() }
        }
    }

    private var topBar: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 1) {
                Text(verbatim: Format.remoteId(remote.peer)).font(.headline.monospacedDigit())
                Text(verbatim: remote.control ? L("View and control") : L("View only")).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if remote.control, remote.displays > 1 {
                Menu {
                    ForEach(0..<remote.displays, id: \.self) { index in
                        Button(remote.displayAll && index == remote.displays - 1 ? L("All screens") : L("Display %d", Int(index) + 1)) { session.selectDisplay(index) }
                    }
                } label: { Label(L("Display"), systemImage: "display.2") }
            }
            if remote.control {
                Button { keyboard.toggle() } label: { Label(L("Keyboard"), systemImage: keyboard ? "keyboard.chevron.compact.down" : "keyboard") }
            }
            Menu {
                if remote.offers("sas") { Button(L("Ctrl+Alt+Del")) { session.ctrlAltDel() } }
                if remote.offers("lock") { Button(L("Lock computer")) { session.lockNow() } }
                if remote.offers("privacy") { Button("\(L("Privacy screen")): \(session.privacy ? L("on") : L("off"))") { session.togglePrivacy() } }
                if remote.offers("block") { Button("\(L("Block input")): \(session.blockInput ? L("on") : L("off"))") { session.toggleBlockInput() } }
                if remote.offers("sound") { Button("\(L("Sound")): \(session.sound ? L("on") : L("off"))") { session.toggleSound() } }
                if remote.offers("restart") { Button(session.restartArmed ? L("Click again to restart") : L("Restart computer")) { session.restartComputer() } }
                if remote.phase == "active" { Button(L("Chat")) { showChat = true } }
                Button(L("Renew connection")) { session.renew() }
            } label: { Label(L("More"), systemImage: "ellipsis.circle") }
            Button(role: .destructive) { confirmEnd = true } label: { Label(L("End session"), systemImage: "xmark.circle.fill") }
                .tint(Theme.brand)
        }
        .labelStyle(.iconOnly)
        .font(.title3)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private var keyBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                modifier("Ctrl", KeyMap.control)
                modifier("Alt", KeyMap.alt)
                modifier("⌘/Win", KeyMap.command)
                modifier("⇧", KeyMap.shift)
                ForEach(KeyMap.Special.allCases) { key in
                    Button(key.label) { session.press(key.usage, modifiers: Array(held)); held.removeAll() }
                        .buttonStyle(.bordered)
                        .tint(.gray)
                        .accessibilityLabel(Text(verbatim: key.rawValue))
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .background(.bar)
    }

    private func modifier(_ title: String, _ usage: UInt16) -> some View {
        let on = held.contains(usage)
        return Button(title) { if on { held.remove(usage) } else { held.insert(usage) } }
            .buttonStyle(.bordered)
            .tint(on ? Theme.brand : .gray)
            .accessibilityValue(Text(verbatim: on ? L("on") : L("off")))
    }
}

struct PhoneChatSheet: View {
    @ObservedObject var session: SessionModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            VStack {
                List(session.chat) { message in
                    HStack {
                        if message.mine { Spacer() }
                        Text(verbatim: message.text).padding(8)
                            .foregroundStyle(message.mine ? .white : .primary)
                            .background(message.mine ? Theme.brand : Theme.surface, in: RoundedRectangle(cornerRadius: 10))
                        if !message.mine { Spacer() }
                    }
                    .listRowSeparator(.hidden)
                }
                .listStyle(.plain)
                HStack {
                    TextField(text: $session.chatDraft, prompt: Text(verbatim: L("Chat message"))) { Text(verbatim: L("Chat message")) }
                        .textFieldStyle(.roundedBorder).onSubmit { session.sendChat() }
                    Button(L("Send")) { session.sendChat() }.disabled(session.chatDraft.isEmpty)
                }
                .padding()
            }
            .navigationTitle(L("Chat"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L("Done")) { dismiss() } } }
        }
    }
}

/// The remote picture in a zoomable scroll view, with touch gestures and keyboard input.
struct TouchScreen: UIViewRepresentable {
    @ObservedObject var session: SessionModel
    @Binding var keyboard: Bool
    @Binding var held: Set<UInt16>

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> UIScrollView {
        let scroll = PictureScroll()
        scroll.backgroundColor = .black
        scroll.minimumZoomScale = 1
        scroll.maximumZoomScale = 5
        scroll.delegate = context.coordinator
        scroll.showsHorizontalScrollIndicator = false
        scroll.showsVerticalScrollIndicator = false
        scroll.contentInsetAdjustmentBehavior = .never
        let image = UIImageView()
        image.contentMode = .scaleAspectFit
        image.isUserInteractionEnabled = true
        image.isAccessibilityElement = true
        image.accessibilityLabel = L("Remote screen")
        image.accessibilityTraits = [.image, .allowsDirectInteraction]
        scroll.addSubview(image)
        context.coordinator.image = image
        let keys = KeyCatcher()
        keys.coordinator = context.coordinator
        scroll.addSubview(keys)
        context.coordinator.keys = keys

        let tap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tap(_:)))
        let double = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.doubleTap(_:)))
        double.numberOfTapsRequired = 2
        tap.require(toFail: double)
        let hold = UILongPressGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.hold(_:)))
        let scrollGesture = UIPanGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.twoFingers(_:)))
        scrollGesture.minimumNumberOfTouches = 2
        scrollGesture.maximumNumberOfTouches = 2
        for gesture in [tap, double, hold, scrollGesture] { image.addGestureRecognizer(gesture as UIGestureRecognizer) }
        // Panning a zoomed picture uses one finger; scrolling the remote screen uses two.
        scroll.panGestureRecognizer.minimumNumberOfTouches = 1
        scroll.panGestureRecognizer.maximumNumberOfTouches = 1
        return scroll
    }

    func updateUIView(_ scroll: UIScrollView, context: Context) {
        context.coordinator.parent = self
        if let frame = session.frame {
            if context.coordinator.image?.image?.cgImage !== frame {
                context.coordinator.image?.image = UIImage(cgImage: frame)
            }
        } else {
            context.coordinator.image?.image = nil
        }
        context.coordinator.layout(scroll)
        if keyboard, !(context.coordinator.keys?.isFirstResponder ?? false) { context.coordinator.keys?.becomeFirstResponder() }
        if !keyboard, context.coordinator.keys?.isFirstResponder ?? false { context.coordinator.keys?.resignFirstResponder() }
    }

    @MainActor
    final class Coordinator: NSObject, UIScrollViewDelegate {
        var parent: TouchScreen
        weak var image: UIImageView?
        weak var keys: KeyCatcher?
        private var wheel = CGPoint.zero
        private var lastSize = CGSize.zero

        init(_ parent: TouchScreen) { self.parent = parent }
        var session: SessionModel { parent.session }

        func viewForZooming(in scrollView: UIScrollView) -> UIView? { image }

        func layout(_ scroll: UIScrollView) {
            guard scroll.bounds.size != lastSize || image?.frame.size == .zero else { return }
            lastSize = scroll.bounds.size
            if scroll.zoomScale == 1 { image?.frame = CGRect(origin: .zero, size: scroll.bounds.size) }
            scroll.contentSize = image?.frame.size ?? .zero
        }

        /// The touched point in the picture (0…1), or nil outside the picture.
        func point(_ gesture: UIGestureRecognizer) -> CGPoint? {
            guard let image, let picture = image.image else { return nil }
            let location = gesture.location(in: image)
            let bounds = image.bounds
            let scale = min(bounds.width / picture.size.width, bounds.height / picture.size.height)
            guard scale > 0 else { return nil }
            let width = picture.size.width * scale, height = picture.size.height * scale
            let x = (location.x - (bounds.width - width) / 2) / width
            let y = (location.y - (bounds.height - height) / 2) / height
            guard (0...1).contains(x), (0...1).contains(y) else { return nil }
            return CGPoint(x: x, y: y)
        }

        func click(at point: CGPoint, button: UInt8, clicks: UInt8) {
            session.move(to: point)
            session.send(.buttonClick(button: button, down: true, clicks: clicks))
            session.send(.buttonClick(button: button, down: false, clicks: clicks))
        }

        @objc func tap(_ gesture: UITapGestureRecognizer) {
            guard session.controllable, let point = point(gesture) else { return }
            click(at: point, button: 1, clicks: 1)
        }

        @objc func doubleTap(_ gesture: UITapGestureRecognizer) {
            guard session.controllable, let point = point(gesture) else { return }
            session.move(to: point)
            for clicks: UInt8 in [1, 2] {
                session.send(.buttonClick(button: 1, down: true, clicks: clicks))
                session.send(.buttonClick(button: 1, down: false, clicks: clicks))
            }
        }

        @objc func hold(_ gesture: UILongPressGestureRecognizer) {
            guard gesture.state == .began, session.controllable, let point = point(gesture) else { return }
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            click(at: point, button: 3, clicks: 1)
        }

        @objc func twoFingers(_ gesture: UIPanGestureRecognizer) {
            guard session.controllable else { return }
            if gesture.state == .began, let point = point(gesture) { session.move(to: point) }
            let delta = gesture.translation(in: gesture.view)
            gesture.setTranslation(.zero, in: gesture.view)
            wheel.x = max(-120, min(120, wheel.x + delta.x / 12))
            wheel.y = max(-120, min(120, wheel.y + delta.y / 12))
            let horizontal = Int16(wheel.x.rounded(.towardZero)), vertical = Int16(wheel.y.rounded(.towardZero))
            wheel.x -= CGFloat(horizontal)
            wheel.y -= CGFloat(vertical)
            if horizontal != 0 || vertical != 0 { session.send(.wheel(horizontal: horizontal, vertical: vertical)) }
            if gesture.state == .ended || gesture.state == .cancelled { wheel = .zero }
        }

        // Keyboard (on-screen and hardware).
        func type(_ text: String) {
            guard session.controllable else { return }
            if parent.held.isEmpty {
                if text == "\n" { session.press(40) } else { session.send(.text(text)) }
            } else if let usage = Self.usage(for: text) {
                session.press(usage, modifiers: Array(parent.held))
                parent.held.removeAll()
            }
        }

        func backspace() { if session.controllable { session.press(42, modifiers: Array(parent.held)) } }

        func hardware(_ presses: Set<UIPress>, down: Bool) -> Bool {
            guard session.controllable else { return false }
            var handled = false
            for press in presses {
                guard let key = press.key else { continue }
                let usage = UInt16(key.keyCode.rawValue)
                guard usage > 3, usage < 232 else { continue }
                session.send(.key(usage: usage, down: down))
                handled = true
            }
            return handled
        }

        /// Letters and digits as keys, for Ctrl+C and similar from the on-screen keyboard.
        static func usage(for text: String) -> UInt16? {
            guard let scalar = text.lowercased().unicodeScalars.first, text.count == 1 else { return nil }
            switch scalar {
            case "a"..."z": return UInt16(scalar.value - 97 + 4)
            case "1"..."9": return UInt16(scalar.value - 49 + 30)
            case "0": return 39
            case " ": return 44
            default: return nil
            }
        }
    }
}

/// Keeps the picture filling the visible area while it is not zoomed (also after rotation).
final class PictureScroll: UIScrollView {
    override func layoutSubviews() {
        super.layoutSubviews()
        if zoomScale == 1, let image = subviews.first(where: { $0 is UIImageView }), image.frame.size != bounds.size {
            image.frame = CGRect(origin: .zero, size: bounds.size)
            contentSize = bounds.size
        }
    }
}

/// An invisible view that receives typing: the on-screen keyboard and a hardware keyboard.
final class KeyCatcher: UIView, UIKeyInput {
    weak var coordinator: TouchScreen.Coordinator?
    override var canBecomeFirstResponder: Bool { true }
    var hasText: Bool { true }
    var autocorrectionType: UITextAutocorrectionType = .no
    var autocapitalizationType: UITextAutocapitalizationType = .none
    var spellCheckingType: UITextSpellCheckingType = .no
    var smartQuotesType: UITextSmartQuotesType = .no
    var smartDashesType: UITextSmartDashesType = .no
    var keyboardType: UIKeyboardType = .asciiCapable

    func insertText(_ text: String) { MainActor.assumeIsolated { coordinator?.type(text) } }
    func deleteBackward() { MainActor.assumeIsolated { coordinator?.backspace() } }

    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        if !(coordinator?.hardware(presses, down: true) ?? false) { super.pressesBegan(presses, with: event) }
    }
    override func pressesEnded(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        if !(coordinator?.hardware(presses, down: false) ?? false) { super.pressesEnded(presses, with: event) }
    }
}
#endif
