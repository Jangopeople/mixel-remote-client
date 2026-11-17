using System.Globalization;
using System.Text;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>
/// Session reports of a company (plans with reports): period, technician and device filters, a note per
/// session, CSV and PDF export. Same behaviour as the Mac app's ReportsModel.
/// </summary>
public sealed class ReportsModel : Observable
{
    private readonly AppModel app;
    private DateOnly from;
    private DateOnly to;
    private string? technician;
    private string? device;
    private List<SessionRow> rows = [];
    private bool loading;
    private bool needsPlan;
    private string error = "";

    internal ReportsModel(AppModel app)
    {
        this.app = app;
        to = DateOnly.FromDateTime(DateTime.Now);
        from = to.AddDays(-30);
    }

    public DateOnly From { get => from; set => Set(ref from, value); }
    public DateOnly To { get => to; set => Set(ref to, value); }
    public string? Technician { get => technician; set => Set(ref technician, value); }
    public string? Device { get => device; set => Set(ref device, value); }
    public List<SessionRow> Rows { get => rows; private set => SetList(ref rows, value); }
    public bool Loading { get => loading; private set => Set(ref loading, value); }
    public bool NeedsPlan { get => needsPlan; private set => Set(ref needsPlan, value); }
    public string Error { get => error; set => Set(ref error, value); }

    public Tenant? Tenant => app.Company.Tenant;
    /// <summary>The company's plan includes reports.</summary>
    public bool Included => app.Company.Overview?.Features?.GetValueOrDefault("reports") ?? false;
    public bool Manager => app.Company.Role is "owner" or "admin";

    /// <summary>Owners and admins edit any note; the others the notes of their own sessions.</summary>
    public bool CanEditNote(SessionRow row)
    {
        if (Manager) return true;
        if (app.Account.Account?.Profile is not { } mine || row.TechnicianId is not { } technicianId) return true;
        return technicianId == mine;
    }

    public static string Day(DateOnly date) => date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    public async Task Load()
    {
        if (Tenant is not { } tenant)
        {
            Rows = [];
            return;
        }
        Loading = true;
        Error = "";
        try
        {
            // The end of the period includes that whole day.
            Rows = await app.Engine.RunAsync<List<SessionRow>>("saas_session_report",
                ("tenant", tenant.TenantId), ("from", Day(From)), ("to", Day(To.AddDays(1))), ("technician", Technician), ("device", Device));
            NeedsPlan = false;
        }
        catch (EngineFailure failure)
        {
            if (failure.Message.Contains("session reports need a paid plan", StringComparison.OrdinalIgnoreCase))
            {
                NeedsPlan = true;
                Rows = [];
            }
            else Error = AppModel.Message(failure);
        }
        finally
        {
            Loading = false;
        }
    }

    public async Task<bool> SetNote(SessionRow row, string text)
    {
        if (Tenant is not { } tenant) return false;
        try
        {
            await app.Engine.PerformAsync("saas_session_note", ("tenant", tenant.TenantId), ("note", text), ("session", row.SessionId));
            await Load();
            return true;
        }
        catch (EngineFailure failure)
        {
            Error = AppModel.Message(failure);
            return false;
        }
    }

    public static string Duration(long seconds)
    {
        var hours = seconds / 3600;
        var minutes = seconds % 3600 / 60;
        var rest = seconds % 60;
        return hours > 0 ? string.Create(CultureInfo.InvariantCulture, $"{hours}:{minutes:00}:{rest:00}") : string.Create(CultureInfo.InvariantCulture, $"{minutes}:{rest:00}");
    }

    public static string Stamp(string? iso) =>
        Format.ParseDate(iso) is { } date ? date.ToLocalTime().ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture) : iso ?? "";

    public static string KindLabel(SessionRow row) => (row.Kind == "unattended" ? L("Unattended") : L("Attended")) + (row.Colleague ? " · " + L("Colleague") : "");

    public static string[] Columns => [L("Start"), L("End"), L("Duration"), L("Technician"), L("Device"), L("Remote ID"), L("Kind"), L("Note")];

    public static string[] Cells(SessionRow row) =>
        [Stamp(row.StartedAt), Stamp(row.EndedAt), Duration(row.DurationSeconds), row.TechnicianName ?? row.TechnicianEmail ?? "",
         row.DeviceName, Format.RemoteId(row.ConnectionId), KindLabel(row), row.Note ?? ""];

    /// <summary>CSV (UTF-8 with BOM for spreadsheet apps, CRLF lines); cells that could start a formula are quoted with '.</summary>
    public string Csv()
    {
        static string Cell(string value)
        {
            var text = value;
            if (text.Length > 0 && "=+-@".Contains(text[0], StringComparison.Ordinal)) text = "'" + text;
            return text.IndexOfAny([',', '"', '\n', '\r']) >= 0 ? "\"" + text.Replace("\"", "\"\"", StringComparison.Ordinal) + "\"" : text;
        }
        var lines = new List<string> { string.Join(',', Columns.Select(Cell)) };
        lines.AddRange(Rows.Select(row => string.Join(',', Cells(row).Select(Cell))));
        return "﻿" + string.Join("\r\n", lines) + "\r\n";
    }

    public string FileStem
    {
        get
        {
            var slug = new string([.. (Tenant?.Slug ?? "mixel-remote").Where(character => char.IsAsciiLetterOrDigit(character) || character == '-')]);
            return $"sessions-{(slug.Length == 0 ? "mixel-remote" : slug)}-{Day(From)}-{Day(To)}";
        }
    }

    /// <summary>A landscape A4 PDF of the report (title, period, totals, the table; paginated).</summary>
    public byte[] Pdf() => ReportPdf.Build(L("Session reports") + " · " + (Tenant?.Name ?? ""),
        $"{Day(From)} – {Day(To)} · {L("%d sessions", Rows.Count)} · {Duration(Rows.Sum(row => row.DurationSeconds))}", Columns, [.. Rows.Select(Cells)]);
}

