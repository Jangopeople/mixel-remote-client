using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using MixelRemote.App.Shell;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>
/// Session reports of a company (plans with reports): period, technician and device filters, the table with a
/// note per session, CSV and PDF export.
/// </summary>
internal sealed class ReportsPage : PageView
{
    private bool loaded;
    private string saved = "";

    public ReportsPage(AppModel app) : base(app)
    {
        Watch(app.Reports);
        Watch(app.Company);
    }

    private ReportsModel Reports => App.Reports;

    protected override UIElement Build()
    {
        if (!loaded && App.SignedIn && Reports.Tenant is not null && Reports.Included)
        {
            loaded = true;
            _ = Reports.Load();
        }
        var title = new TextBlock { Text = L("Session reports"), FontSize = 26, FontWeight = Microsoft.UI.Text.FontWeights.Bold };
        AutomationProperties.SetHeadingLevel(title, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level1);
        var stack = new StackPanel { Spacing = 14, Padding = new Thickness(28, 22, 28, 28) };
        stack.Children.Add(title);
        if (Reports.Tenant is null)
        {
            stack.Children.Add(Ui.Text(L("Session reports are available for companies."), secondary: true));
            return Wrap(stack);
        }
        if (!Reports.Included || Reports.NeedsPlan)
        {
            stack.Children.Add(Ui.Card(Ui.CardTitle(L("Session reports"), ""),
                Ui.Text(L("Session reports need a plan that includes them. Ask an owner of your company to upgrade the plan."), secondary: true)));
            return Wrap(stack);
        }
        stack.Children.Add(Ui.Text(Reports.Manager ? L("Every session of your company's technicians.") : L("Your own sessions."), secondary: true));
        stack.Children.Add(Filters());
        // Ui.Message is null when there is nothing to say; adding null to the panel throws and left the page empty.
        if (Ui.Message(Reports.Error) is { } failure) stack.Children.Add(failure);
        if (Ui.Message(saved, error: false) is { } done) stack.Children.Add(done);
        stack.Children.Add(Table());
        return Wrap(stack);
    }

    private static UIElement Wrap(UIElement content) => new Border { Child = new ScrollViewer { Content = content, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled }, Background = Ui.Brush("MixelPaper") };

    private static CalendarDatePicker Date(string name, DateOnly value, Action<DateOnly> changed)
    {
        var picker = new CalendarDatePicker { Date = new DateTimeOffset(value.ToDateTime(TimeOnly.MinValue)), DateFormat = "{year.full}-{month.integer(2)}-{day.integer(2)}", MinWidth = 150 };
        AutomationProperties.SetName(picker, name);
        picker.DateChanged += (_, args) =>
        {
            if (args.NewDate is { } date && DateOnly.FromDateTime(date.Date) != value) changed(DateOnly.FromDateTime(date.Date));
        };
        return picker;
    }

    private UIElement Filters()
    {
        var reports = Reports;
        var technicians = reports.Rows.Select(row => (row.TechnicianId, Name: row.TechnicianName ?? row.TechnicianEmail ?? "")).Where(item => item.TechnicianId is not null).DistinctBy(item => item.TechnicianId).ToList();
        var devices = reports.Rows.Select(row => (row.DeviceId, row.DeviceName)).DistinctBy(item => item.DeviceId).ToList();
        var bar = Ui.Row(12,
            Ui.Field(L("From"), Date(L("From"), reports.From, value => reports.From = value)),
            Ui.Field(L("To"), Date(L("To"), reports.To, value => reports.To = value)),
            reports.Manager ? Ui.Field(L("Technician"), Ui.Choice([("", L("All technicians")), .. technicians.Select(item => (item.TechnicianId!, item.Name))], reports.Technician ?? "", value => reports.Technician = string.IsNullOrEmpty(value) ? null : value, 180)) : null,
            Ui.Field(L("Device"), Ui.Choice([("", L("All devices")), .. devices.Select(item => (item.DeviceId, item.DeviceName))], reports.Device ?? "", value => reports.Device = string.IsNullOrEmpty(value) ? null : value, 180)));
        var apply = Ui.Primary(L("Show"), () => _ = Reports.Load(), !reports.Loading);
        var csv = Ui.Plain(L("Export CSV…"), () => _ = Export(csv: true), reports.Rows.Count > 0, "");
        var pdf = Ui.Plain(L("Export PDF…"), () => _ = Export(csv: false), reports.Rows.Count > 0, "");
        var totals = L("%d sessions", reports.Rows.Count) + " · " + ReportsModel.Duration(reports.Rows.Sum(row => row.DurationSeconds));
        var row2 = Ui.Row(8, apply, csv, pdf, reports.Loading ? Ui.Spinner() : Ui.Text(totals, secondary: true, wrap: false));
        row2.VerticalAlignment = VerticalAlignment.Center;
        return Ui.Column(10, new ScrollViewer { Content = bar, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollMode = ScrollMode.Auto, VerticalScrollMode = ScrollMode.Disabled }, row2);
    }

