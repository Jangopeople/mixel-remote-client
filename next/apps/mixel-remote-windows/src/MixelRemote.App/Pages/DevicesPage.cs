using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using MixelRemote.App.Shell;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>
/// Devices in the "Pro Console" look: the header with Connect by ID, search and quick filters, and a dense
/// table grouped by group (favourite, system, name and note, ID, status, last seen, version, Connect, row
/// menu). Connecting shows its progress on the row; the person stays here. For companies also groups, the
/// Manage menu, Add this computer and Add other computers (deploy link).
/// </summary>
internal sealed class DevicesPage : PageView
{
    private const double StarWidth = 26, TileWidth = 44, IdWidth = 112, StatusWidth = 196, SeenWidth = 92, VersionWidth = 118, ActionsWidth = 150;

    public DevicesPage(AppModel app) : base(app)
    {
        Watch(app.Devices);
        Watch(app.Company);
        Watch(app.Enrollment);
        Watch(app.Workspace);
        app.PropertyChanged += (_, change) =>
        {
            if (change.PropertyName != nameof(AppModel.AddThisRequested) || !app.AddThisRequested) return;
            app.AddThisRequested = false;
            DispatcherQueue.TryEnqueue(() => _ = AddThisComputer());
        };
        SizeChanged += (_, _) =>
        {
            if (WidthClass == widthClass) return;
            widthClass = WidthClass;
            Rebuild();
        };
        var refresh = new KeyboardAccelerator { Key = Windows.System.VirtualKey.F5 };
        refresh.Invoked += (sender, args) => { _ = app.Devices.Load(); args.Handled = true; };
        KeyboardAccelerators.Add(refresh);
        // "/" puts the cursor into the search field, as in the mockup (not while typing in a field).
        var slash = new KeyboardAccelerator { Key = (Windows.System.VirtualKey)191 };
        slash.Invoked += (sender, args) =>
        {
            if (XamlRoot is not null && FocusManager.GetFocusedElement(XamlRoot) is TextBox or PasswordBox or ComboBox) return;
            FocusSearch();
            args.Handled = true;
        };
        KeyboardAccelerators.Add(slash);
    }

    private DevicesModel Model => App.Devices;
    private WorkspaceModel Workspace => App.Workspace;
    /// <summary>A connection is being prepared (its panel shows no failure): one at a time.</summary>
    // An engine that runs several sessions lets the next connection start while one is still being prepared.
    private bool Preparing => Workspace.Attempt is { Failure.Length: 0 } && App.Remote.Sessions is null;

    public void FocusSearch() => Peek<TextBox>("search")?.Focus(FocusState.Keyboard);
    public void FocusConnectById() => Peek<TextBox>("byid")?.Focus(FocusState.Keyboard);

    // The table follows the width: from 1100 DIPs the version, from 900 the last-seen column, below that a tight layout.
    private bool ShowVersion => Model.AdminAvailable && (ActualWidth == 0 || ActualWidth >= 1100);
    private bool ShowSeen => ActualWidth == 0 || ActualWidth >= 900;
    private bool Tight => ActualWidth > 0 && ActualWidth < 900;
    private int widthClass = -1;

    private int WidthClass => (ShowVersion ? 1 : 0) + (ShowSeen ? 2 : 0) + (Tight ? 4 : 0);

    private string Title() => Model.View.Kind switch
    {
        DeviceViewKind.Online => L("Online now"),
        DeviceViewKind.Favorites => L("Favourites"),
        DeviceViewKind.Recent => L("Recent"),
        DeviceViewKind.Group => Model.View.Group,
        _ => L("All devices"),
    };

    private string Summary()
    {
        if (!App.SignedIn) return L("Sign in to see the devices available to your account.");
        var shown = Model.Filtered;
        var total = Model.DevicesIn(Model.View).Count;
        var line = L("%d of %d devices · %d online", shown.Count, total, shown.Count(Model.IsOnline));
        return Model.AdminAvailable ? line + " · " + L("%d with unattended access", Model.UnattendedCount) : line;
    }