/// <summary>A minimal PDF writer: Helvetica text in a table on landscape A4 pages, no dependencies.</summary>
public static class ReportPdf
{
    private static readonly double[] Widths = [88, 88, 54, 120, 130, 78, 90, 122];

    public static byte[] Build(string title, string subtitle, string[] columns, List<string[]> rows)
    {
        const double margin = 36, rowHeight = 16, pageWidth = 842, pageHeight = 595;
        var pages = new List<string>();
        var index = 0;
        do
        {
            var page = new StringBuilder();
            var y = pageHeight - margin - 14;
            Text(page, title, margin, y, 14, bold: true, 700);
            y -= 16;
            Text(page, subtitle, margin, y, 9, bold: false, 700);
            y -= 22;
            var x = margin;
            for (var column = 0; column < columns.Length; column++)
            {
                Text(page, columns[column], x, y, 8, bold: true, Widths[column]);
                x += Widths[column];
            }
            y -= rowHeight;
            while (index < rows.Count && y > margin)
            {
                x = margin;
                for (var column = 0; column < columns.Length; column++)
                {
                    Text(page, rows[index][column], x, y, 8, bold: false, Widths[column]);
                    x += Widths[column];
                }
                y -= rowHeight;
                index++;
            }
            pages.Add(page.ToString());
        }
        while (index < rows.Count);

        // Objects: 1 catalog, 2 pages, 3 Helvetica, 4 Helvetica-Bold, then a page and its content per page.
        var objects = new List<string>
        {
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + string.Join(' ', pages.Select((_, page) => $"{5 + page * 2} 0 R")) + $"] /Count {pages.Count} >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        };
        for (var page = 0; page < pages.Count; page++)
        {
            objects.Add(string.Create(CultureInfo.InvariantCulture, $"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pageWidth} {pageHeight}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + page * 2} 0 R >>"));
            objects.Add(string.Create(CultureInfo.InvariantCulture, $"<< /Length {Encoding.Latin1.GetByteCount(pages[page])} >>\nstream\n{pages[page]}\nendstream"));
        }
        var output = new MemoryStream();
        void Write(string text) => output.Write(Encoding.Latin1.GetBytes(text));
        Write("%PDF-1.4\n");
        var offsets = new List<long>();
        for (var number = 0; number < objects.Count; number++)
        {
            offsets.Add(output.Length);
            Write($"{number + 1} 0 obj\n{objects[number]}\nendobj\n");
        }
        var xref = output.Length;
        Write($"xref\n0 {objects.Count + 1}\n0000000000 65535 f \n");
        foreach (var offset in offsets) Write(string.Create(CultureInfo.InvariantCulture, $"{offset:0000000000} 00000 n \n"));
        Write($"trailer\n<< /Size {objects.Count + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n");
        return output.ToArray();
    }

    private static void Text(StringBuilder page, string text, double x, double y, double size, bool bold, double width)
    {
        // About half the font size per character; cut long cells with an ellipsis.
        var fit = Math.Max(1, (int)((width - 4) / (size * 0.52)));
        var shown = text.Replace('\n', ' ').Replace('\r', ' ');
        if (shown.Length > fit) shown = shown[..Math.Max(1, fit - 2)] + "..";
        var bytes = Encoding.Latin1.GetBytes(shown);
        var escaped = new StringBuilder();
        foreach (var value in bytes)
        {
            var character = (char)value;
            if (character is '(' or ')' or '\\') escaped.Append('\\');
            escaped.Append(character);
        }
        page.Append(CultureInfo.InvariantCulture, $"BT /{(bold ? "F2" : "F1")} {size} Tf {x:0.##} {y:0.##} Td ({escaped}) Tj ET\n");
    }
}
