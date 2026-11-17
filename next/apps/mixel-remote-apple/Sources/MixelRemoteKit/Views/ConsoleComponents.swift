#if os(macOS)
import SwiftUI

/// The header of a console page: a large title, one line under it, and the page's buttons at the right.
struct ConsoleHeader<Trailing: View>: View {
    let title: String
    let subtitle: String
    @ViewBuilder let trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text(verbatim: title).font(.system(size: 26, weight: .bold)).accessibilityAddTraits(.isHeader)
                if !subtitle.isEmpty { Text(verbatim: subtitle).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 12)
            HStack(spacing: 8) { trailing() }
        }
    }
}

/// A segmented control as in the redesign: a soft track, the chosen segment raised.
struct ConsoleSegmented<Value: Hashable>: View {
    let options: [(Value, String)]
    @Binding var selection: Value
    var label = ""

    var body: some View {
        HStack(spacing: 2) {
            ForEach(Array(options.enumerated()), id: \.offset) { _, option in
                let on = selection == option.0
                Button { selection = option.0 } label: {
                    Text(verbatim: option.1)
                        .font(.system(size: 13, weight: on ? .semibold : .regular))
                        .padding(.horizontal, 12).frame(height: 26)
                        .background(on ? Theme.page : .clear, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                        .shadow(color: on ? .black.opacity(0.08) : .clear, radius: 1, y: 1)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? [.isSelected, .isButton] : .isButton)
            }
        }
        .padding(2)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(verbatim: label))
    }
}

/// A settings row: title and help at the left, the control at the right.
struct SettingRow<Control: View>: View {
    let title: String
    var help = ""
    var first = false
    @ViewBuilder let control: () -> Control

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: title).font(.system(size: 13, weight: .semibold))
                if !help.isEmpty { Text(verbatim: help).font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            }
            Spacer(minLength: 12)
            control()
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .overlay(alignment: .top) { if !first { Rectangle().fill(Theme.hairline).frame(height: 1) } }
    }
}

/// A rounded panel of rows with a hairline border.
struct ConsoleCard<Content: View>: View {
    @ViewBuilder let content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: 0, content: content)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.page, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
    }
}

/// A switch with an accessibility label (the settings rows use it).
struct ConsoleSwitch: View {
    @Binding var isOn: Bool
    let label: String
    var body: some View {
        Toggle(isOn: $isOn) { Text(verbatim: label) }
            .labelsHidden()
            .toggleStyle(.switch)
            .tint(Theme.brand)
            .accessibilityLabel(Text(verbatim: label))
    }
}

/// A small search field with a magnifier.
struct ConsoleSearch: View {
    @Binding var text: String
    let prompt: String
    var width: CGFloat = 220
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(text: $text, prompt: Text(verbatim: prompt)) { Text(verbatim: prompt) }.textFieldStyle(.plain)
        }
        .padding(.horizontal, 10).frame(width: width, height: 30)
        .background(Theme.page, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
    }
}

/// A KPI card: caption, a big number, one line of fine print.
struct KpiCard: View {
    let title: String
    let value: String
    let detail: String
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(verbatim: title).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            Text(verbatim: value).font(.system(size: 26, weight: .bold)).monospacedDigit()
            Text(verbatim: detail).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(2)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.page, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}
#endif
