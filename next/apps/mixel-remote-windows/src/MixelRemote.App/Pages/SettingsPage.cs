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

    protected override UIElement Build() => Page(L("Settings"), "", 860,
        General(), ThisComputer(), SignInScreen(), Printing(), Updates(), Shortcuts(), About(), Ui.Message(Settings.Error), Ui.Message(Settings.Notice, error: false));

    private Border General()
    {
        var language = Ui.Choice([("auto", L("Automatic (this computer’s language)")), ("en", "English"), ("de", "Deutsch"), ("fr", "Français"), ("it", "Italiano")],
            Settings.Language, value => Settings.SetLanguage(value ?? "auto"));
        return Ui.Card(
            Ui.CardTitle(L("General"), "\uE713"),
            Ui.Switch(L("Stay signed in on this computer"), Settings.Startup?.Remembered ?? false, value => _ = Settings.SetStaySignedIn(value), App.SignedIn),
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
            ("Ctrl+1 … Ctrl+4", L("Open Remote control, Devices, Account or Settings")),
            ("F5", L("Refresh devices")),
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