    protected override UIElement Build()
    {
        var model = Model;
        var titleBlock = new TextBlock { Text = Title(), FontSize = 26, FontWeight = Microsoft.UI.Text.FontWeights.Bold };
        AutomationProperties.SetHeadingLevel(titleBlock, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level1);
        var header = Ui.Split(Ui.Column(4, titleBlock, Ui.Text(Summary(), secondary: true)), ConnectById());
        var search = SearchBox();
        var filters = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        filters.Children.Add(search);
        filters.Children.Add(Ui.Chip(L("Online"), model.OnlineOnly, () => model.OnlineOnly = !model.OnlineOnly, "MixelSuccess"));
        if (model.AdminAvailable)
        {
            filters.Children.Add(Ui.Chip(L("Unattended"), model.UnattendedOnly, () => model.UnattendedOnly = !model.UnattendedOnly));
            filters.Children.Add(Ui.Chip(L("Update available"), model.UpdateOnly, () => model.UpdateOnly = !model.UpdateOnly));
        }
        filters.Children.Add(Ui.Hairline(vertical: true));
        foreach (var (key, label) in new[] { ("macos", "macOS"), ("windows", "Windows"), ("linux", "Linux") })
        {
            filters.Children.Add(Ui.Chip(label, model.Systems.Contains(key), () => model.ToggleSystem(key)));
        }
        var tools = Ui.Row(8,
            model.Tenant is not null && App.SignedIn && model.Manages && !model.RegisteredHere
                ? Tip(Ui.Plain(L("Add this computer"), () => _ = AddThisComputer(), model.AddBlocked.Length == 0), model.AddBlocked) : null,
            model.Tenant is not null && App.SignedIn && model.Deploys
                ? Tip(Ui.Plain(L("Add other computers"), () => _ = AddOthers(), model.AddBlocked.Length == 0), model.AddBlocked) : null,
            App.SignedIn && model.RollsOut ? Ui.Plain(L("Create rollout command"), () => _ = Rollout()) : null,
            Ui.IconButton("", L("Refresh devices") + " (F5)", () => _ = model.Load(), App.SignedIn && !model.Loading),
            Ui.Text(L("Sorted by status"), secondary: true, wrap: false));
        var filterRow = Ui.Split(new ScrollViewer { Content = filters, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollMode = ScrollMode.Auto, VerticalScrollMode = ScrollMode.Disabled }, tools);
        var top = Ui.Column(14, header, filterRow,
            model.AddBlocked.Length > 0 && model.Manages ? Ui.Message(model.Writable
                ? L("Device limit of your plan reached. Remove a device or upgrade the plan to add more.")
                : L("This company is read-only. You can still remove devices; adding devices and other changes need an active plan."), error: false) : null,
            App.SignedIn && !App.Remote.Connected && App.Remote.Phase != "closing" && !App.Enrollment.NeedsSetup ? ConnectionBanner() : null,
            Ui.Message(model.Error),
            Workspace.Attempt is null ? Ui.Message(App.Error) : null,
            Ui.Message(model.Notice, error: false));
        top.Padding = new Thickness(28, 22, 28, 10);
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(top);
        var list = Table();
        Grid.SetRow(list, 1);
        grid.Children.Add(list);
        return new Border { Child = grid, Background = Ui.Brush("MixelPaper") };
    }

    private TextBox SearchBox()
    {
        var box = Keep("search", () =>
        {
            var created = new TextBox { PlaceholderText = L("Search name, ID or group"), Width = 300 };
            created.TextChanged += (_, _) => Model.FilterText = created.Text;
            created.KeyDown += (sender, key) =>
            {
                // The directory of a team member is paged on the server: search there too.
                if (key.Key == Windows.System.VirtualKey.Enter && Model.Tenant is null)
                {
                    Model.Search = created.Text;
                    _ = Model.Load();
                }
            };
            return created;
        });
        box.PlaceholderText = L("Search name, ID or group");
        AutomationProperties.SetName(box, L("Search"));
        ToolTipService.SetToolTip(box, L("Search") + " (/)");
        return box;
    }

