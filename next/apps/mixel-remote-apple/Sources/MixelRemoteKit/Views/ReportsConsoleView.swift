#if os(macOS)
import SwiftUI

/// Session reports in the "Pro Console" look: the header with Export CSV / PDF, the period buttons with
/// From and To, the technician and a search, four KPI cards and the table (Started, Device, Technician,
/// Duration, Type, Recording, Note, Ticket). Only what the report contains is shown: the ticket and the
/// recording show "—" until the server records them, and no number is estimated.
struct ReportsConsoleView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var reports: ReportsModel
    @State private var editing: SessionRow?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ConsoleHeader(title: L("Session reports"), subtitle: reports.tenant.map { L("Every session of %@, with duration, type and notes.", $0.name) } ?? "") {
                Button { save(Data(reports.csv().utf8), name: reports.fileStem + ".csv") } label: { Label(L("Export CSV"), systemImage: "arrow.down.to.line") }
                    .disabled(reports.visibleRows.isEmpty)
                Button { save(reports.pdf(), name: reports.fileStem + ".pdf") } label: { Label(L("Export PDF"), systemImage: "arrow.down.to.line") }
                    .disabled(reports.visibleRows.isEmpty)
            }
            .padding(.horizontal, 28).padding(.top, 22).padding(.bottom, 14)

            if reports.tenant == nil {
                note(L("Session reports are part of company plans. Create or join a company under Account."))
            } else if reports.needsPlan || !reports.included {
                note(L("Session reports need a paid plan with reports. Upgrade the plan in the portal."))
            } else {
                toolbar.padding(.horizontal, 28).padding(.bottom, 14)
                MessageLine(reports.error).padding(.horizontal, 28).padding(.bottom, 6)
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        kpis
                        table
                    }
                    .padding(.horizontal, 28).padding(.bottom, 28)
                }
            }
            if reports.tenant == nil || reports.needsPlan || !reports.included { Spacer(minLength: 0) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.chrome.opacity(0.5))
        .background(Theme.page)
        .task { await reports.load() }
        .sheet(item: $editing) { row in
            FormSheet(title: L("Session note"), fields: [.init(id: "note", label: L("Note (optional)"), value: row.note ?? "")], confirm: L("Save note")) { values in
                await reports.setNote(row, values["note"] ?? "") ? nil : reports.error
            }
        }
    }

    private func note(_ text: String) -> some View {
        Text(verbatim: text).foregroundStyle(.secondary).padding(.horizontal, 28)
    }

    // MARK: Toolbar

    private var toolbar: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { periodGroup; Spacer(minLength: 0); filterGroup }
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) { periodGroup }
                HStack(spacing: 10) { filterGroup }
            }
        }
    }

    @ViewBuilder private var periodGroup: some View {
        ConsoleSegmented(options: [(ReportsModel.Period.today, L("Today")), (.week, L("7 days")), (.month, L("30 days")), (.custom, L("Custom"))],
                         selection: Binding(get: { reports.period }, set: { next in Task { await reports.choose(next) } }), label: L("Period"))
        dateField(L("From"), $reports.from)
        dateField(L("To"), $reports.to)
    }

    @ViewBuilder private var filterGroup: some View {
        if reports.manager {
            Picker(selection: $reports.technician) {
                Text(verbatim: L("All technicians")).tag(String?.none)
                ForEach(app.company.members) { Text(verbatim: $0.name.isEmpty ? $0.email : $0.name).tag(Optional($0.userId)) }
            } label: { Text(verbatim: L("Technician")) }
                .labelsHidden().fixedSize()
                .onChange(of: reports.technician) { _ in Task { await reports.load() } }
        }
        ConsoleSearch(text: $reports.search, prompt: L("Device, ticket or note"))
    }

    private func dateField(_ title: String, _ value: Binding<Date>) -> some View {
        HStack(spacing: 6) {
            Text(verbatim: title).foregroundStyle(.secondary)
            DatePicker(selection: Binding(get: { value.wrappedValue }, set: { value.wrappedValue = $0; Task { await reports.dateChanged() } }), displayedComponents: .date) { Text(verbatim: title) }
                .labelsHidden().datePickerStyle(.field)
        }
        .padding(.horizontal, 10).frame(height: 30)
        .background(Theme.page, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    // MARK: KPI cards

    private var kpis: some View {
        let sum = reports.summary
        return HStack(spacing: 12) {
            KpiCard(title: L("Sessions"), value: "\(sum.sessions)", detail: ReportsModel.stamp(day: reports.from) + " – " + ReportsModel.stamp(day: reports.to))
            KpiCard(title: L("Total time"), value: ReportsModel.hoursMinutes(sum.totalSeconds),
                    detail: sum.sessions == 0 ? "—" : L("Avg. %@ per session", ReportsModel.hoursMinutes(sum.averageSeconds)))
            KpiCard(title: L("Unattended"), value: sum.sessions == 0 ? "—" : "\(sum.unattendedPercent)%", detail: L("%d of %d sessions", sum.unattended, sum.sessions))
            KpiCard(title: L("Notes filed"), value: sum.sessions == 0 ? "—" : "\(sum.notedPercent)%",
                    detail: sum.withTicket > 0 ? L("%d linked to tickets", sum.withTicket) : L("%d of %d sessions", sum.withNote, sum.sessions))
        }
    }

    // MARK: Table

    private enum Col {
        static let started: CGFloat = 128, technician: CGFloat = 150, duration: CGFloat = 76, type: CGFloat = 110, recording: CGFloat = 92, ticket: CGFloat = 84
    }

    @ViewBuilder private var table: some View {
        let rows = reports.visibleRows
        ConsoleCard {
            if rows.isEmpty {
                Text(verbatim: reports.loading ? L("Loading…") : reports.search.isEmpty ? L("No sessions in this period.") : L("No matching sessions."))
                    .foregroundStyle(.secondary).padding(24).frame(maxWidth: .infinity)
            } else {
                header
                ForEach(rows) { row in
                    self.row(row)
                }
            }
        }
    }

    private func head(_ text: String) -> some View {
        Text(verbatim: text.uppercased()).font(.system(size: 11, weight: .semibold)).tracking(0.8).foregroundStyle(.secondary)
    }

    private var header: some View {
        HStack(spacing: 10) {
            head(L("Started")).frame(width: Col.started, alignment: .leading)
            head(L("Device")).frame(maxWidth: .infinity, alignment: .leading)
            head(L("Technician")).frame(width: Col.technician, alignment: .leading)
            head(L("Duration")).frame(width: Col.duration, alignment: .trailing)
            head(L("Type")).frame(width: Col.type, alignment: .leading)
            head(L("Recording")).frame(width: Col.recording, alignment: .leading)
            head(L("Note")).frame(maxWidth: .infinity, alignment: .leading)
            head(L("Ticket")).frame(width: Col.ticket, alignment: .leading)
        }
        .padding(.horizontal, 16).padding(.vertical, 9)
        .background(Theme.groupRow)
        .clipShape(UnevenRoundedTop(radius: 12))
    }

    private func row(_ row: SessionRow) -> some View {
        HStack(spacing: 10) {
            Text(verbatim: ReportsModel.stamp(row.startedAt)).monospacedDigit().frame(width: Col.started, alignment: .leading)
            HStack(spacing: 8) {
                OSTile(os: app.devices.os(of: row.connectionId), dot: row.endedAt == nil ? Theme.success : nil, size: 26)
                Text(verbatim: row.deviceName).fontWeight(.medium).lineLimit(1)
                if row.endedAt == nil { Pill(L("Running"), foreground: Theme.sessionText, background: Theme.sessionSoft) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(verbatim: row.technicianName ?? row.technicianEmail ?? L("Deleted account")).lineLimit(1).frame(width: Col.technician, alignment: .leading)
            Text(verbatim: ReportsModel.duration(row.durationSeconds)).monospacedDigit().frame(width: Col.duration, alignment: .trailing)
            kind(row).frame(width: Col.type, alignment: .leading)
            recording(row).frame(width: Col.recording, alignment: .leading)
            noteCell(row).frame(maxWidth: .infinity, alignment: .leading)
            Text(verbatim: (row.ticket ?? "").isEmpty ? "—" : row.ticket ?? "").font(.system(size: 13, design: .monospaced)).foregroundStyle((row.ticket ?? "").isEmpty ? .secondary : .primary)
                .frame(width: Col.ticket, alignment: .leading)
        }
        .font(.system(size: 13))
        .padding(.horizontal, 16).padding(.vertical, 9)
        .overlay(alignment: .top) { Rectangle().fill(Theme.hairline.opacity(0.7)).frame(height: 1) }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private func kind(_ row: SessionRow) -> some View {
        if row.kind == "unattended" {
            Pill(L("Unattended"), symbol: "key.fill", foreground: Theme.infoText, background: Theme.infoSoft)
        } else {
            Text(verbatim: reports.kindLabel(row)).foregroundStyle(.secondary).lineLimit(1)
        }
    }

    @ViewBuilder private func recording(_ row: SessionRow) -> some View {
        if row.recording == true {
            Pill(L("Saved"), symbol: "record.circle", foreground: Theme.brand, background: Theme.brandSoft)
        } else {
            Text(verbatim: "—").foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func noteCell(_ row: SessionRow) -> some View {
        if let text = row.note, !text.isEmpty {
            HStack(spacing: 6) {
                Text(verbatim: text).lineLimit(2).help(text)
                if reports.canEditNote(row) {
                    Button { editing = row } label: { Image(systemName: "pencil").font(.system(size: 11)) }
                        .buttonStyle(.plain).foregroundStyle(.secondary).help(L("Edit note")).accessibilityLabel(Text(verbatim: L("Edit note")))
                }
            }
        } else if reports.canEditNote(row) {
            Button(L("Add note")) { editing = row }.linkButton()
        } else {
            Text(verbatim: "—").foregroundStyle(.secondary)
        }
    }

    private func save(_ data: Data, name: String) {
        Task {
            guard let path = await app.chooseSaveLocation(name) else { return }
            do { try data.write(to: URL(fileURLWithPath: path), options: .atomic) } catch { reports.error = AppModel.message(error) }
        }
    }
}
#endif
