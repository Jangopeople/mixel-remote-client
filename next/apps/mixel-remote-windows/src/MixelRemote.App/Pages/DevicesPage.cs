using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using MixelRemote.App.Shell;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>
/// Devices: every device the account may reach, with its status and the reason when it cannot be reached;
/// for companies also groups, the Manage menu, Add this computer and Add other computers (deploy link).
/// </summary>
internal sealed class DevicesPage : PageView
{
    public DevicesPage(AppModel app) : base(app)
    {
        Watch(app.Devices);
        Watch(app.Company);
        Watch(app.Enrollment);
        var refresh = new KeyboardAccelerator { Key = Windows.System.VirtualKey.F5 };
        refresh.Invoked += (sender, args) => { _ = app.Devices.Load(); args.Handled = true; };
        KeyboardAccelerators.Add(refresh);
    }

    private DevicesModel Model => App.Devices;

    protected override UIElement Build()
    {
        var model = Model;
        var header = Ui.Split(
            Ui.Column(4, Ui.PageTitle(L("Devices")), model.Tenant is { } tenant ? Ui.Text(tenant.Name, secondary: true) : null),
            Ui.Row(8,
                model.Tenant is not null && App.SignedIn && model.Manages && !model.RegisteredHere
                    ? Tip(Ui.Plain(L("Add this computer"), () => _ = AddThisComputer(), model.AddBlocked.Length == 0, "\uE710"), model.AddBlocked) : null,
                model.Tenant is not null && App.SignedIn && model.Deploys
                    ? Tip(Ui.Plain(L("Add other computers"), () => _ = AddOthers(), model.AddBlocked.Length == 0, "\uE71B"), model.AddBlocked) : null,
                App.SignedIn && model.RollsOut ? Ui.Plain(L("Create rollout command"), () => _ = Rollout(), true, "\uE71B") : null,
                Ui.IconButton("\uE72C", L("Refresh devices") + " (F5)", () => _ = model.Load(), App.SignedIn && !model.Loading)));
        var search = Keep("search", () =>
        {
            var box = new TextBox { PlaceholderText = L("Device name"), Width = 300 };
            box.TextChanged += (_, _) => Model.Search = box.Text;
            box.KeyDown += (sender, key) => { if (key.Key == Windows.System.VirtualKey.Enter) _ = Model.Load(); };
            AutomationProperties.SetName(box, L("Search"));
            return box;
        });
        var filters = Ui.Row(8, search,
            model.Manages && model.GroupsOn && model.Groups.Count > 0
                ? Ui.Choice([("", L("All groups")), .. model.Groups.Select(group => (group.GroupId, group.Name))], model.GroupFilter ?? "", value =>
                {
                    model.GroupFilter = string.IsNullOrEmpty(value) ? null : value;
                    _ = model.Load();
                })
                : null,
            Ui.Plain(L("Search"), () => _ = model.Load(), App.SignedIn && !model.Loading));
        var top = Ui.Column(14, header, filters,
            model.AddBlocked.Length > 0 && model.Manages ? Ui.Message(model.Writable
                ? L("Device limit of your plan reached. Remove a device or upgrade the plan to add more.")
                : L("This company is read-only. You can still remove devices; adding devices and other changes need an active plan."), error: false) : null,
            App.SignedIn && !App.Remote.Connected && App.Remote.Phase != "closing" && !App.Enrollment.NeedsSetup ? ConnectionBanner() : null,
            Ui.Message(model.Error),
            Ui.Message(model.Message == L("Page %d · %d devices.", model.Page + 1, model.Devices.Count) ? "" : model.Message, error: false));
        top.Padding = new Thickness(32, 24, 32, 12);
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(top);
        var list = List();
        Grid.SetRow(list, 1);
        grid.Children.Add(list);
        return grid;
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
                Ui.Row(10, Ui.Icon("\uE871", 16, Ui.Brush("MixelWarning")), Ui.Column(3,
                    Ui.Text(App.Remote.Phase == "error" ? L("This computer lost its connection") : L("This computer is not connected yet"), bold: true),
                    Ui.Text(L("Mixel Remote reconnects by itself. Device status appears once it is connected; you can still choose Connect."), secondary: true))),
                Ui.Plain(L("Reconnect now"), () => _ = App.GoOnline())),
        };
        return banner;
    }

    private FrameworkElement List()
    {
        var model = Model;
        if (model.Devices.Count == 0)
        {
            var empty = Ui.Column(10,
                Ui.Icon("\uE977", 44, Ui.Secondary),
                new TextBlock { Text = EmptyTitle(), FontSize = 20, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, HorizontalAlignment = HorizontalAlignment.Center },
                new TextBlock { Text = EmptyDetail(), Foreground = Ui.Secondary, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center, MaxWidth = 440 },
                !App.SignedIn ? Ui.Link(L("Sign in to Mixel →"), () => App.Section = AppSection.Account) : null);
            empty.HorizontalAlignment = HorizontalAlignment.Center;
            empty.VerticalAlignment = VerticalAlignment.Center;
            foreach (var child in empty.Children) ((FrameworkElement)child).HorizontalAlignment = HorizontalAlignment.Center;
            return empty;
        }
        var rows = new StackPanel { Spacing = 8, Padding = new Thickness(32, 0, 32, 24) };
        foreach (var device in model.Devices) rows.Children.Add(Row(device));
        if (model.Page > 0 || model.HasNext)
        {
            rows.Children.Add(Ui.Split(
                Ui.Row(8, Ui.Plain(L("Previous"), () => _ = model.PreviousPage(), model.Page > 0 && !model.Loading), Ui.Plain(L("Next"), () => _ = model.NextPage(), model.HasNext && !model.Loading)),
                Ui.Text(model.Message, secondary: true)));
        }
        return new ScrollViewer { Content = rows, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
    }

    private string EmptyTitle()
    {
        if (Model.Loading) return L("Loading your devices…");
        if (!App.SignedIn) return L("Your devices belong here");
        if (Model.Search.Length > 0) return L("No matching devices");
        if (Model.Tenant is not null && Model.GroupFilter is not null) return L("No devices in this group");
        return L("No devices to display");
    }

    private string EmptyDetail()
    {
        if (!App.SignedIn) return L("Sign in to see the devices available to your account.");
        if (Model.Search.Length > 0) return L("Try another device name or clear the search.");
        if (Model.Tenant is not null)
        {
            return Model.Manages ? L("Add this computer, or add other computers with a deploy link.") : L("Devices you add appear here. Ask an administrator of your company for access to other devices.");
        }
        return L("Devices appear here after a Mixel team member enrolls them.");
    }

    private Border Row(Device device)
    {
        var model = Model;
        var presence = model.PresenceOf(device);
        var blocked = model.ConnectBlocked(device);
        var grid = new Grid { ColumnSpacing = 14 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var icon = Ui.Icon(Format.OsGlyph(device.Os), 24, presence is Presence.Online or Presence.This ? Ui.BrandBrush : Ui.Secondary);
        icon.VerticalAlignment = VerticalAlignment.Center;
        grid.Children.Add(icon);
        var details = string.Join(" · ", new[] { Format.Os(device.Os), device.GroupName, Format.RemoteId(device.ConnectionId) }.Where(part => !string.IsNullOrEmpty(part)));
        var name = Ui.Column(2, Ui.Text(device.Name, bold: true, wrap: false), Ui.Caption(details));
        Grid.SetColumn(name, 1);
        grid.Children.Add(name);
        var reason = model.Reason(device);
        var status = Ui.Column(2, Ui.Status(Badge(presence), model.Label(presence)), reason.Length > 0 ? Ui.Caption(reason) : null);
        status.MaxWidth = 300;
        status.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(status, 2);
        grid.Children.Add(status);
        var macs = model.KnownMacs(device);
        var actions = Ui.Row(8,
            presence == Presence.Offline && macs.Length > 0 ? Ui.Plain(L("Wake up"), () => _ = model.Wake(device, macs), !App.Busy, "\uE7E8") : null,
            model.Manages ? ManageButton(device) : null,
            Tip(Ui.Primary(L("Connect"), () => _ = model.Connect(device), blocked.Length == 0), blocked.Length == 0 ? L("Connect to %@", device.Name) : blocked));
        actions.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(actions, 3);
        grid.Children.Add(actions);
        var card = new Border
        {
            Child = grid,
            Padding = new Thickness(16, 12, 16, 12),
            CornerRadius = new CornerRadius(8),
            Background = Ui.Brush("CardBackgroundFillColorDefaultBrush"),
            BorderBrush = Ui.Brush("CardStrokeColorDefaultBrush"),
            BorderThickness = new Thickness(1),
        };
        AutomationProperties.SetName(card, $"{device.Name}, {model.Label(presence)}");
        return card;
    }

    private static Ui.Badge Badge(Presence presence) => presence switch
    {
        Presence.Online or Presence.This => Ui.Badge.Online,
        Presence.Offline => Ui.Badge.Offline,
        Presence.Checking => Ui.Badge.Busy,
        _ => Ui.Badge.Unknown,
    };

    private Button ManageButton(Device device)
    {
        var model = Model;
        var menu = new MenuFlyout();
        void Add(string text, Action action, bool enabled = true)
        {
            var item = new MenuFlyoutItem { Text = text, IsEnabled = enabled };
            item.Click += (_, _) => action();
            menu.Items.Add(item);
        }
        Add(L("Rename…"), () => _ = Rename(device), model.Writable);
        if (model.GroupsOn) Add(L("Move to group…"), () => _ = Move(device), model.Writable);
        menu.Items.Add(new MenuFlyoutSeparator());
        Add(L("Remove…"), () => _ = Remove(device));
        var button = new DropDownButton { Content = L("Manage"), Flyout = menu };
        AutomationProperties.SetName(button, L("Manage %@", device.Name));
        return button;
    }

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
        if (await Dialogs.Confirm(Root, L("Remove this device?"), L("Its running sessions end at once. It can be added again later."), L("Remove %@", device.Name)))
        {
            await Model.Remove(device);
        }
    }

    private async Task AddThisComputer()
    {
        var card = Shared.EnrollmentCard(App, this);
        await Dialogs.Show(Root, L("Add this computer"), card);
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
