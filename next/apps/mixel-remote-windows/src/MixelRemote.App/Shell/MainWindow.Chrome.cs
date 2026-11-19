using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using MixelRemote.ViewModels;
using MixelRemote.Models;

namespace MixelRemote.App.Shell;

// The tab strip in the title bar, the sidebar and the notices bottom right.
internal sealed partial class MainWindow
{
    // ---- Title bar tabs ----

    private void BuildTabs()
    {
        var workspace = app.Workspace;
        tabs.Children.Clear();
        var mark = new Image { Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-mark.png"))), Width = 18, Height = 18, Margin = new Thickness(14, 0, 8, 10), VerticalAlignment = VerticalAlignment.Bottom };
        AutomationProperties.SetAccessibilityView(mark, Microsoft.UI.Xaml.Automation.Peers.AccessibilityView.Raw);
        tabs.Children.Add(mark);
        var devices = Ui.Row(7, Ui.Icon("", 14), new TextBlock { Text = L("Devices"), VerticalAlignment = VerticalAlignment.Center });
        tabs.Children.Add(Tab(devices, L("Devices"), workspace.Selected.IsConsole, () => workspace.Selected = WorkspaceTab.Console, null, null));
        foreach (var tab in workspace.Tabs)
        {
            var dot = tab.State == "ready" ? "MixelSuccess" : tab.State is "ending" or "failed" ? "TextFillColorTertiaryBrush" : "MixelWarning";
            var row = Ui.Row(7,
                new Microsoft.UI.Xaml.Shapes.Ellipse { Width = 7, Height = 7, Fill = Ui.Brush(dot), VerticalAlignment = VerticalAlignment.Center },
                Ui.OsMark(tab.Os, 13),
                new TextBlock { Text = tab.Name, MaxWidth = 150, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center });
            if (workspace.IsInWindow(tab.Peer)) row.Children.Add(Ui.Icon("", 12, Ui.Brush("MixelInfoText")));
            var peer = tab.Peer;
            var name = tab.Name;
            var element = Tab(row, L("Session with %@", name), workspace.Selected.Peer == peer, () => workspace.Show(peer, inWindow: false), () => _ = ConfirmEnd(peer, name), TabMenu(peer, name));
            tabs.Children.Add(element);
        }
        var plus = Ui.Flat(Ui.Icon("", 13), L("Connect by ID") + " (Ctrl+T)", ConnectById);
        plus.VerticalAlignment = VerticalAlignment.Center;
        plus.Margin = new Thickness(2, 0, 0, 4);
        tabs.Children.Add(plus);
    }

    private FrameworkElement Tab(UIElement label, string name, bool selected, Action select, Action? close, MenuFlyout? menu)
    {
        var button = new Button
        {
            Content = label, Background = new SolidColorBrush(Colors.Transparent), BorderThickness = new Thickness(0), Padding = new Thickness(12, 6, close is null ? 14 : 4, 6), CornerRadius = new CornerRadius(8, 8, 0, 0),
            Foreground = Ui.Brush(selected ? "TextFillColorPrimaryBrush" : "TextFillColorSecondaryBrush"), VerticalAlignment = VerticalAlignment.Stretch,
        };
        AutomationProperties.SetName(button, name);
        AutomationProperties.SetItemStatus(button, selected ? L("Selected") : "");
        button.Click += (_, _) => select();
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(button);
        if (close is not null)
        {
            var x = Ui.Flat(Ui.Icon("", 10), L("End session"), close);
            x.Padding = new Thickness(5);
            x.MinWidth = 0;
            x.MinHeight = 0;
            x.Margin = new Thickness(0, 0, 6, 0);
            row.Children.Add(x);
        }
        var frame = new Border
        {
            Child = row, Height = 34, VerticalAlignment = VerticalAlignment.Bottom, MaxWidth = 280, CornerRadius = new CornerRadius(8, 8, 0, 0),
            Background = selected ? Ui.Brush("MixelPaper") : new SolidColorBrush(Colors.Transparent),
            BorderBrush = Ui.Brush("MixelHairline"), BorderThickness = selected ? new Thickness(1, 1, 1, 0) : new Thickness(0),
        };
        if (menu is not null) frame.ContextFlyout = menu;
        return frame;
    }

    private MenuFlyout TabMenu(uint peer, string name)
    {
        var workspace = app.Workspace;
        var menu = new MenuFlyout();
        var move = new MenuFlyoutItem { Text = workspace.IsInWindow(peer) ? L("Move back into a tab") : L("Open in its own window") };
        move.Click += (_, _) => { if (workspace.IsInWindow(peer)) workspace.Dock(peer); else workspace.PopOut(peer); };
        menu.Items.Add(move);
        menu.Items.Add(new MenuFlyoutSeparator());
        var end = new MenuFlyoutItem { Text = L("End session") };
        end.Click += (_, _) => _ = ConfirmEnd(peer, name);
        menu.Items.Add(end);
        return menu;
    }

    private async Task ConfirmEnd(uint peer, string name)
    {
        if (root.XamlRoot is { } xaml && await Dialogs.Confirm(xaml, L("End the session with %@?", name), L("The connection ends for both sides."), L("End session"))) await app.Workspace.End(peer);
    }

    // ---- Sidebar ----

    private void BuildSidebar()
    {
        var workspace = app.Workspace;
        var devices = app.Devices;
        sidebarItems.Children.Clear();
        var company = app.Company.Tenant?.Name ?? (app.Account.Account?.Staff == true ? "Mixel IT" : "");
        var brand = Ui.Row(10,
            new Image { Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-mark.png"))), Width = 34, Height = 34 },
            Ui.Column(1, new TextBlock { Text = "Mixel Remote", FontSize = 15, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold },
                company.Length > 0 ? Ui.Text(company, secondary: true, size: 12, wrap: false) : null));
        brand.Margin = new Thickness(10, 14, 10, 14);
        AutomationProperties.SetName(brand, "Mixel Remote" + (company.Length > 0 ? ", " + company : ""));
        sidebarItems.Children.Add(brand);
        var inConsole = workspace.Selected.IsConsole;
        sidebarItems.Children.Add(NavRow(AppSection.Devices, "", L("Devices"), app.SignedIn ? devices.AllDevices.Count : null, 0, inConsole));
        sidebarItems.Children.Add(NavRow(AppSection.Sessions, "", L("Sessions"), null, workspace.Tabs.Count, inConsole));
        if (app.Company.Tenant is not null) sidebarItems.Children.Add(NavRow(AppSection.Reports, "", L("Reports"), null, 0, inConsole));
        sidebarItems.Children.Add(NavRow(AppSection.Settings, "", L("Settings"), null, 0, inConsole));
        if (app.SignedIn && devices.AllDevices.Count > 0)
        {
            sidebarItems.Children.Add(SideHeading(L("Views")));
            sidebarItems.Children.Add(ViewRow(DeviceView.All, "", L("All devices"), devices.DevicesIn(DeviceView.All).Count));
            sidebarItems.Children.Add(ViewRow(DeviceView.Online, "", L("Online now"), devices.DevicesIn(DeviceView.Online).Count));
            if (devices.Tenant is not null)
            {
                sidebarItems.Children.Add(ViewRow(DeviceView.Favorites, "", L("Favourites"), devices.DevicesIn(DeviceView.Favorites).Count));
                sidebarItems.Children.Add(ViewRow(DeviceView.Recent, "", L("Recent"), devices.DevicesIn(DeviceView.Recent).Count));
            }
            var canCreate = devices.Deploys && devices.GroupsOn && devices.Writable;
            if (devices.GroupNames.Count > 0 || canCreate)
            {
                var heading = SideHeading(L("Groups"));
                UIElement header = heading;
                if (canCreate)
                {
                    var add = Ui.Flat(Ui.Icon("", 12), L("Create group"), () => _ = CreateGroup());
                    add.Padding = new Thickness(5);
                    add.MinHeight = 0;
                    add.MinWidth = 0;
                    header = Ui.Split(heading, add);
                }
                sidebarItems.Children.Add(header);
                foreach (var name in devices.GroupNames) sidebarItems.Children.Add(GroupRow(name, devices.DevicesIn(DeviceView.InGroup(name)).Count));
            }
        }
        BuildFooter();
    }

    private async Task CreateGroup()
    {
        if (root.XamlRoot is not { } xaml) return;
        await Dialogs.Form(xaml, L("Create group"), null, [(L("Name"), "")], L("Create group"), async values => await app.Devices.CreateGroup(values[0].Trim()) ? null : app.Devices.Error);
    }

    private static TextBlock SideHeading(string text)
    {
        var label = Ui.Label(text);
        label.Margin = new Thickness(10, 16, 10, 4);
        return label;
    }

    private UIElement Row(string? glyph, UIElement? mark, string text, int? count, int badge, bool selected, Action action, string name)
    {
        var grid = new Grid { ColumnSpacing = 10 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var icon = mark ?? Ui.Icon(glyph!, 15, selected ? Ui.BrandBrush : null);
        ((FrameworkElement)icon).VerticalAlignment = VerticalAlignment.Center;
        grid.Children.Add(icon);
        var label = new TextBlock { Text = text, FontWeight = selected ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal, Foreground = selected ? Ui.BrandBrush : Ui.Brush("TextFillColorPrimaryBrush"), VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
        Grid.SetColumn(label, 1);
        grid.Children.Add(label);
        if (badge > 0)
        {
            var pill = new Border { Background = Ui.BrandBrush, CornerRadius = new CornerRadius(9), MinWidth = 18, Height = 18, Padding = new Thickness(5, 0, 5, 0), VerticalAlignment = VerticalAlignment.Center, Child = new TextBlock { Text = badge.ToString(System.Globalization.CultureInfo.InvariantCulture), FontSize = 11, FontWeight = Microsoft.UI.Text.FontWeights.Bold, Foreground = new SolidColorBrush(Colors.White), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center } };
            Grid.SetColumn(pill, 2);
            grid.Children.Add(pill);
        }
        else if (count is { } number)
        {
            var text2 = Ui.Text(number.ToString(System.Globalization.CultureInfo.InvariantCulture), secondary: true, wrap: false);
            text2.FontSize = 12;
            text2.VerticalAlignment = VerticalAlignment.Center;
            Grid.SetColumn(text2, 2);
            grid.Children.Add(text2);
        }
        var button = new Button
        {
            Content = grid, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(10, 8, 10, 8),
            Background = selected ? Ui.Brush("MixelBrandSoft") : new SolidColorBrush(Colors.Transparent), BorderThickness = new Thickness(0),
        };
        AutomationProperties.SetName(button, count is { } c ? $"{name}, {c}" : badge > 0 ? $"{name}, {badge}" : name);
        if (selected) AutomationProperties.SetItemStatus(button, L("Selected"));
        button.Click += (_, _) => action();
        return button;
    }

    private UIElement NavRow(AppSection section, string glyph, string text, int? count, int badge, bool inConsole)
    {
        var selected = inConsole && app.Section == section;
        var row = Row(glyph, null, text, count, badge, selected, () => ShowSection(section), text);
        AutomationProperties.SetAutomationId(row, "section-" + section.ToString().ToLowerInvariant());
        var key = section switch { AppSection.Devices => 1, AppSection.Sessions => 2, AppSection.Reports => 3, _ => 4 };
        ToolTipService.SetToolTip(row, $"{text} (Ctrl+{key})");
        return row;
    }

    private UIElement ViewRow(DeviceView view, string glyph, string text, int count)
    {
        var selected = app.Section == AppSection.Devices && app.Workspace.Selected.IsConsole && app.Devices.View == view;
        return Row(glyph, null, text, count, 0, selected, () =>
        {
            app.Devices.View = view;
            ShowSection(AppSection.Devices);
        }, text);
    }

    private UIElement GroupRow(string name, int count)
    {
        var view = DeviceView.InGroup(name);
        var selected = app.Section == AppSection.Devices && app.Workspace.Selected.IsConsole && app.Devices.View == view;
        var dot = new Microsoft.UI.Xaml.Shapes.Ellipse { Width = 8, Height = 8, Fill = Ui.GroupColor(name), HorizontalAlignment = HorizontalAlignment.Center };
        return Row(null, dot, name, count, 0, selected, () =>
        {
            app.Devices.View = view;
            ShowSection(AppSection.Devices);
        }, name);
    }

    /// <summary>This computer (status, ID, copy) and the account row.</summary>
    private void BuildFooter()
    {
        footer.Children.Clear();
        var remote = app.Remote;
        var card = new Border { Margin = new Thickness(12, 0, 12, 10), Padding = new Thickness(12, 10, 12, 10), CornerRadius = new CornerRadius(10), Background = Ui.Brush("MixelPaper"), BorderBrush = Ui.Brush("MixelHairline"), BorderThickness = new Thickness(1) };
        var status = Ui.Status(remote.Connected ? Ui.Badge.Online : remote.Phase == "error" ? Ui.Badge.Attention : Ui.Badge.Offline,
            (remote.Connected ? L("This computer · online") : remote.Phase == "error" ? L("Connection problem") : L("This computer · offline")));
        if ((app.SignedIn || app.QuickSupportMode) && remote.Host is { } host)
        {
            var id = Ui.Selectable(Format.RemoteId(host), 20, mono: true);
            id.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
            AutomationProperties.SetName(id, L("Connection ID") + " " + Format.RemoteId(host));
            var copy = Ui.IconButton("", L("Copy ID"), () => app.CopyText(host.ToString(System.Globalization.CultureInfo.InvariantCulture)));
            copy.Style = null;
            copy.Background = new SolidColorBrush(Colors.Transparent);
            copy.BorderThickness = new Thickness(0);
            var open = Ui.Flat(Ui.Icon("", 13), L("Remote control"), () => ShowSection(AppSection.Remote));
            card.Child = Ui.Column(4, Ui.Split(status, open), Ui.Split(id, copy));
        }
        else if (!app.SignedIn)
        {
            card.Child = Ui.Column(4, status, Ui.Link(L("Sign in"), () => ShowSection(AppSection.Account)));
        }
        else card.Child = status;
        footer.Children.Add(card);
        footer.Children.Add(Ui.Hairline());
        var profile = app.Account.Account;
        var name = profile?.DisplayName ?? (app.SignedIn ? L("Account") : L("Sign in"));
        var initials = string.Concat(name.Split(' ', StringSplitOptions.RemoveEmptyEntries).Take(2).Select(part => char.ToUpperInvariant(part[0])));
        var avatar = new Border
        {
            Width = 34, Height = 34, CornerRadius = new CornerRadius(17), Background = Ui.Brush("MixelInfoSoft"),
            Child = new TextBlock { Text = initials.Length > 0 ? initials : "?", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Foreground = Ui.Brush("MixelInfoText"), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center },
        };
        var who = Ui.Row(10, avatar, Ui.Column(1, Ui.Text(name, bold: true, wrap: false), Ui.Text(app.SignedIn ? (app.Company.Tenant is not null ? Format.Role(app.Company.Role) : L("Account")) : L("Not signed in"), secondary: true, size: 12, wrap: false)));
        var row = new Button { Content = who, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Left, Background = new SolidColorBrush(Colors.Transparent), BorderThickness = new Thickness(0), Padding = new Thickness(18, 10, 12, 10) };
        AutomationProperties.SetName(row, name + ", " + L("Account"));
        row.Click += (_, _) => ShowSection(AppSection.Account);
        footer.Children.Add(row);
    }

    // ---- Notices bottom right: the connection being prepared, and "ready · Open" ----

    private void BuildNotices()
    {
        var workspace = app.Workspace;
        notices.Children.Clear();
        foreach (var peer in workspace.ReadyNotices)
        {
            if (workspace.Tab(peer) is { } tab) notices.Children.Add(ReadyNotice(tab));
        }
        if (workspace.Attempt is { } attempt) notices.Children.Add(AttemptPanel(attempt));
        if (app.Settings.UpdatePrompt is { } update) notices.Children.Add(UpdateNotice(update));
    }

    /// <summary>A newer version that would sign the person out is not installed in secret: say so, one click installs it.</summary>
    private UIElement UpdateNotice(UpdateInfo update)
    {
        var settings = app.Settings;
        var text = Ui.Column(2,
            Ui.Text(L("Update available"), bold: true, wrap: false),
            Ui.Caption(L("Version %@ is ready. Installing restarts the app and signs you out.", update.Version)));
        var now = Ui.Primary(L("Update now"), () => _ = settings.InstallUpdate());
        var later = Ui.IconButton("", L("Dismiss"), settings.DismissUpdatePrompt);
        later.Style = null;
        later.Background = new SolidColorBrush(Colors.Transparent);
        later.BorderThickness = new Thickness(0);
        var grid = new Grid { ColumnSpacing = 10 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.Children.Add(text);
        Grid.SetColumn(now, 1);
        grid.Children.Add(now);
        Grid.SetColumn(later, 2);
        grid.Children.Add(later);
        var card = Floating(grid, 12);
        AutomationProperties.SetName(card, L("Update available"));
        AutomationProperties.SetLiveSetting(card, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
        return card;
    }

    private static Border Floating(UIElement child, double radius) => new()
    {
        Child = child, Padding = new Thickness(radius > 12 ? 16 : 12), Width = 380, CornerRadius = new CornerRadius(radius),
        Background = Ui.Brush("AcrylicInAppFillColorDefaultBrush"), BorderBrush = Ui.Brush("MixelHairline"), BorderThickness = new Thickness(1),
        Shadow = new ThemeShadow(), Translation = new System.Numerics.Vector3(0, 0, 24),
    };

    private UIElement ReadyNotice(SessionTab tab)
    {
        var workspace = app.Workspace;
        var open = Ui.Primary(L("Open"), () => workspace.Show(tab.Peer));
        var dismiss = Ui.IconButton("", L("Dismiss"), () => workspace.DismissNotice(tab.Peer));
        dismiss.Style = null;
        dismiss.Background = new SolidColorBrush(Colors.Transparent);
        dismiss.BorderThickness = new Thickness(0);
        var grid = new Grid { ColumnSpacing = 10 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.Children.Add(Ui.OsTile(tab.Os, "MixelSuccess", 28));
        var text = Ui.Column(1, Ui.Text(tab.Name, bold: true, wrap: false), Ui.Caption(L("The session is ready.")));
        Grid.SetColumn(text, 1);
        grid.Children.Add(text);
        Grid.SetColumn(open, 2);
        grid.Children.Add(open);
        Grid.SetColumn(dismiss, 3);
        grid.Children.Add(dismiss);
        var card = Floating(grid, 12);
        AutomationProperties.SetName(card, L("Session with %@ is ready", tab.Name));
        AutomationProperties.SetLiveSetting(card, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
        return card;
    }

    private UIElement AttemptPanel(ConnectAttempt attempt)
    {
        var workspace = app.Workspace;
        var ok = attempt.Failure.Length == 0;
        var header = Ui.Row(8, Ui.Icon("", 14, Ui.BrandBrush), Ui.Text(ok ? L("Connecting") : L("Not connected"), bold: true, wrap: false));
        if (ok) header.Children.Add(Ui.Text("· " + (attempt.InWindow ? L("keep working, it opens in its own window") : L("keep working, it opens as a tab")), secondary: true, wrap: false));
        var identity = Ui.Row(10, Ui.OsTile(attempt.Os, null, 36), Ui.Column(1, Ui.Text(attempt.Name, bold: true, wrap: false), Ui.Selectable(Format.RemoteId(attempt.Peer), 13, mono: true)));
        var stack = Ui.Column(12, header, Ui.Hairline(), identity);
        if (ok)
        {
            var steps = Ui.Column(8);
            for (var step = 0; step < 4; step++)
            {
                UIElement mark = step < attempt.Step ? Ui.Icon("", 15, Ui.Brush("MixelSuccess"))
                    : step == attempt.Step ? Ui.Spinner(16) : Ui.Icon("", 15, Ui.Brush("TextFillColorTertiaryBrush"));
                var title = Ui.Text(Pages.DevicesPage.ConnectStepTitle(step, attempt.Unattended), secondary: step > attempt.Step, wrap: false);
                var line = Ui.Row(10, mark, title);
                AutomationProperties.SetName(line, title.Text);
                AutomationProperties.SetItemStatus(line, step < attempt.Step ? L("done") : step == attempt.Step ? L("in progress") : L("waiting"));
                steps.Children.Add(line);
            }
            stack.Children.Add(steps);
            if (attempt.Step == 0 && app.Progress.Length > 0) stack.Children.Add(Ui.Caption(app.Progress));
        }
        else
        {
            stack.Children.Add(Ui.Message(attempt.Failure, maxWidth: 310) ?? new Grid());
            // The first connect of a computer that is not added to the account yet: the fix is one click away (same as the Mac).
            if (app.SetupOffer) stack.Children.Add(Ui.Primary(L("Add this computer"), () => _ = app.AddThisFromFailure()));
        }
        var cancel = Ui.Plain(ok ? L("Cancel") : L("Close"), () => _ = workspace.CancelAttempt());
        var buttons = Ui.Split(cancel, ok && !attempt.InWindow ? Ui.Link(L("Open in window instead"), workspace.OpenAttemptInWindow) : null);
        stack.Children.Add(buttons);
        var card = Floating(stack, 14);
        AutomationProperties.SetName(card, L("Connecting to %@", attempt.Name));
        return card;
    }
}
