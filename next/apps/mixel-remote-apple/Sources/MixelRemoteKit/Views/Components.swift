import SwiftUI

/// The Mixel mark: red rounded square with the arrows (drawn, so it is sharp at every size).
public struct MixelMark: View {
    var size: CGFloat
    public init(size: CGFloat = 28) { self.size = size }
    public var body: some View {
        RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
            .fill(Theme.brand)
            .frame(width: size, height: size)
            .overlay(Image(systemName: "arrow.left.arrow.right").font(.system(size: size * 0.48, weight: .bold)).foregroundStyle(.white))
            .accessibilityHidden(true)
    }
}

/// "Mixel Remote" with the mark.
public struct Brand: View {
    public init() {}
    public var body: some View {
        HStack(spacing: 8) {
            MixelMark(size: 26)
            (Text(verbatim: "Mixel ").fontWeight(.bold) + Text(verbatim: "Remote").fontWeight(.regular))
                .font(.title3)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(verbatim: "Mixel Remote"))
    }
}

/// The page header of a section.
public struct PageHeader: View {
    let title: String
    let subtitle: String
    public init(_ title: String, subtitle: String = "") {
        self.title = title
        self.subtitle = subtitle
    }
    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(verbatim: title).font(.largeTitle.weight(.semibold)).accessibilityAddTraits(.isHeader)
            if !subtitle.isEmpty { Text(verbatim: subtitle).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A Remote ID in large digits, readable aloud digit groups.
public struct RemoteIdText: View {
    let id: UInt32?
    public init(_ id: UInt32?) { self.id = id }
    public var body: some View {
        Text(verbatim: Format.remoteId(id))
            .font(.system(size: 30, weight: .semibold, design: .rounded).monospacedDigit())
            .textSelection(.enabled)
            .accessibilityLabel(Text(verbatim: L("Connection ID") + " " + Format.remoteId(id)))
    }
}

/// A labelled text field in Mixel's forms.
public struct FieldRow<Field: View>: View {
    let label: String
    let field: Field
    public init(_ label: String, @ViewBuilder field: () -> Field) {
        self.label = label
        self.field = field()
    }
    public var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(verbatim: label.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary).tracking(0.8)
            field
        }
    }
}

/// The 9-digit ID field: groups digits as typed.
public struct RemoteIdField: View {
    @Binding var text: String
    let prompt: String
    public init(text: Binding<String>, prompt: String = "123 456 789") {
        _text = text
        self.prompt = prompt
    }
    public var body: some View {
        TextField(text: $text, prompt: Text(verbatim: prompt)) { Text(verbatim: L("Partner connection ID")) }
            .textFieldStyle(.roundedBorder)
            .font(.title3.monospacedDigit())
            #if os(iOS)
            .keyboardType(.numberPad)
            #endif
            .onChange(of: text) { value in
                let digits = String(value.filter(\.isASCIIDigit).prefix(9))
                var grouped = ""
                for (index, digit) in digits.enumerated() {
                    if index > 0, index % 3 == 0 { grouped.append(" ") }
                    grouped.append(digit)
                }
                if grouped != value { text = grouped }
            }
    }
}

/// A confirmation dialog with one or two text fields (rename, invite, delete with typed name).
public struct FormSheet: View {
    public struct Field: Identifiable {
        public var id: String
        public var label: String
        public var value: String
        public var prompt: String = ""
        public init(id: String, label: String, value: String = "", prompt: String = "") {
            self.id = id
            self.label = label
            self.value = value
            self.prompt = prompt
        }
    }
    let title: String
    let text: String
    @State var fields: [Field]
    let confirm: String
    let danger: Bool
    let run: ([String: String]) async -> String?
    @Environment(\.dismiss) private var dismiss
    @State private var error = ""
    @State private var working = false

    /// `run` returns an error message, or nil when done.
    public init(title: String, text: String = "", fields: [Field] = [], confirm: String, danger: Bool = false, run: @escaping ([String: String]) async -> String?) {
        self.title = title
        self.text = text
        _fields = State(initialValue: fields)
        self.confirm = confirm
        self.danger = danger
        self.run = run
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(verbatim: title).font(.title2.weight(.semibold))
            if !text.isEmpty { Text(verbatim: text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            ForEach($fields) { $field in
                FieldRow(field.label) {
                    TextField(text: $field.value, prompt: Text(verbatim: field.prompt)) { Text(verbatim: field.label) }
                        .textFieldStyle(.roundedBorder)
                }
            }
            MessageLine(error)
            HStack {
                Spacer()
                Button(L("Cancel"), role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(role: danger ? .destructive : nil) {
                    working = true
                    Task {
                        let values = Dictionary(uniqueKeysWithValues: fields.map { ($0.id, $0.value.trimmingCharacters(in: .whitespacesAndNewlines)) })
                        if let problem = await run(values) { error = problem } else { dismiss() }
                        working = false
                    }
                } label: { Text(verbatim: confirm) }
                    .buttonStyle(.borderedProminent)
                    .tint(danger ? .red : Theme.brand)
                    .keyboardShortcut(.defaultAction)
                    .disabled(working)
            }
        }
        .padding(22)
        .frame(minWidth: 380)
    }
}

public extension View {
    /// A text-only button (a link on the Mac).
    @ViewBuilder func linkButton() -> some View {
        #if os(macOS)
        buttonStyle(.link)
        #else
        buttonStyle(.borderless)
        #endif
    }
}
