import CoreGraphics
import Foundation

/// Drawing on the remote screen (as the desktop app): pen, highlighter and arrow in five colours and
/// four sizes. Strokes go to the other computer (`remote_draw`), which shows them over its screen
/// for the person there; they are drawn here as well and fade a few seconds after drawing stops.
/// A host that shows the drawing in the picture itself (`drawpic`, Linux X11) gets finished strokes
/// left to the picture. Drawing types and clicks nothing, so it works in view-only sessions too.
@MainActor
public final class DrawingModel: ObservableObject {
    public enum Tool: String, CaseIterable, Sendable { case pen, highlighter, arrow }

    public struct Stroke: Identifiable, Equatable, Sendable {
        public var id: UInt16
        public var tool: Tool
        public var color: Int
        public var width: Int
        /// In the picture, 0…1 from the top left.
        public var points: [CGPoint]
        public var done = false
        public var finished: Date?
    }

    /// Red, yellow, green, blue, white (the engine's palette).
    public static let palette: [(Double, Double, Double)] = [
        (229.0 / 255, 50.0 / 255, 45.0 / 255), (1.0, 212.0 / 255, 0.0), (34.0 / 255, 177.0 / 255, 76.0 / 255), (30.0 / 255, 123.0 / 255, 1.0), (1.0, 1.0, 1.0),
    ]
    public static let fadeAfter: TimeInterval = 5, fadeDuration: TimeInterval = 0.6, inPictureAfter: TimeInterval = 1.5
    static let maxPointsPerMessage = 64, maxPointsPerStroke = 4096, maxStrokes = 100

    let app: AppModel
    @Published public var on = false
    @Published public var tool = Tool.pen
    @Published public var color = 0
    @Published public var width = 2
    @Published public private(set) var strokes: [Stroke] = []
    @Published public private(set) var lastChange = Date.distantPast
    private var nextId: UInt16 = 1
    private var unsent: [CGPoint] = []
    private var sending = false
    private var session: String?

    init(app: AppModel) { self.app = app }

    /// The other computer shows drawings (it announced `draw`), and this side is the technician.
    public var available: Bool {
        let remote = app.remote
        return remote.phase == "active" && !remote.sharing && remote.session != nil && remote.peerFeatures.contains("draw")
    }
    var inPicture: Bool { app.remote.peerFeatures.contains("drawpic") }

    func sessionChanged(_ next: String?) {
        guard next != session else { return }
        session = next
        on = false
        strokes = []
        unsent = []
    }

    // MARK: Drawing

    public func begin(at point: CGPoint) {
        guard available, strokes.count < Self.maxStrokes else { return }
        let stroke = Stroke(id: nextId, tool: tool, color: color, width: width, points: [Self.clamped(point)])
        nextId = nextId == .max ? 1 : nextId + 1
        strokes.append(stroke)
        unsent = stroke.points
        touch()
        flush(done: false)
    }

    public func add(_ point: CGPoint) {
        guard var stroke = strokes.last, !stroke.done, stroke.points.count < Self.maxPointsPerStroke else { return }
        let point = Self.clamped(point)
        if let last = stroke.points.last, hypot(last.x - point.x, last.y - point.y) < 0.002 { return }
        if stroke.tool == .arrow, stroke.points.count >= 2 { stroke.points[stroke.points.count - 1] = point } else { stroke.points.append(point) }
        strokes[strokes.count - 1] = stroke
        unsent.append(point)
        touch()
        flush(done: false)
    }

    public func end() {
        guard var stroke = strokes.last, !stroke.done else { return }
        stroke.done = true
        stroke.finished = Date()
        strokes[strokes.count - 1] = stroke
        touch()
        flush(done: true)
    }

    public func undo() {
        guard !strokes.isEmpty else { return }
        strokes.removeLast()
        touch()
        send(.undo)
    }

    public func clear() {
        strokes = []
        touch()
        send(.clear)
    }

    private func touch() { lastChange = Date() }

