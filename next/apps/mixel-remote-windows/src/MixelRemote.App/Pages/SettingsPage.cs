using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MixelRemote.App.Shell;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>
/// Settings: stay signed in, start with Windows, language, automatic updates; this computer (remove it);
/// sign-in screen and always-on access; remote printing; updates; keyboard shortcuts; About and legal.
/// </summary>
internal sealed class SettingsPage : PageView
{
    private string removalResult = "";

    public SettingsPage(AppModel app) : base(app)
    {
        Watch(app.Settings);
        Watch(app.Enrollment);
        Loaded += (_, _) => _ = app.Settings.Load();
    }

    private SettingsModel Settings => App.Settings;

    private string current = "sessions";

    private static readonly (string Key, string Glyph)[] Sections =
    [
        ("sessions", "\uE7C4"), ("unattended", "\uE823"), ("updates", "\uE895"), ("general", "\uE713"), ("account", "\uE77B"), ("about", "\uE946"),
    ];

    private static string SectionTitle(string key) => key switch
    {
        "sessions" => L("Sessions"),
        "unattended" => L("Unattended access"),
        "updates" => L("Updates"),
        "general" => L("General"),
        "account" => L("Account & security"),
        _ => L("About and legal"),
    };

    protected override UIElement Build()
    {
        var title = new TextBlock { Text = L("Settings"), FontSize = 26, FontWeight = Microsoft.UI.Text.FontWeights.Bold };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetHeadingLevel(title, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level1);
        var nav = Ui.Column(2);
        foreach (var (key, glyph) in Sections)
        {
            var selected = key == current;
            var row = Ui.Row(10, Ui.Icon(glyph, 15, selected ? Ui.BrandBrush : null),
                new TextBlock { Text = SectionTitle(key), FontWeight = selected ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal, Foreground = selected ? Ui.BrandBrush : Ui.Brush("TextFillColorPrimaryBrush") });
            var button = new Button
            {
                Content = row, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Left, Padding = new Thickness(12, 9, 12, 9),
                Background = selected ? Ui.Brush("MixelBrandSoft") : new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent), BorderThickness = new Thickness(0),
            };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, SectionTitle(key));
            if (selected) Microsoft.UI.Xaml.Automation.AutomationProperties.SetItemStatus(button, L("Selected"));
            var chosen = key;
            button.Click += (_, _) => { current = chosen; Rebuild(); };
            nav.Children.Add(button);
        }
        nav.Width = 220;
        UIElement[] cards = current switch
        {
            "sessions" => [SessionsCard(), Shortcuts()],
            "unattended" => [UnattendedCard(), SignInScreen()!],
            "updates" => [Updates()],
            "general" => [General(), Appearance(), ThisComputer(), Printing()!],
            "account" => [AccountCard()],
            _ => [About()],
        };
        var content = new StackPanel { Spacing = 16, MaxWidth = 760, HorizontalAlignment = HorizontalAlignment.Left };
        foreach (var card in cards) if (card is not null) content.Children.Add(card);
        if (Ui.Message(Settings.Error) is { } error) content.Children.Add(error);
        if (Ui.Message(Settings.Notice, error: false) is { } notice) content.Children.Add(notice);
        var grid = new Grid { ColumnSpacing = 28, Padding = new Thickness(28, 22, 28, 28) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var header = Ui.Column(4, title, Ui.Text(L("Version") + " " + App.Info.Version, secondary: true));
        header.Margin = new Thickness(0, 0, 0, 16);
        Grid.SetColumnSpan(header, 2);
        grid.Children.Add(header);
        Grid.SetRow(nav, 1);
        grid.Children.Add(nav);
        var scroller = new ScrollViewer { Content = content, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        Grid.SetRow(scroller, 1);
        Grid.SetColumn(scroller, 1);
        grid.Children.Add(scroller);
        return new Border { Child = grid, Background = Ui.Brush("MixelPaper") };
    }

    /// <summary>How new sessions open: "Open sessions in" and "When a session is ready" (remembered across restarts).</summary>
    private Border SessionsCard()
    {
        var workspace = App.Workspace;
        RadioButtons Choice(string name, IEnumerable<(string Label, string Help)> items, int selected, Action<int> chosen)
        {
            var list = new RadioButtons { Header = name };
            foreach (var (label, help) in items)
            {
                var item = new RadioButton { Content = Ui.Column(1, Ui.Text(label), Ui.Caption(help)) };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(item, label);
                list.Items.Add(item);
            }
            list.SelectedIndex = selected;
            list.SelectionChanged += (_, _) => { if (list.SelectedIndex >= 0 && list.SelectedIndex != selected) chosen(list.SelectedIndex); };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(list, name);
            return list;
        }
        return Ui.Card(
            Ui.CardTitle(L("Sessions"), "\uE7C4"),
            Ui.Text(L("How new remote sessions open. You can always move a session between tab and window."), secondary: true),
            Choice(L("Open sessions in"), [(L("A tab in this window"), L("All sessions in one place.")), (L("Its own window"), L("Best with several monitors; drag it where you want."))],
                workspace.OpenIn == OpenIn.Tab ? 0 : 1, index => workspace.OpenIn = index == 0 ? OpenIn.Tab : OpenIn.Window),
            Choice(L("When a session is ready"), [(L("Stay in Devices and show “Open”"), L("You keep working; a notice offers “Open”.")), (L("Switch to the session"), L("The session comes to the front at once."))],
                workspace.WhenReady == WhenReady.Stay ? 0 : 1, index => workspace.WhenReady = index == 0 ? WhenReady.Stay : WhenReady.Open),
            Ui.Caption(L("The picture adapts to the line by itself.")));
    }

    /// <summary>Unattended access on this computer; the full set-up (display, confirmation) is under Remote control.</summary>
    private Border UnattendedCard()
    {
        var remote = App.Remote;
        var state = remote.UnattendedPending == "enabling" ? L("Turning on. Waiting for the connection service.")
            : remote.UnattendedPending is not null ? L("Sharing is off. Confirming with the connection service.")
            : remote.UnattendedEnabled ? L("On. Approved users can connect while this computer stays signed in.")
            : L("Off. Choose a display and confirm before enabling.");
        var items = new List<UIElement?>
        {
            Ui.CardTitle(L("Unattended access"), "\uE823"),
            Ui.Text(L("Lets approved technicians of your company connect to this computer without someone confirming."), secondary: true),
            Ui.Status(remote.UnattendedEnabled ? Ui.Badge.Online : Ui.Badge.Offline, state),
        };
        if (App.SignedIn && !remote.ControllerOnly)
        {
            items.Add(remote.UnattendedEnabled
                ? Ui.Plain(L("Turn off unattended sharing"), () => _ = App.Host.DisableUnattended(), !App.Busy)
                : Ui.Plain(L("Set up unattended sharing…"), () => App.Section = AppSection.Remote));
        }
        return Ui.Card([.. items]);
    }

    /// <summary>Settings > General > Appearance: System, Light or Dark.</summary>
    private Border Appearance()
    {
        var list = new RadioButtons { Header = L("Appearance"), MaxColumns = 3 };
        string[] keys = ["system", "light", "dark"];
        foreach (var label in new[] { L("System"), L("Light"), L("Dark") })
        {
            var item = new RadioButton { Content = label };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(item, label);
            list.Items.Add(item);
        }
        list.SelectedIndex = Math.Max(0, Array.IndexOf(keys, Settings.Appearance));
        list.SelectionChanged += (_, _) => { if (list.SelectedIndex >= 0) Settings.Appearance = keys[list.SelectedIndex]; };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(list, L("Appearance"));
        return Ui.Card(Ui.CardTitle(L("Appearance"), "\uE790"), list);
    }

    /// <summary>Account & security: who is signed in, staying signed in, sign out.</summary>
    private Border AccountCard()
    {
        var profile = App.Account.Account;
        var items = new List<UIElement?> { Ui.CardTitle(L("Account & security"), "\uE77B") };
        if (!App.SignedIn || profile is null)
        {
            items.Add(Ui.Text(L("Sign in to get your connection ID."), secondary: true));
            items.Add(Ui.Primary(L("Sign in"), () => App.Section = AppSection.Account));
        }
        else
        {
            items.Add(Ui.Text(profile.DisplayName ?? L("Signed in"), bold: true));
            items.Add(Ui.Switch(L("Stay signed in on this computer"), Settings.Startup?.Remembered ?? false, value => _ = Settings.SetStaySignedIn(value)));
            items.Add(Ui.Row(8, Ui.Plain(L("Open account"), () => App.Section = AppSection.Account), Ui.Plain(L("Sign out"), () => _ = App.SignOut())));
        }
        return Ui.Card([.. items]);
    }

    private Border General()
    {
        var language = Ui.Choice([("auto", L("Automatic (this computer’s language)")), ("en", "English"), ("de", "Deutsch"), ("fr", "Français"), ("it", "Italiano")],
            Settings.Language, value => Settings.SetLanguage(value ?? "auto"));
        return Ui.Card(
            Ui.CardTitle(L("General"), "\uE713"),
            Ui.Switch(L("Start Mixel Remote when this computer starts"), Settings.Startup?.Autostart ?? false, value => _ = Settings.SetAutostart(value)),
            Ui.Switch(L("Install updates automatically"), Settings.AutoUpdate, value => Settings.AutoUpdate = value),
            Ui.Field(L("Language"), language));
    }

    private Border ThisComputer()
    {
        var enrollment = App.Enrollment;
        var id = Ui.Selectable(Format.RemoteId(App.Remote.Host ?? enrollment.Current?.ConnectionId), 18);
        UIElement action;
        if (Settings.RemovalPending) action = Ui.Message(L("This computer is being removed from Mixel Remote. It is removed as soon as it is online."), error: false)!;
        else if (enrollment.Current is not null || App.Remote.Host is not null) action = Ui.Plain(L("Remove this computer from Mixel Remote"), () => _ = Remove(), !enrollment.Working);
        else action = Ui.Text(L("This computer is not set up yet."), secondary: true);
        return Ui.Card(Ui.CardTitle(L("This computer"), "\uE7F4"), Ui.Field(L("Connection ID"), id), action, Ui.Message(removalResult, error: false), Ui.Message(enrollment.Error));
    }

    private async Task Remove()
    {
        if (!await Dialogs.Confirm(Root, L("Remove this computer from Mixel Remote?"), L("It can no longer be reached or connect until it is added again."), L("Remove this computer"))) return;
        removalResult = await App.Enrollment.RemoveThisComputer() switch
        {
            SelfRemovalStatus.Removed => L("This computer was removed from Mixel Remote."),
            SelfRemovalStatus.Queued => L("Saved. This computer is removed as soon as Mixel Remote can be reached."),
            SelfRemovalStatus.Stale => L("This computer's registration changed since; nothing was removed."),
            _ => "",
        };
        Rebuild();
    }

    private Border? SignInScreen()
    {
        if (Settings.SignInScreen is not { Supported: true } screen) return null;
        var items = new List<UIElement?>
        {
            Ui.CardTitle(L("Sign-in screen access"), "\uE72E"),
            Ui.Text(L("Lets approved technicians connect while nobody is signed in to Windows, and keeps this computer reachable at all times (always-on access, Ctrl+Alt+Del). Needs two administrator approvals."), secondary: true),
        };
        if (screen.ConnectionId is { } connection)
        {
            items.Add(Ui.Status(Ui.Badge.Online, L("On · Remote ID %@", Format.RemoteId(connection))));
            if (screen.Name is { } name) items.Add(Ui.Caption(name));
            if (screen.Technician) items.Add(Ui.Message(L("A technician is connected through the sign-in screen access."), error: false));
            items.Add(Ui.Row(8,
                screen.Outdated ? Ui.Primary(L("Update sign-in screen access"), () => _ = Settings.UpdateSignInScreen(), !Settings.Working) : null,
                Ui.Plain(L("Turn off"), () => _ = Settings.DisableSignInScreen(), !Settings.Working)));
        }
        else if (App.Account.Account?.Staff == true)
        {
            items.Add(Ui.Primary(L("Turn on…"), () => _ = EnableSignInScreen(), !Settings.Working && App.SignedIn));
        }
        else
        {
            items.Add(Ui.Text(L("Off. Your administrator turns it on for company computers with a deploy link."), secondary: true));
        }
        if (Settings.Working) items.Add(Ui.Spinner());
        return Ui.Card([.. items]);
    }

    private async Task EnableSignInScreen()
    {
        List<EnrollmentCompany> companies;
        try { companies = await App.Engine.RunAsync<List<EnrollmentCompany>>("enrollment_companies"); }
        catch (EngineFailure failure)
        {
            Settings.Error = failure.Translated;
            return;
        }
        if (companies.Count == 0) return;
        var company = companies[0].ClientId;
        var choice = Ui.Field(L("Company"), Ui.Choice(companies.Select(item => (item.ClientId, item.ClientName)), company, value => company = value ?? company));
        await Dialogs.Form(Root, L("Sign-in screen access"), L("Windows asks twice for administrator approval."), [(L("Computer name"), App.Enrollment.Name)], L("Turn on"),
            async values =>
            {
                await Settings.EnableSignInScreen(company, values[0].Trim());
                return Settings.Error.Length > 0 ? Settings.Error : null;
            }, extra: choice);
    }

    private Border? Printing()
    {
        if (Settings.Printing is not { Supported: true } printing) return null;
        return Ui.Card(
            Ui.CardTitle(L("Remote printing"), "\uE749"),
            Ui.Text(L("Adds the printer “Mixel Remote Printer” to this computer. What is printed on it during a session arrives at the technician, who prints it on a printer there. Needs administrator approval on this computer."), secondary: true),
            Ui.Switch(L("Remote printing"), printing.Installed, value => _ = Settings.SetPrinting(value), !Settings.Working));
    }

    private Border Updates()
    {
        var status = new List<UIElement?>();
        if (Settings.Installing) status.Add(Ui.Row(8, Ui.Spinner(), Ui.Text(L("Installing Mixel Remote %@… The app restarts by itself.", Settings.Update?.Version ?? ""))));
        else if (Settings.Update is { } update)
        {
            status.Add(Ui.Text(L("Version %@ is available.", update.Version)));
            if (update.WaitsForSession) status.Add(Ui.Text(L("Update ready, installs after this session"), secondary: true));
            status.Add(update.Installable && !Settings.InstallFailed
                ? Ui.Primary(L("Update now"), () => _ = Settings.InstallUpdate())
                : Ui.Plain(L("Download"), Settings.OpenStorePage));
        }
        else if (Settings.UpdateChecked) status.Add(Ui.Text(L("This is the newest version."), secondary: true));
        return Ui.Card(
            Ui.CardTitle(L("Updates"), "\uE895"),
            Ui.Text(L("Version") + " " + App.Info.Version, secondary: true),
            Ui.Row(12, Ui.Plain(L("Check for updates"), () => _ = Settings.CheckForUpdate(), !Settings.Working && !Settings.Installing)),
            Ui.Column(8, [.. status]));
    }

    private static Border Shortcuts()
    {
        var grid = new Grid { ColumnSpacing = 24, RowSpacing = 6 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        (string Keys, string What)[] rows =
        [
            ("Ctrl+1 … Ctrl+4", L("Open Devices, Sessions, Reports or Settings")),
            ("F5", L("Refresh devices")),
            ("/", L("Search the devices")),
            ("Ctrl+T", L("Connect by ID")),
            ("Ctrl+Alt+End", L("Ctrl+Alt+Del on the remote computer")),
            ("Ctrl+Alt+Home", L("Give the keyboard back to this computer during a session")),
            ("Ctrl+Alt+Enter", L("Full screen on or off during a session")),
            ("Ctrl+Alt+Q", L("End the session")),
        ];
        for (var index = 0; index < rows.Length; index++)
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var keys = Ui.Text(rows[index].Keys, bold: true);
            Grid.SetRow(keys, index);
            grid.Children.Add(keys);
            var what = Ui.Text(rows[index].What, secondary: true);
            Grid.SetRow(what, index);
            Grid.SetColumn(what, 1);
            grid.Children.Add(what);
        }
        return Ui.Card(Ui.CardTitle(L("Keyboard shortcuts"), "\uE765"), grid,
            Ui.Caption(L("During a session every other key, Windows key and Alt+Tab included, goes to the remote computer.")));
    }

    private Border About()
    {
        var links = Ui.Row(16,
            Ui.Link(L("Terms of Service"), () => _ = Settings.OpenLegal("terms")),
            Ui.Link(L("Privacy Policy"), () => _ = Settings.OpenLegal("privacy")),
            Ui.Link(L("Data Processing Agreement"), () => _ = Settings.OpenLegal("dpa")),
            Ui.Link(L("Sub-processors"), () => _ = Settings.OpenLegal("subprocessors")));
        var mark = new Image { Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-mark.png"))), Width = 44, Height = 44 };
        return Ui.Card(
            Ui.CardTitle(L("About and legal"), "\uE946"),
            Ui.Row(12, mark, Ui.Column(2, Ui.Text("Mixel Remote", size: 18, bold: true), Ui.Text(L("Version") + " " + App.Info.Version, secondary: true))),
            Ui.Caption("© Mixel IT and Corporate Services"),
            links);
    }
}
