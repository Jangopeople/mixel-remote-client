import SwiftUI

/// The technician's drawing over the remote picture (aspect fit like the picture). While drawing is
/// on, dragging draws instead of moving the remote pointer.
public struct DrawingOverlay: View {
    @ObservedObject var drawing: DrawingModel
    let pictureSize: CGSize

    public init(drawing: DrawingModel, pictureSize: CGSize) {
        self.drawing = drawing
        self.pictureSize = pictureSize
    }

    static func fitted(_ picture: CGSize, in bounds: CGSize) -> CGRect {
        guard picture.width > 0, picture.height > 0 else { return .zero }
        let scale = min(bounds.width / picture.width, bounds.height / picture.height)
        let size = CGSize(width: picture.width * scale, height: picture.height * scale)
        return CGRect(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2, width: size.width, height: size.height)
    }

    public var body: some View {
        GeometryReader { geometry in
            let frame = Self.fitted(pictureSize, in: geometry.size)
            TimelineView(.animation(minimumInterval: 1 / 30, paused: drawing.strokes.isEmpty)) { timeline in
                Canvas { context, _ in
                    let now = timeline.date
                    context.opacity = drawing.opacity(at: now)
                    for stroke in drawing.visible(at: now) { draw(stroke, in: frame, context: &context) }
                }
                .onChange(of: timeline.date) { date in drawing.expire(at: date) }
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0, coordinateSpace: .local)
                    .onChanged { value in
                        guard frame.width > 0 else { return }
                        let point = CGPoint(x: (value.location.x - frame.minX) / frame.width, y: (value.location.y - frame.minY) / frame.height)
                        if drawing.strokes.last?.done ?? true { drawing.begin(at: point) } else { drawing.add(point) }
                    }
                    .onEnded { _ in drawing.end() },
                including: drawing.on ? .all : .none
            )
            .allowsHitTesting(drawing.on)
        }
        .accessibilityHidden(!drawing.on)
        .accessibilityLabel(Text(verbatim: L("Drawing on the remote screen")))
    }

    private func draw(_ stroke: DrawingModel.Stroke, in frame: CGRect, context: inout GraphicsContext) {
        guard let first = stroke.points.first else { return }
        let point = { (p: CGPoint) in CGPoint(x: frame.minX + p.x * frame.width, y: frame.minY + p.y * frame.height) }
        let rgb = DrawingModel.palette[min(DrawingModel.palette.count - 1, max(0, stroke.color))]
        let color = Color(red: rgb.0, green: rgb.1, blue: rgb.2).opacity(stroke.tool == .highlighter ? 0.35 : 1)
        let width = DrawingModel.lineWidth(tool: stroke.tool, width: stroke.width, height: frame.height)
        var path = Path()
        if stroke.tool == .arrow {
            let start = point(first), end = point(stroke.points.last ?? first)
            path.move(to: start)
            path.addLine(to: end)
            let angle = atan2(end.y - start.y, end.x - start.x), head = max(10, width * 4)
            for side in [-0.45, 0.45] {
                path.move(to: end)
                path.addLine(to: CGPoint(x: end.x - head * cos(angle + side), y: end.y - head * sin(angle + side)))
            }
        } else {
            path.move(to: point(first))
            for next in stroke.points.dropFirst() { path.addLine(to: point(next)) }
            if stroke.points.count == 1 { path.addLine(to: point(first)) }
        }
        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round))
    }
}

/// Pen on/off and its tools: pen, highlighter, arrow, colour, size, undo, clear.
public struct DrawingControls: View {
    @ObservedObject var drawing: DrawingModel

    public init(drawing: DrawingModel) { self.drawing = drawing }

    public static func title(_ tool: DrawingModel.Tool) -> String {
        switch tool {
        case .pen: L("Pen")
        case .highlighter: L("Highlighter")
        case .arrow: L("Arrow")
        }
    }
    static func symbol(_ tool: DrawingModel.Tool) -> String {
        switch tool {
        case .pen: "pencil.tip"
        case .highlighter: "highlighter"
        case .arrow: "arrow.up.right"
        }
    }
    static func colorName(_ index: Int) -> String { [L("Red"), L("Yellow"), L("Green"), L("Blue"), L("White")][index] }

    public var body: some View {
        HStack(spacing: 6) {
            Toggle(isOn: $drawing.on) { Label(drawing.on ? L("Draw: on") : L("Draw: off"), systemImage: "scribble.variable") }
                .toggleStyle(.button)
                .help(L("Draw on the remote screen. The person there sees it; nothing is clicked."))
            if drawing.on {
                Picker(selection: $drawing.tool) {
                    ForEach(DrawingModel.Tool.allCases, id: \.self) { tool in Label(Self.title(tool), systemImage: Self.symbol(tool)).tag(tool) }
                } label: { Text(verbatim: L("Tool")) }
                    .labelsHidden().fixedSize()
                Menu {
                    ForEach(0..<DrawingModel.palette.count, id: \.self) { index in
                        Button { drawing.color = index } label: { Label(Self.colorName(index), systemImage: drawing.color == index ? "checkmark.circle.fill" : "circle.fill") }
                    }
                    Divider()
                    ForEach(1...4, id: \.self) { size in
                        Button { drawing.width = size } label: { Label(L("Size %d", size), systemImage: drawing.width == size ? "checkmark" : "line.diagonal") }
                    }
                } label: {
                    let rgb = DrawingModel.palette[drawing.color]
                    Image(systemName: "circle.fill").foregroundStyle(Color(red: rgb.0, green: rgb.1, blue: rgb.2))
                }
                .fixedSize()
                .accessibilityLabel(Text(verbatim: L("Colour and size")))
                Button { drawing.undo() } label: { Label(L("Undo"), systemImage: "arrow.uturn.backward") }.disabled(drawing.strokes.isEmpty)
                Button { drawing.clear() } label: { Label(L("Clear"), systemImage: "trash") }
            }
        }
        .labelStyle(.iconOnly)
    }
}