    /// <summary>"Connect by ID 000 000 000" and the red Connect button, top right.</summary>
    private UIElement ConnectById()
    {
        var box = Keep("byid", () =>
        {
            var created = new TextBox { PlaceholderText = "000 000 000", Width = 150, FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Cascadia Mono, Consolas"), FontSize = 15, MaxLength = 11 };
            created.TextChanged += (_, _) =>
            {
                var digits = new string([.. created.Text.Where(char.IsAsciiDigit).Take(9)]);
                var grouped = string.Join(' ', Enumerable.Range(0, (digits.Length + 2) / 3).Select(group => digits.Substring(group * 3, Math.Min(3, digits.Length - group * 3))));
                if (grouped != created.Text)
                {
                    created.Text = grouped;
                    created.SelectionStart = grouped.Length;
                }
                else Rebuild();
            };
            created.KeyDown += (sender, key) =>
            {
                if (key.Key == Windows.System.VirtualKey.Enter) ConnectTyped(created);
            };
            return created;
        });
        AutomationProperties.SetName(box, L("Connect by ID"));
        var connect = Ui.Primary(L("Connect"), () => ConnectTyped(box), App.SignedIn && Format.ParseRemoteId(box.Text) is not null && App.CanStartConnection && !Preparing, "\uE945");
        return Ui.Row(8, Ui.Text(L("Connect by ID"), secondary: true, wrap: false), box, connect);
    }

    private void ConnectTyped(TextBox box)
    {
        if (Format.ParseRemoteId(box.Text) is not { } id)
        {
            App.Error = L("Enter a nine-digit connection ID");
            return;
        }
        box.Text = "";
        var known = Model.AllDevices.FirstOrDefault(device => device.ConnectionId == id);
        _ = Workspace.Connect(id, known?.Name, known?.Os ?? "", unattended: true, checkPhone: known is null);
    }

    private static Button Tip(Button button, string why)
    {
        if (why.Length > 0) ToolTipService.SetToolTip(button, why);
        return button;
    }

    private Border ConnectionBanner()
    {
        var banner = new Border
        {
            Padding = new Thickness(12),
            CornerRadius = new CornerRadius(8),
            Background = Ui.Brush("MixelWarningSoft"),
            Child = Ui.Split(
                Ui.Row(10, Ui.Icon("", 16, Ui.Brush("MixelWarning")), Ui.Column(3,
                    Ui.Text(App.Remote.Phase == "error" ? L("This computer lost its connection") : L("This computer is not connected yet"), bold: true),
                    Ui.Text(string.IsNullOrWhiteSpace(App.Remote.Message)
                        ? L("Mixel Remote reconnects by itself. Device status appears once it is connected; you can still choose Connect.")
                        : Translator.Shared.Translate(App.Remote.Message), secondary: true))),
                Ui.Plain(L("Reconnect now"), () => _ = App.GoOnline())),
        };
        return banner;
    }

    // ---- The table ----

    private Grid Columns()
    {
        var grid = new Grid { ColumnSpacing = 8 };
        var tight = Tight;
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(StarWidth) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(TileWidth) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star), MinWidth = tight ? 120 : 160 });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(tight ? 104 : IdWidth) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(tight ? 160 : StatusWidth) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(ShowSeen ? SeenWidth : 0) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(ShowVersion ? VersionWidth : 0) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(tight ? 128 : ActionsWidth) });
        return grid;
    }

    private static void Put(Grid grid, UIElement element, int column)
    {
        Grid.SetColumn((FrameworkElement)element, column);
        ((FrameworkElement)element).VerticalAlignment = VerticalAlignment.Center;
        grid.Children.Add(element);
    }

    private FrameworkElement Table()
    {
        var model = Model;
        var groups = model.RowGroups;
        if (groups.Count == 0)
        {
            var empty = Ui.Column(10,
                Ui.Icon("", 44, Ui.Secondary),
                new TextBlock { Text = EmptyTitle(), FontSize = 20, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, HorizontalAlignment = HorizontalAlignment.Center },
                new TextBlock { Text = EmptyDetail(), Foreground = Ui.Secondary, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center, MaxWidth = 440 },
                !App.SignedIn ? Ui.Link(L("Sign in to Mixel →"), () => App.Section = AppSection.Account) : null);
            empty.HorizontalAlignment = HorizontalAlignment.Center;
            empty.VerticalAlignment = VerticalAlignment.Center;
            foreach (var child in empty.Children) ((FrameworkElement)child).HorizontalAlignment = HorizontalAlignment.Center;
            return empty;
        }
        var header = Columns();
        header.Padding = new Thickness(12, 8, 12, 8);
        Put(header, Ui.Label(L("Device")), 2);
        Put(header, Ui.Label(L("ID")), 3);
        Put(header, Ui.Label(L("Status")), 4);
        if (ShowSeen) Put(header, Ui.Label(L("Last seen")), 5);
        if (ShowVersion) Put(header, Ui.Label(L("Version")), 6);
        var actionsLabel = Ui.Label(L("Actions"));
        actionsLabel.HorizontalAlignment = HorizontalAlignment.Right;
        Put(header, actionsLabel, 7);
        var rows = new StackPanel { Spacing = 0, Padding = new Thickness(0, 0, 0, 120) };
        foreach (var group in groups)
        {
            rows.Children.Add(GroupHeader(group));
            foreach (var device in group.Devices) rows.Children.Add(Row(device, group));
        }
        if (model.Tenant is null && (model.Page > 0 || model.HasNext))
        {
            var pager = Ui.Split(
                Ui.Row(8, Ui.Plain(L("Previous"), () => _ = model.PreviousPage(), model.Page > 0 && !model.Loading), Ui.Plain(L("Next"), () => _ = model.NextPage(), model.HasNext && !model.Loading)),
                Ui.Text(model.Message, secondary: true));
            pager.Margin = new Thickness(12, 16, 12, 0);
            rows.Children.Add(pager);
        }
        var table = new Grid { Margin = new Thickness(16, 0, 16, 0) };
        table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        table.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        table.Children.Add(header);
        var line = Ui.Hairline();
        Grid.SetRow(line, 1);
        table.Children.Add(line);
        var scroller = new ScrollViewer { Content = rows, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        Grid.SetRow(scroller, 2);
        table.Children.Add(scroller);
        return table;
    }

    private static UIElement GroupHeader(RowGroup group)
    {
        var row = Ui.Row(8,
            new Microsoft.UI.Xaml.Shapes.Ellipse { Width = 8, Height = 8, Fill = group.Id == "-" ? Ui.Secondary : Ui.GroupColor(group.Name), VerticalAlignment = VerticalAlignment.Center },
            Ui.Text(group.Name, bold: true, wrap: false),
            Ui.Text(L("%d devices · %d online", group.Devices.Count, group.Online), secondary: true, wrap: false));
        var border = new Border { Child = row, Padding = new Thickness(14, 8, 14, 8), Background = Ui.Brush("MixelGroupRow"), BorderBrush = Ui.Brush("MixelHairline"), BorderThickness = new Thickness(0, 0, 0, 1) };
        AutomationProperties.SetName(border, $"{group.Name}, {L("%d devices · %d online", group.Devices.Count, group.Online)}");
        AutomationProperties.SetHeadingLevel(border, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level2);
        return border;
    }

    private string EmptyTitle()
    {
        if (Model.Loading) return L("Loading your devices…");
        if (!App.SignedIn) return L("Your devices belong here");
        if (Model.AnyFilter) return L("No matching devices");
        return L("No devices to display");
    }

    private string EmptyDetail()
    {
        if (!App.SignedIn) return L("Sign in to see the devices available to your account.");
        if (Model.AnyFilter) return L("Try another device name or clear the search.");
        if (Model.Tenant is not null)
        {
            return Model.Manages ? L("Add this computer, or add other computers with a deploy link.") : L("Devices you add appear here. Ask an administrator of your company for access to other devices.");
        }
        return L("Devices appear here after a Mixel team member enrolls them.");
    }

    private static string LastSeenText(DevicesModel model, Device device, Presence presence, string? state)
    {
        if (state is not null || presence is Presence.Online or Presence.This) return L("Now");
        if (model.LastSeenDate(device) is not { } date) return "—";
        var minutes = Math.Max(0, (int)(DateTimeOffset.UtcNow - date).TotalMinutes);
        if (minutes < 2) return L("Just now");
        if (minutes < 60) return L("%d min ago", minutes);
        var hours = minutes / 60;
        if (hours < 24) return L("%d h ago", hours);
        var days = hours / 24;
        return days == 1 ? L("Yesterday") : L("%d days ago", days);
    }

    private UIElement Row(Device device, RowGroup group)
    {
        var model = Model;
        var presence = model.PresenceOf(device);
        var state = Workspace.StateOf(device.ConnectionId);
        var attempt = Workspace.Attempt is { } current && current.Peer == device.ConnectionId ? current : null;
        var info = model.AdminOf(device);
        var inSession = state is "ready" or "starting";
        var grid = Columns();
        grid.Padding = new Thickness(12, 8, 12, 8);

        // Star, system tile with the status dot, name and the line under it.
        if (model.Tenant is not null)
        {
            var on = model.IsFavorite(device);
            var star = Ui.IconButton(on ? "" : "", on ? L("Remove from favorites") : L("Add to favorites"), () => _ = model.ToggleFavorite(device), !App.Busy);
            star.Style = null;
            star.Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent);
            star.BorderThickness = new Thickness(0);
            star.Padding = new Thickness(3);
            if (on && star.Content is FontIcon icon) icon.Foreground = Ui.Brush("MixelStar");
            Put(grid, star, 0);
        }
        var dot = state is not null ? "MixelWarning" : presence is Presence.Online or Presence.This ? "MixelSuccess" : presence == Presence.Offline ? "TextFillColorTertiaryBrush" : null;
        Put(grid, Ui.OsTile(device.Os, dot), 1);
        var parts = new List<string> { Format.Os(device.Os) };
        if (model.View.Kind != DeviceViewKind.Group && !string.IsNullOrEmpty(device.GroupName) && group.Id == "-") parts.Add(device.GroupName);
        if (!string.IsNullOrWhiteSpace(info?.Note)) parts.Add(info!.Note!);
        if (presence == Presence.This) parts.Add(L("This computer"));
        var name = Ui.Column(2, Ui.Text(device.Name, bold: true, wrap: false), Ui.Text(string.Join(" · ", parts), secondary: true, wrap: false));
        foreach (var child in name.Children.OfType<TextBlock>().Skip(1)) child.FontSize = 12;
        Put(grid, name, 2);
        var id = Ui.Selectable(Format.RemoteId(device.ConnectionId), 13, mono: true);
        AutomationProperties.SetName(id, L("Connection ID") + " " + Format.RemoteId(device.ConnectionId));
        Put(grid, id, 3);

        // Status: connect progress on the row, "In session", or online/offline with the badges.
        UIElement status;
        if (attempt is { Failure.Length: 0 })
        {
            var progress = new ProgressBar { Minimum = 0, Maximum = 5, Value = attempt.Step + 1, MaxWidth = 180, Foreground = Ui.BrandBrush };
            AutomationProperties.SetName(progress, L("Connecting"));
            status = Ui.Column(4, Ui.Row(6, Ui.Spinner(14), Ui.Text(ConnectStepTitle(attempt.Step, attempt.Unattended) + "…", bold: true, wrap: false)), progress);
        }
        else if (state is "ready" or "starting" or "ending" or "reconnecting" or "failed")
        {
            status = Ui.Pill(state switch { "starting" => L("Starting video"), "reconnecting" => L("Reconnecting..."), "failed" => L("Could not reconnect"), _ => L("In session") }, "", "MixelSessionText", "MixelSessionSoft");
        }
        else
        {
            var line = Ui.Row(6, Ui.Status(Badge(presence), model.Label(presence)));
            if (info?.Unattended == true) line.Children.Add(Ui.Pill(L("Unattended"), "", "MixelInfoText", "MixelInfoSoft"));
            status = line;
            ToolTipService.SetToolTip(line, model.Reason(device));
        }
        Put(grid, status, 4);
        if (ShowSeen) Put(grid, Ui.Text(LastSeenText(model, device, presence, state), secondary: true, wrap: false), 5);
        if (ShowVersion)
        {
            var version = Ui.Row(6, Ui.Text(model.VersionOf(device), secondary: true, wrap: false));
            if (model.UpdateAvailable(device)) version.Children.Add(Ui.Pill(L("Update"), null, "MixelUpdateText", "MixelUpdateSoft"));
            Put(grid, version, 6);
        }

        // Actions: Connect or Show, and the "..." menu.
        var blocked = model.ConnectBlocked(device);
        var connecting = state == "connecting";
        Button main;
        if (inSession)
        {
            main = Ui.Plain(L("Show"), () => Workspace.Show(device.ConnectionId), true, "");
            ToolTipService.SetToolTip(main, L("Show the session with %@", device.Name));
        }
        else
        {
            main = Ui.Plain(connecting ? L("Connecting…") : L("Connect"), () => _ = Workspace.Connect(device.ConnectionId, device.Name, device.Os, unattended: true),
                blocked.Length == 0 && !connecting && !Preparing);
            ToolTipService.SetToolTip(main, blocked.Length == 0 ? L("Connect to %@", device.Name) : blocked);
        }
        var menu = DeviceMenu(device, presence);
        var more = new DropDownButton { Content = Ui.Icon("", 14), Flyout = menu, Padding = new Thickness(8, 6, 8, 6) };
        more.Resources["DropDownButtonCustomChevronVisibility"] = Visibility.Collapsed;
        AutomationProperties.SetName(more, L("More for %@", device.Name));
        var actions = Ui.Row(6, main, more);
        actions.HorizontalAlignment = HorizontalAlignment.Right;
        Put(grid, actions, 7);

        var card = new Border
        {
            Child = grid,
            Background = Ui.Brush(inSession ? "MixelActiveRow" : "MixelPaper"),
            BorderBrush = Ui.Brush("MixelHairline"),
            BorderThickness = new Thickness(0, 0, 0, 1),
            ContextFlyout = DeviceMenu(device, presence),
        };
        if (!inSession)
        {
            card.PointerEntered += (_, _) => card.Background = Ui.Brush("MixelHoverRow");
            card.PointerExited += (_, _) => card.Background = Ui.Brush("MixelPaper");
        }
        AutomationProperties.SetName(card, $"{device.Name}, {Format.Os(device.Os)}, {model.Label(presence)}");
        return card;
    }

    /// <summary>The steps of a connection, as the connecting panel and the row show them.</summary>
    public static string ConnectStepTitle(int step, bool unattended) => step switch
    {
        0 => L("Reaching device"),
        1 => unattended ? L("Checking unattended access") : L("Waiting for the person to accept"),
        2 => L("Encrypting end-to-end"),
        _ => L("Starting video"),
    };

    private static Ui.Badge Badge(Presence presence) => presence switch
    {
        Presence.Online or Presence.This => Ui.Badge.Online,
        Presence.Offline => Ui.Badge.Offline,
        Presence.Checking => Ui.Badge.Busy,
        _ => Ui.Badge.Unknown,
    };

    /// <summary>The row's "..." menu (also its right-click menu).</summary>
    private MenuFlyout DeviceMenu(Device device, Presence presence)
    {
        var model = Model;
        var menu = new MenuFlyout();
        void Add(string text, Action action, bool enabled = true)
        {
            var item = new MenuFlyoutItem { Text = text, IsEnabled = enabled };
            item.Click += (_, _) => action();
            menu.Items.Add(item);
        }
        var blocked = model.ConnectBlocked(device);
        Add(L("Connect without confirmation (unattended access)"), () => _ = Workspace.Connect(device.ConnectionId, device.Name, device.Os, unattended: true), blocked.Length == 0);
        Add(L("Connect and ask the person to accept"), () => _ = Workspace.Connect(device.ConnectionId, device.Name, device.Os, unattended: false), blocked.Length == 0);
        var macs = model.KnownMacs(device);
        if (presence == Presence.Offline && macs.Length > 0) Add(L("Wake"), () => _ = model.Wake(device, macs), !App.Busy);
        Add(L("Copy ID"), () => App.CopyText(device.ConnectionId.ToString(System.Globalization.CultureInfo.InvariantCulture)));
        if (model.Tenant is not null) Add(model.IsFavorite(device) ? L("Remove from favorites") : L("Add to favorites"), () => _ = model.ToggleFavorite(device));
        if (model.Manages || model.CanAdmin(device))
        {
            menu.Items.Add(new MenuFlyoutSeparator());
            if (model.AdminAvailable && model.CanAdmin(device))
            {
                Add(L("Edit note…"), () => _ = EditNote(device));
                var unattended = new MenuFlyoutSubItem { Text = L("Unattended access") };
                var current = model.AdminOf(device)?.UnattendedSetting ?? "default";
                foreach (var (key, label) in new[] { ("default", L("Company default")), ("on", L("Allowed")), ("off", L("Turned off")) })
                {
                    var choice = new ToggleMenuFlyoutItem { Text = label, IsChecked = current == key };
                    var setting = key;
                    choice.Click += (_, _) => _ = model.SetUnattended(device, setting);
                    unattended.Items.Add(choice);
                }
                menu.Items.Add(unattended);
            }
            if (model.Manages)
            {
                Add(L("Rename…"), () => _ = Rename(device), model.Writable);
                if (model.GroupsOn) Add(L("Move to group…"), () => _ = Move(device), model.Writable);
                menu.Items.Add(new MenuFlyoutSeparator());
                Add(L("Remove…"), () => _ = Remove(device));
            }
        }
        return menu;
    }

    private Task EditNote(Device device) => Dialogs.Form(Root, L("Edit note…"), null, [(L("Note"), model_note(device))], L("Save"),
        async values => await Model.SetNote(device, values[0]) ? null : Model.Error);

    private string model_note(Device device) => Model.AdminOf(device)?.Note ?? "";

    private Task Rename(Device device) => Dialogs.Form(Root, L("Rename device"), null, [(L("Device name"), device.Name)], L("Rename"),
        async values => await Model.Rename(device, values[0].Trim()) ? null : Model.Error);

    private async Task Move(Device device)
    {
        string? group = device.GroupId;
        var choice = Ui.Choice([("", L("No group")), .. Model.Groups.Select(item => (item.GroupId, item.Name))], group ?? "", value => group = string.IsNullOrEmpty(value) ? null : value);
        UIElement content = Ui.Field(L("Group"), choice);
        if (Model.Deploys)
        {
            var name = new TextBox { PlaceholderText = L("New group"), MinWidth = 220 };
            var create = Ui.Plain(L("Create group"), async () =>
            {
                if (name.Text.Trim().Length > 0 && await Model.CreateGroup(name.Text.Trim())) name.Text = "";
            });
            content = Ui.Column(12, content, Ui.Row(8, name, create));
        }
        await Dialogs.Show(Root, L("Move %@ to a group", device.Name), content, L("Move"), async () => await Model.Move(device, group));
    }

    private async Task Remove(Device device)
    {
        if (Model.AdminAvailable && Model.CanAdmin(device))
        {
            // The admin API asks for the device's own 9-digit ID as confirmation.
            await Dialogs.Form(Root, L("Remove this device?"),
                L("Remove %@ (%@)? It is signed out of Mixel Remote and must be added again.", device.Name, Format.RemoteId(device.ConnectionId)),
                [(L("Type the ID to confirm"), "")], L("Remove %@", device.Name),
                async values => await Model.RemoveConfirmed(device, values[0]) ? null : Model.Error);
            return;
        }
        if (await Dialogs.Confirm(Root, L("Remove this device?"), L("Its running sessions end at once. It can be added again later."), L("Remove %@", device.Name)))
        {
            await Model.Remove(device);
        }
    }

    private async Task AddThisComputer()
    {
        var card = Shared.EnrollmentCard(App, this);
        // The dialog closes by itself once this computer is added.
        var enrollment = App.Enrollment;
        System.ComponentModel.PropertyChangedEventHandler? watch = null;
        await Dialogs.Show(Root, L("Add this computer"), card, opened: dialog =>
        {
            watch = (_, _) => { if (enrollment.Current is not null) DispatcherQueue.TryEnqueue(dialog.Hide); };
            enrollment.PropertyChanged += watch;
        });
        if (watch is not null) enrollment.PropertyChanged -= watch;
    }

    /// <summary>"Add other computers": a deploy link and the one-line command for each system.</summary>
    private async Task AddOthers()
    {
        Model.DeployLink = null;
        var label = new TextBox { PlaceholderText = L("Rollout"), MinWidth = 320 };
        var computers = new NumberBox { Value = 50, Minimum = 1, Maximum = 2000, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline, Header = L("Computers") };
        var days = new NumberBox { Value = 30, Minimum = 1, Maximum = 365, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline, Header = L("Valid for (days)") };
        string? group = null;
        var form = Ui.Column(12, Ui.Field(L("Name"), label), computers, days,
            Model.GroupsOn && Model.Groups.Count > 0 ? Ui.Field(L("Group"), Ui.Choice([("", L("No group")), .. Model.Groups.Select(item => (item.GroupId, item.Name))], "", value => group = string.IsNullOrEmpty(value) ? null : value)) : null);
        var holder = new ContentControl { Content = form, HorizontalContentAlignment = HorizontalAlignment.Stretch };
        await Dialogs.Show(Root, L("Add other computers"), holder, L("Create deploy link"), async () =>
        {
            if (Model.DeployLink is not null) return true;
            var done = await Model.CreateDeployLink(label.Text.Trim().Length > 0 ? label.Text.Trim() : L("Rollout"), (int)days.Value, (int)computers.Value, group);
            if (!done)
            {
                holder.Content = Ui.Column(12, form, Ui.Message(Model.Error));
                return false;
            }
            holder.Content = DeployLinkView(Model.DeployLink!);
            return false;
        });
        Model.DeployLink = null;
    }

    /// <summary>Mixel team: a rollout command for an ISM company's computers, and the open rollout tokens.</summary>
    private async Task Rollout()
    {
        var model = Model;
        var companies = await model.RolloutCompanies();
        if (companies.Count == 0) return;
        var company = companies[0].ClientId;
        await model.LoadRollouts(company);
        var label = new TextBox { PlaceholderText = L("Rollout"), MinWidth = 320 };
        var computers = new NumberBox { Value = 50, Minimum = 1, Maximum = 2000, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline, Header = L("Computers") };
        var days = new NumberBox { Value = 30, Minimum = 1, Maximum = 365, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline, Header = L("Valid for (days)") };
        var list = new StackPanel { Spacing = 6 };
        void Tokens()
        {
            list.Children.Clear();
            foreach (var token in model.Rollouts.Where(token => token.RevokedAt is null))
            {
                list.Children.Add(Ui.Split(Ui.Column(1, Ui.Text(token.Label), Ui.Caption($"{token.Uses}/{token.MaxUses} · {L("Expires %@", Format.Day(token.ExpiresAt))}")),
                    Ui.Plain(L("Revoke"), async () => { await model.RevokeRollout(company, token); Tokens(); })));
            }
        }
        Tokens();
        var form = Ui.Column(12,
            Ui.Field(L("Company"), Ui.Choice(companies.Select(item => (item.ClientId, item.ClientName)), company, async value => { company = value ?? company; await model.LoadRollouts(company); Tokens(); })),
            Ui.Field(L("Name"), label), computers, days, list);
        var holder = new ContentControl { Content = form, HorizontalContentAlignment = HorizontalAlignment.Stretch };
        await Dialogs.Show(Root, L("Create rollout command"), holder, L("Create rollout command"), async () =>
        {
            if (model.DeployLink is not null) return true;
            if (!await model.CreateRollout(company, label.Text.Trim().Length > 0 ? label.Text.Trim() : L("Rollout"), (int)days.Value, (int)computers.Value))
            {
                holder.Content = Ui.Column(12, form, Ui.Message(model.Error));
                return false;
            }
            holder.Content = DeployLinkView(model.DeployLink!);
            return false;
        });
        model.DeployLink = null;
    }

    /// <summary>Screenshots: the dialog with a created link.</summary>
    public Task ShowDeployLink(DeployLinkCreated link) => Dialogs.Show(Root, L("Add other computers"), DeployLinkView(link));

    private StackPanel DeployLinkView(DeployLinkCreated link)
    {
        StackPanel Command(string title, string text)
        {
            var box = Ui.Selectable(text, mono: true);
            box.FontSize = 12;
            var copy = Ui.IconButton("\uE8C8", L("Copy command"), () => App.CopyText(text));
            var border = new Border { Child = Ui.Split(box, copy), Padding = new Thickness(10), CornerRadius = new CornerRadius(6), Background = Ui.Brush("LayerFillColorDefaultBrush") };
            return Ui.Field(title, border);
        }
        return Ui.Column(12,
            Ui.Text(L("Run the command on each computer (as administrator, or with your deployment tool). The link is shown only now."), secondary: true),
            Command(L("Windows (PowerShell, as administrator)"), link.WindowsCommand),
            Command("macOS", link.MacosCommand),
            Command("Linux", link.LinuxCommand),
            Ui.Field(L("Deploy token"), Ui.Selectable(link.Token, mono: true)));
    }
}
