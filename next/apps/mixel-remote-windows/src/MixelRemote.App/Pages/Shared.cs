using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MixelRemote.App.Shell;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>Cards used on more than one screen.</summary>
internal static class Shared
{
    /// <summary>Set up this computer once: the path depends on the account (company, Mixel team, personal).</summary>
    public static Border EnrollmentCard(AppModel app, PageView page)
    {
        var enrollment = app.Enrollment;
        var items = new List<UIElement?> { Ui.CardTitle(L("Set up this computer"), "\uE8D7") };
        TextBox Name(string key) => page.Keep(key, () =>
        {
            var box = new TextBox { Text = enrollment.Name, MaxWidth = 360, HorizontalAlignment = HorizontalAlignment.Left };
            box.TextChanged += (_, _) => enrollment.Name = box.Text;
            return box;
        });
        switch (enrollment.Path)
        {
            case EnrollmentPath.Company:
                items.Add(Ui.Text(L("Add this computer to %@ so you can connect from it and to it.", app.Company.Tenant?.Name ?? ""), secondary: true));
                items.Add(Ui.Field(L("Name of this computer"), Name("enrol-name")));
                if (app.Devices.Groups.Count > 0)
                {
                    items.Add(Ui.Field(L("Group"), Ui.Choice(
                        [("", L("No group")), .. app.Devices.Groups.Select(group => (group.GroupId, group.Name))],
                        page.Tag as string ?? "", value => page.Tag = value)));
                }
                items.Add(Ui.Primary(L("Add this computer"), () => _ = enrollment.AddToCompany(page.Tag as string is { Length: > 0 } group ? group : null),
                    !enrollment.Working && app.Devices.AddBlocked.Length == 0));
                if (app.Devices.AddBlocked.Length > 0) items.Add(Ui.Message(app.Devices.AddBlocked, error: false));
                break;
            case EnrollmentPath.CompanyMember:
                items.Add(Ui.Text(L("Devices you add appear here. Ask an administrator of your company for access to other devices."), secondary: true));
                break;
            case EnrollmentPath.Personal:
                items.Add(Ui.Text(L("Register this computer with your personal account."), secondary: true));
                items.Add(Ui.Field(L("Device name"), Name("enrol-name")));
                items.Add(Ui.Primary(L("Register this computer"), () => _ = enrollment.RegisterPersonal(), !enrollment.Working));
                break;
            case EnrollmentPath.Team:
                items.Add(Ui.Text(L("Enroll this computer against its existing ISM record, or add it to a company."), secondary: true));
                var search = page.Keep("enrol-search", () =>
                {
                    var box = new TextBox { PlaceholderText = L("Device name"), MinWidth = 260 };
                    box.TextChanged += (_, _) => enrollment.RecordSearch = box.Text;
                    return box;
                });
                items.Add(Ui.Row(8, search, Ui.Plain(L("Find device"), () => _ = enrollment.LoadTeamRecords())));
                foreach (var record in enrollment.Records)
                {
                    items.Add(Ui.Split(Ui.Row(10, Ui.Icon(Format.OsGlyph(record.Os)), Ui.Text(record.Name)),
                        Ui.Plain(L("Enroll this computer"), () => _ = enrollment.Enroll(record), !enrollment.Working)));
                }
                if (enrollment.Companies.Count > 0)
                {
                    var chosen = page.Tag as string is { Length: > 0 } picked ? picked : enrollment.Companies[0].ClientId;
                    items.Add(Ui.Heading(L("This computer is not in the list? Add it")));
                    items.Add(Ui.Field(L("Company"), Ui.Choice(enrollment.Companies.Select(company => (company.ClientId, company.ClientName)), chosen, value => page.Tag = value)));
                    items.Add(Ui.Field(L("Computer name"), Name("enrol-name")));
                    items.Add(Ui.Plain(L("Add and enroll this computer"), () => _ = enrollment.AddAndEnroll(page.Tag as string is { Length: > 0 } company ? company : chosen), !enrollment.Working));
                }
                break;
            default:
                items.Add(Ui.Text(L("Create a company to manage your devices and team, or a personal space for your own devices."), secondary: true));
                items.Add(Ui.Primary(L("Create a company"), () => app.Section = AppSection.Account));
                break;
        }
        if (enrollment.Working) items.Add(Ui.Spinner());
        items.Add(Ui.Message(enrollment.Error));
        return Ui.Card([.. items]);
    }

    /// <summary>While this computer is shared: who is connected, a colleague asking to join, a meeting offer, switching sides and ending.</summary>
    public static Border HostedSessionCard(AppModel app)
    {
        var remote = app.Remote;
        var host = app.Host;
        var items = new List<UIElement?>
        {
            Ui.CardTitle(L("A technician is connected to this computer."), "\uE95A"),
            Ui.Text(remote.Control ? L("They can see your screen and use keyboard and mouse.") : L("They can see your screen."), secondary: true),
        };
        if (remote.RecordingPermitted) items.Add(Ui.Row(8, Ui.Icon("\uE7C8", 14, Ui.BrandBrush), Ui.Text(L("You allowed the other person to save a video of this session."))));
        foreach (var colleague in remote.Colleagues)
        {
            items.Add(Ui.Split(
                Ui.Row(8, Ui.Icon("\uE77B", 14), Ui.Text(L("Colleague %@", Format.RemoteId(colleague.Connection))), Ui.Caption(colleague.Control ? L("View and control") : L("View only"))),
                Ui.Plain(L("Remove"), () => _ = host.RemoveColleague(colleague))));
        }
        if (remote.JoinRequest is { } request)
        {
            items.Add(Ui.Heading(L("A second technician wants to join")));
            items.Add(Ui.Text(L("Technician %@ was invited by the technician who is connected.", Format.RemoteId(request.Connection)), secondary: true));
            items.Add(Ui.Row(8,
                Ui.Plain(L("Decline"), () => _ = host.DecideJoin(false, false)),
                Ui.Plain(L("Allow viewing"), () => _ = host.DecideJoin(true, false)),
                request.Control ? Ui.Primary(L("Allow viewing and control"), () => _ = host.DecideJoin(true, true)) : null));
        }
        if (host.MeetingOffer is not null)
        {
            items.Add(Ui.Heading(L("The technician wants to show you their screen.")));
            items.Add(Ui.Row(8, Ui.Plain(L("Not now"), host.DismissMeetingOffer), Ui.Primary(L("Show it"), () => _ = host.OpenMeetingOffer())));
        }
        items.Add(Ui.Split(
            app.Session.CanSwitchSides && !remote.Switched ? Ui.Plain(L("Switch sides"), () => _ = app.Session.SwitchSides(), glyph: "\uE8AB") : new Grid(),
            Ui.Primary(L("End session"), () => _ = host.StopSharing(), glyph: "\uE711")));
        return Ui.Card([.. items]);
    }
}