    private static readonly double[] Widths = [130, 130, 70, 150, 170, 112, 130];

    private UIElement Table()
    {
        var reports = Reports;
        if (reports.Rows.Count == 0)
        {
            return Ui.Text(reports.Loading ? L("Loading…") : L("No sessions in this period."), secondary: true);
        }
        var table = new StackPanel { Spacing = 0 };
        Grid RowGrid()
        {
            var grid = new Grid { ColumnSpacing = 10, Padding = new Thickness(12, 9, 12, 9) };
            foreach (var width in Widths) grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(width) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star), MinWidth = 160 });
            return grid;
        }
        var header = RowGrid();
        var columns = ReportsModel.Columns;
        for (var index = 0; index < columns.Length; index++)
        {
            var label = Ui.Label(columns[index]);
            Grid.SetColumn(label, index);
            header.Children.Add(label);
        }
        table.Children.Add(header);
        table.Children.Add(Ui.Hairline());
        foreach (var row in reports.Rows)
        {
            var grid = RowGrid();
            var cells = ReportsModel.Cells(row);
            for (var index = 0; index < cells.Length - 1; index++)
            {
                var text = Ui.Text(cells[index], secondary: index is 0 or 1 or 2, wrap: false);
                if (index == 5) text.FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Cascadia Mono, Consolas");
                Grid.SetColumn(text, index);
                grid.Children.Add(text);
            }
            var note = Ui.Row(8, Ui.Text(row.Note ?? "", secondary: true, wrap: false));
            if (reports.CanEditNote(row)) note.Children.Add(Ui.Link(L("Note…"), () => _ = Note(row)));
            Grid.SetColumn(note, cells.Length - 1);
            grid.Children.Add(note);
            AutomationProperties.SetName(grid, $"{cells[0]}, {cells[3]}, {cells[4]}, {cells[2]}");
            table.Children.Add(grid);
            table.Children.Add(Ui.Hairline());
        }
        return new ScrollViewer { Content = table, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollMode = ScrollMode.Auto, VerticalScrollMode = ScrollMode.Disabled };
    }

    private Task Note(SessionRow row) => Dialogs.Form(Root, L("Session note"), $"{row.DeviceName} · {Format.RemoteId(row.ConnectionId)}", [(L("Note"), row.Note ?? "")], L("Save"),
        async values => await Reports.SetNote(row, values[0]) ? null : Reports.Error);

    private async Task Export(bool csv)
    {
        var name = Reports.FileStem + (csv ? ".csv" : ".pdf");
        if (await App.ChooseSaveLocation(name) is not { } path) return;
        if (csv) await File.WriteAllTextAsync(path, Reports.Csv(), new System.Text.UTF8Encoding(false));
        else await File.WriteAllBytesAsync(path, Reports.Pdf());
        saved = L("Saved %@", Path.GetFileName(path));
        Rebuild();
    }
}
