using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MixelRemote.App.Shell;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>Remote control: connect to another computer or phone, and allow remote access to this computer.</summary>
internal sealed class RemotePage : PageView
{
    private bool unattended;
    private Action? enter;

    public RemotePage(AppModel app) : base(app)
    {
        Watch(app.Host);
        Watch(app.Enrollment);
        Watch(app.Company);
        Watch(app.Devices);
    }

    private RemoteStatus Remote => App.Remote;

    protected override UIElement Build()
    {
        var cards = new Grid { ColumnSpacing = 16, RowSpacing = 16 };
        cards.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        cards.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var control = ControlCard();
        var allow = AllowCard();
        cards.Children.Add(control);
        Grid.SetColumn(allow, 1);
        cards.Children.Add(allow);
        return Page(L("Remote control"), App.ConnectionMessage, 1040,
            App.SignedIn ? null : SignInNotice(),
            App.SignedIn && App.Enrollment.NeedsSetup ? Shared.EnrollmentCard(App, this) : null,
            Remote.Invitations.Count > 0 ? Invitations() : null,
            Remote is { Phase: "active", Sharing: true } ? Shared.HostedSessionCard(App) : null,
            cards,
            App.Account.Account?.Staff == true ? PhoneCard() : null,
            !Remote.ControllerOnly && App.SignedIn && (Remote.Phase == "ready" || Remote.UnattendedEnabled) ? UnattendedCard() : null,
            Ui.Message(App.Error));
    }

    private Border SignInNotice() => Ui.Card(
        Ui.CardTitle(L("Connect with your Mixel account"), "\uE8D7"),
        Ui.Text(L("Sign in to get your connection ID."), secondary: true),
        Ui.Primary(L("Sign in"), () => App.Section = AppSection.Account));

    private Border ControlCard()
    {
        var partner = Keep("partner", () => new TextBox { PlaceholderText = "123 456 789", MaxWidth = 260, HorizontalAlignment = HorizontalAlignment.Left, FontSize = 18 });
        var password = Keep("password", () => new PasswordBox { MaxWidth = 260, HorizontalAlignment = HorizontalAlignment.Left });
        password.IsEnabled = !unattended;
        var ready = !App.Busy && App.SignedIn && Remote.Phase is "ready" or "offline" or "error";
        void Go()
        {
            if (Format.ParseRemoteId(partner.Text) is not { } id)
            {
                App.Error = L("Enter a nine-digit connection ID");
                return;
            }
            var supplied = password.Password;
            password.Password = "";
            _ = App.Connect(id, supplied, unattended);
        }
        var connect = Ui.Primary(L("Connect"), Go, ready, "\uE72A");
        if (partner.Tag is null)
        {
            partner.Tag = "wired";
            partner.KeyDown += (sender, key) =>
            {
                if (key.Key == Windows.System.VirtualKey.Enter) enter?.Invoke();
            };
        }
        enter = ready ? Go : null;
        var buttons = Ui.Row(8, connect, Remote.Phase == "outgoing" ? Ui.Plain(L("Cancel"), () => _ = App.Session.End()) : null);
        UIElement? progress = App.Progress.Length > 0 && (App.Busy || Remote.Phase == "outgoing")
            ? Ui.Row(8, Ui.Spinner(), Ui.Text(App.Progress, secondary: true))
            : null;
        return Ui.Card(
            Ui.CardTitle(L("Control a remote device"), "\uE7F4"),
            Ui.Text(L("Enter the 9-digit ID shown on the other computer."), secondary: true),
            Ui.Field(L("Partner connection ID"), partner),
            Ui.Field(L("One-time password, if provided"), password),
            Ui.Check(L("Connect without confirmation (unattended access)"), unattended, value => { unattended = value; Rebuild(); }),
            buttons,
            progress);
    }