    static func clamped(_ point: CGPoint) -> CGPoint { CGPoint(x: min(1, max(0, point.x)), y: min(1, max(0, point.y))) }
    nonisolated static func wire(_ value: CGFloat) -> UInt16 { UInt16((min(1, max(0, value)) * 65535).rounded()) }

    /// Points travel in batches (at most 64 per message, about 20 messages a second).
    private func flush(done: Bool) {
        guard let stroke = strokes.last else { return }
        if done {
            let points = unsent
            unsent = []
            var chunks = stride(from: 0, to: points.count, by: Self.maxPointsPerMessage).map { Array(points[$0..<min(points.count, $0 + Self.maxPointsPerMessage)]) }
            if chunks.isEmpty { chunks = [[]] }
            for (index, chunk) in chunks.enumerated() {
                send(.stroke(id: stroke.id, tool: stroke.tool, color: stroke.color, width: stroke.width, points: chunk, done: index == chunks.count - 1))
            }
            return
        }
        guard !sending else { return }
        sending = true
        Task {
            try? await Task.sleep(nanoseconds: 50_000_000)
            self.sending = false
            guard let current = self.strokes.last, current.id == stroke.id, !current.done, !self.unsent.isEmpty else { return }
            let points = Array(self.unsent.prefix(Self.maxPointsPerMessage))
            self.unsent.removeFirst(points.count)
            self.send(.stroke(id: current.id, tool: current.tool, color: current.color, width: current.width, points: points, done: false))
            if !self.unsent.isEmpty { self.flush(done: false) }
        }
    }

    enum Message: Encodable {
        case stroke(id: UInt16, tool: Tool, color: Int, width: Int, points: [CGPoint], done: Bool)
        case undo, clear
        enum Keys: String, CodingKey { case kind, id, tool, color, width, points, done }
        func encode(to encoder: Encoder) throws {
            var container = encoder.container(keyedBy: Keys.self)
            switch self {
            case let .stroke(id, tool, color, width, points, done):
                try container.encode("stroke", forKey: .kind)
                try container.encode(id, forKey: .id)
                try container.encode(tool.rawValue, forKey: .tool)
                try container.encode(color, forKey: .color)
                try container.encode(width, forKey: .width)
                try container.encode(points.map { [DrawingModel.wire($0.x), DrawingModel.wire($0.y)] }, forKey: .points)
                try container.encode(done, forKey: .done)
            case .undo: try container.encode("undo", forKey: .kind)
            case .clear: try container.encode("clear", forKey: .kind)
            }
        }
    }

    struct DrawArgs: Encodable { var session: String; var message: Message }

    private func send(_ message: Message) {
        guard let session = app.remote.session else { return }
        Task { _ = try? await app.engine.run("remote_draw", DrawArgs(session: session, message: message), as: Bool.self) }
    }

    // MARK: What is shown here

    /// Opacity of the whole drawing now; it fades like on the other computer, then it is gone.
    public func opacity(at now: Date) -> Double {
        let idle = now.timeIntervalSince(lastChange)
        if strokes.last.map({ !$0.done }) == true || idle < Self.fadeAfter { return 1 }
        return max(0, 1 - (idle - Self.fadeAfter) / Self.fadeDuration)
    }

    public func visible(at now: Date) -> [Stroke] {
        if opacity(at: now) <= 0 { return [] }
        return strokes.filter { stroke in
            !(inPicture && stroke.done && (stroke.finished.map { now.timeIntervalSince($0) >= Self.inPictureAfter } ?? false))
        }
    }

    /// Forgets faded strokes (the other computer cleared its board too).
    func expire(at now: Date) {
        if !strokes.isEmpty, opacity(at: now) <= 0 { strokes = [] }
    }

    /// Line width in points for a picture shown `height` points high (as the engine scales it).
    public static func lineWidth(tool: Tool, width: Int, height: CGFloat) -> CGFloat {
        let pen: [CGFloat] = [3, 5, 8, 12], highlight: [CGFloat] = [14, 20, 28, 36]
        let step = min(3, max(0, width - 1))
        return max(1, (tool == .highlighter ? highlight[step] : pen[step]) * max(1, height) / 1080)
    }
}
