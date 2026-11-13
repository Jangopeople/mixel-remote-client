import SwiftUI

/// Session reports of the company: period, technician and device, a note per session, CSV and PDF.
public struct ReportsView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var reports: ReportsModel
    @State private var editing: SessionRow?
    @State private var exported: URL?

    public init(app: AppModel) {
        self.app = app
        reports = app.reports
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            PageHeader(L("Session reports"), subtitle: reports.tenant?.name ?? "")
            if reports.tenant == nil {
                Text(verbatim: L("Session reports are part of company plans. Create or join a company under Account.")).foregroundStyle(.secondary)
            } else if reports.needsPlan || !reports.included {
                MessageLine(L("Session reports need a paid plan with reports. Upgrade the plan in the portal."), error: false)
            } else {
                filters
                MessageLine(reports.error)
                table
            }
            Spacer(minLength: 0)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.background)
        .task { await reports.load() }
        .sheet(item: $editing) { row in
            FormSheet(title: L("Session note"), fields: [.init(id: "note", label: L("Note (optional)"), value: row.note ?? "")], confirm: L("Save note")) { values in
                await reports.setNote(row, values["note"] ?? "") ? nil : reports.error
            }
        }
    }

    private var filters: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { filterFields }
            VStack(alignment: .leading, spacing: 8) { filterFields }
        }
    }

    @ViewBuilder private var filterFields: some View {
        DatePicker(selection: $reports.from, displayedComponents: .date) { Text(verbatim: L("From")) }.fixedSize()
        DatePicker(selection: $reports.to, displayedComponents: .date) { Text(verbatim: L("To")) }.fixedSize()
        if reports.manager {
            Picker(selection: $reports.technician) {
                Text(verbatim: L("All technicians")).tag(String?.none)
                ForEach(app.company.members) { Text(verbatim: $0.name.isEmpty ? $0.email : $0.name).tag(Optional($0.userId)) }
            } label: { Text(verbatim: L("Technician")) }.fixedSize()
        }
        Button(L("Show")) { Task { await reports.load() } }.disabled(reports.loading)
        Spacer(minLength: 0)
        exportButtons
    }

    @ViewBuilder private var exportButtons: some View {
        #if os(macOS)
        Button(L("Export CSV")) { save(Data(reports.csv().utf8), name: reports.fileStem + ".csv") }.disabled(reports.rows.isEmpty)
        Button(L("Export PDF")) { save(reports.pdf(), name: reports.fileStem + ".pdf") }.disabled(reports.rows.isEmpty)
        #else
        if !reports.rows.isEmpty {
            ShareLink(item: temporary(Data(reports.csv().utf8), name: reports.fileStem + ".csv")) { Text(verbatim: L("Export CSV")) }
            ShareLink(item: temporary(reports.pdf(), name: reports.fileStem + ".pdf")) { Text(verbatim: L("Export PDF")) }
        }
        #endif
    }

    private func save(_ data: Data, name: String) {
        Task {
            guard let path = await app.chooseSaveLocation(name) else { return }
            do { try data.write(to: URL(fileURLWithPath: path), options: .atomic) } catch { reports.error = AppModel.message(error) }
        }
    }

    private func temporary(_ data: Data, name: String) -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        try? data.write(to: url, options: .atomic)
        return url
    }

    @ViewBuilder private var table: some View {
        if reports.rows.isEmpty {
            Text(verbatim: reports.loading ? L("Loading…") : L("No sessions in this period.")).foregroundStyle(.secondary)
        } else {
            let total = reports.rows.reduce(0) { $0 + $1.durationSeconds }
            Text(verbatim: L("%d sessions", reports.rows.count) + " · " + ReportsModel.duration(total)).font(.callout).foregroundStyle(.secondary)
            List(reports.rows) { row in
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: row.deviceName).font(.body.weight(.semibold))
                        Text(verbatim: "\(ReportsModel.stamp(row.startedAt)) · \(ReportsModel.duration(row.durationSeconds)) · \(reports.kindLabel(row))")
                            .font(.callout).foregroundStyle(.secondary).monospacedDigit()
                        Text(verbatim: row.technicianName ?? row.technicianEmail ?? L("Deleted account")).font(.caption).foregroundStyle(.secondary)
                        if let note = row.note, !note.isEmpty { Text(verbatim: note).font(.callout).padding(.top, 2) }
                    }
                    Spacer()
                    if row.endedAt == nil { StatusBadge(.online, L("Running")) }
                    if reports.canEditNote(row) {
                        Button(row.note?.isEmpty == false ? L("Edit note") : L("Add note")) { editing = row }.controlSize(.small)
                    }
                }
                .padding(.vertical, 4)
            }
            #if os(macOS)
            .listStyle(.inset(alternatesRowBackgrounds: false))
            #endif
        }
    }
}

/// After a controlled session with a company device: an optional note for the session report.
public struct SessionNoteSheet: View {
    @ObservedObject var reports: ReportsModel
    let host: UInt32
    let name: String
    @State private var note = ""
    @State private var saving = false
    @Environment(\.dismiss) private var dismiss

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(verbatim: L("Session note")).font(.title2.weight(.semibold))
            Text(verbatim: L("Session with") + " " + name + ". " + L("Add a note? It appears in the session report of your company.")).foregroundStyle(.secondary)
            TextEditor(text: $note).frame(minHeight: 110).overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
            MessageLine(reports.error)
            HStack {
                Spacer()
                Button(L("Skip")) { reports.noteFor = nil; dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("Save note")) {
                    saving = true
                    Task {
                        if await reports.saveNote(String(note.prefix(4000)), for: host) { reports.noteFor = nil; dismiss() }
                        saving = false
                    }
                }
                .buttonStyle(PrimaryButtonStyle()).keyboardShortcut(.defaultAction).disabled(saving)
            }
        }
        .padding(22)
        .frame(minWidth: 420)
    }
}