    private Border AllowCard()
    {
        if (Remote.ControllerOnly)
        {
            return Ui.Card(Ui.CardTitle(L("Allow remote access"), "\uE785"), Ui.Text(L("This app controls other devices; it cannot be controlled itself."), secondary: true));
        }
        var id = Ui.Selectable(App.SignedIn ? Format.RemoteId(Remote.Host) : "— — —", 28);
        id.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(id, L("Your connection ID") + " " + Format.RemoteId(Remote.Host));
        var status = Ui.Split(
            Ui.Status(Remote.Connected ? Ui.Badge.Online : Remote.Phase == "error" ? Ui.Badge.Attention : Ui.Badge.Offline,
                Remote.Connected ? L("Online") : Remote.Phase == "error" ? L("Connection problem") : L("Offline")),
            Ui.Plain(L("Copy ID"), () => _ = App.Host.CopyId(), App.SignedIn && Remote.Host is not null, "\uE8C8"));
        var actions = Ui.Row(8,
            Remote.Disconnected
                ? Ui.Plain(L("Make this computer available"), () => _ = App.GoOnline(), !App.Busy && App.SignedIn)
                : Ui.Plain(L("Go offline"), () => _ = App.GoOffline(), !App.Busy),
            App.SignedIn && Remote.Host is not null
                ? Remote.CodeRequired
                    ? Ui.Plain(L("Stop requiring a password"), () => _ = App.Host.OneTimePassword(false))
                    : Ui.Plain(L("Generate one-time password"), () => _ = App.Host.OneTimePassword(true), Remote.Phase == "ready")
                : null);
        UIElement? code = null;
        if (Remote.SupportCode is { } supportCode && App.SignedIn)
        {
            code = Ui.Column(4,
                Ui.Field(L("One-time password"), Ui.Selectable(supportCode, 22, mono: true)),
                Ui.Caption(L("Share this password with your technician. It lasts five minutes and allows one request.")));
        }
        UIElement? pairing = null;
        if (App.SignedIn && Remote.Phase == "ready")
        {
            pairing = Remote.Pairing is { } prompt
                ? Ui.Column(8, Ui.Text(L("Pair this computer with %@ for %@?", prompt.ProviderName, prompt.CustomerName)), Ui.Primary(L("Confirm pairing"), () => _ = App.Host.ConfirmPairing()))
                : Ui.Row(12, Ui.Link(L("Check for pairing"), () => _ = App.Host.CheckPairing()),
                    Remote.PairingMessage is { } note ? Ui.Text(Translator.Shared.Translate(note), secondary: true) : null);
        }
        return Ui.Card(Ui.CardTitle(L("Allow remote access"), "\uE785"), Ui.Field(L("Your connection ID"), id), status, code, actions, pairing);
    }

    private Border PhoneCard()
    {
        var phone = Keep("phone", () => new TextBox { PlaceholderText = "123 456 789", MaxWidth = 260, HorizontalAlignment = HorizontalAlignment.Left, FontSize = 18 });
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(phone, "phone-id");
        return Ui.Card(
            Ui.CardTitle(L("Help someone on their phone"), "\uE8EA"),
            Ui.Text(L("The person opens Mixel Quick Support on their phone and tells you the ID it shows."), secondary: true),
            Ui.Field(L("ID from the phone"), phone),
            Ui.Primary(L("Connect"), () =>
            {
                if (Format.ParseRemoteId(phone.Text) is not { } id)
                {
                    App.Error = L("Enter a nine-digit connection ID");
                    return;
                }
                _ = App.Connect(id);
            }, !App.Busy && App.SignedIn, "\uE72A"));
    }

    private Border UnattendedCard()
    {
        var host = App.Host;
        var state = Remote.UnattendedPending == "enabling" ? L("Turning on. Waiting for the connection service.")
            : Remote.UnattendedPending is not null ? L("Sharing is off. Confirming with the connection service.")
            : Remote.UnattendedEnabled ? L("On. Approved users can connect while this computer stays signed in.")
            : L("Off. Choose a display and confirm before enabling.");
        var card = new List<UIElement?> { Ui.CardTitle(L("Unattended sharing"), "\uE823"), Ui.Text(state, secondary: true) };
        if (Remote.UnattendedEnabled || Remote.UnattendedPending == "enabling")
        {
            card.Add(Ui.Plain(L("Turn off unattended sharing"), () => _ = host.DisableUnattended()));
        }
        else if (host.UnattendedSources.Count == 0)
        {
            card.Add(Ui.Plain(L("Choose a display"), () => _ = host.LoadUnattendedSources(), !App.Busy));
        }
        else
        {
            card.Add(Ui.Field(L("Display"), Ui.Choice(host.UnattendedSources.Select(source => (source.Id, source.Name)), host.UnattendedSource, value => host.UnattendedSource = value ?? "")));
            card.Add(Ui.Check(L("Allow keyboard and mouse control"), host.UnattendedControl, value => host.UnattendedControl = value, Remote.InputAccess));
            card.Add(Ui.Check(L("I confirm that approved users may see this display without asking."), host.UnattendedConfirm, value => host.UnattendedConfirm = value));
            card.Add(Ui.Primary(L("Enable unattended sharing"), () => _ = host.EnableUnattended(), !App.Busy && host.UnattendedConfirm && !Remote.CodeRequired));
        }
        card.Add(Ui.Message(host.Error));
        return Ui.Card([.. card]);
    }

    private Border Invitations()
    {
        var items = new List<UIElement?> { Ui.CardTitle(L("A colleague invites you to a session"), "\uE716") };
        foreach (var invitation in Remote.Invitations)
        {
            items.Add(Ui.Split(
                Ui.Column(2, Ui.Text(L("Computer %@", Format.RemoteId(invitation.Host)), bold: true), Ui.Caption(invitation.Control ? L("View and control") : L("View only"))),
                Ui.Row(8, Ui.Plain(L("Not now"), () => _ = App.Session.Dismiss(invitation)), Ui.Primary(L("Join"), () => _ = App.Session.Join(invitation)))));
        }
        return Ui.Card([.. items]);
    }
}
