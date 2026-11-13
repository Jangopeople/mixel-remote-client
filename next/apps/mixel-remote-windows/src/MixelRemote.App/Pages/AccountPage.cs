using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MixelRemote.App.Shell;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>
/// Account: sign in with Mixel ID in the system browser (and its second step), the company with plan,
/// usage, members, roles and invitations, creating a company or a personal space, deleting the company
/// or the account.
/// </summary>
internal sealed class AccountPage : PageView
{
    private bool terms;
    private bool? signup;
    private bool providersLoaded;

    public AccountPage(AppModel app) : base(app) => Watch(app.Company);

    private CompanyModel Company => App.Company;

    protected override UIElement Build()
    {
        var account = App.Account;
        if (account.SignedIn && account.Account is { } profile)
        {
            return Page(L("Account"), "", 860,
                ProfileCard(profile),
                Company.Tenant is not null ? CompanyCard() : Company.Loaded && !profile.Staff ? CreateCompanyCard(profile) : null,
                DangerCard(),
                Ui.Message(App.Error));
        }
        return Page(L("Account"), L("A secure home for your devices and connections."), 860, SignInCard(account), Ui.Message(App.Error));
    }

    private Border SignInCard(AccountStatus account)
    {
        var items = new List<UIElement?> { Ui.CardTitle(L("Connect with your Mixel account"), "\uE8D7") };
        switch (account.Phase)
        {
            case "starting" or "waiting_browser" or "verifying":
                items.Add(Ui.Row(10, Ui.Spinner(), Ui.Text(Translator.Shared.Translate(account.Message))));
                if (App.SignInLink is { } link)
                {
                    var text = Ui.Selectable(link, mono: true);
                    text.FontSize = 12;
                    text.MaxLines = 2;
                    items.Add(Ui.Field(L("Open this link in your other browser"), Ui.Split(text, Ui.IconButton("\uE8C8", L("Copy"), () => App.CopyText(link)))));
                }
                items.Add(Ui.Plain(L("Cancel sign-in"), () => _ = App.CancelSignIn()));
                break;
            case "profile_setup":
                items.Add(Ui.Text(L("Choose the name shown on your devices to finish setting up your Remote account."), secondary: true));
                var display = Keep("display-name", () => new TextBox { MaxWidth = 360, HorizontalAlignment = HorizontalAlignment.Left });
                items.Add(Ui.Field(L("Your name"), display));
                items.Add(Ui.Row(8, Ui.Primary(L("Set up Remote account"), () => _ = App.SetUpProfile(display.Text), !App.Busy), Ui.Plain(L("Cancel sign-in"), () => _ = App.CancelSignIn())));
                break;
            case "mfa_required":
                items.Add(Ui.Text(L("Additional verification is required for this Mixel account"), secondary: true));
                var factor = account.Factors.FirstOrDefault();
                if (factor?.FriendlyName is { } name) items.Add(Ui.Text(L("Enter the code from the authenticator entry “%@”.", name)));
                var code = Keep("code", () =>
                {
                    var box = new TextBox { PlaceholderText = "123456", MaxWidth = 200, HorizontalAlignment = HorizontalAlignment.Left, FontSize = 20, InputScope = new Microsoft.UI.Xaml.Input.InputScope { Names = { new Microsoft.UI.Xaml.Input.InputScopeName(Microsoft.UI.Xaml.Input.InputScopeNameValue.Number) } } };
                    box.TextChanged += (_, _) => App.VerificationCode = box.Text;
                    box.KeyDown += (sender, key) =>
                    {
                        if (key.Key == Windows.System.VirtualKey.Enter && App.Account.Factors.FirstOrDefault() is { } first) _ = Verify(box, first.Id);
                    };
                    return box;
                });
                items.Add(Ui.Field(L("Verification code"), code));
                items.Add(Ui.Row(8,
                    Ui.Primary(L("Verify"), () => { if (factor is not null) _ = Verify(code, factor.Id); }, factor is not null && !App.Busy),
                    Ui.Plain(L("Cancel sign-in"), () => _ = App.CancelSignIn())));
                break;
            default:
                if (!providersLoaded)
                {
                    providersLoaded = true;
                    _ = App.LoadProviders();
                }
                items.Add(Ui.Text(L("Sign in with your account. Your browser opens; come back here when it says you are signed in."), secondary: true));
                if (account.Phase == "error") items.Add(Ui.Message(Translator.Shared.Translate(account.Message)));
                var email = Keep("signin-email", () =>
                {
                    var box = new TextBox { PlaceholderText = "name@company.ch", MinWidth = 260 };
                    box.KeyDown += (sender, key) => { if (key.Key == Windows.System.VirtualKey.Enter) _ = App.LookUpSignIn(box.Text); };
                    return box;
                });
                items.Add(Ui.Field(L("Work e-mail (optional: shows your company's sign-in methods)"), Ui.Row(8, email, Ui.Plain(L("Continue"), () => _ = App.LookUpSignIn(email.Text), !App.Busy))));
                if (App.SignInOptions is { Company: { } company } options)
                {
                    items.Add(Ui.Message(options.RequireMfa ? L("%@ asks for a second step at sign-in.", company) : L("Sign in the way %@ allows.", company), error: false));
                }
                var buttons = new StackPanel { Spacing = 8, MaxWidth = 360, HorizontalAlignment = HorizontalAlignment.Left };
                var first = true;
                foreach (var (provider, label) in App.SignInButtons)
                {
                    var button = first ? Ui.Primary(L("Continue with %@", label), () => _ = App.SignIn(provider: provider), !App.Busy)
                        : Ui.Plain(L("Continue with %@", label), () => _ = App.SignIn(provider: provider), !App.Busy);
                    button.HorizontalAlignment = HorizontalAlignment.Stretch;
                    Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(button, "signin-" + provider);
                    buttons.Children.Add(button);
                    first = false;
                }
                items.Add(buttons);
                items.Add(Ui.Plain(L("Use another browser"), () => _ = App.SignIn(showLink: true, provider: App.SignInButtons.Select(item => item.Provider).FirstOrDefault()), !App.Busy));
                if (signup is null) _ = CheckSignup();
                if (signup == true) items.Add(Ui.Link(L("Create Mixel ID"), () => App.OpenUrl("https://auth.mixel.ch/if/flow/mixel-remote-enrollment/")));
                break;
        }
        return Ui.Card([.. items]);
    }

    private async Task CheckSignup()
    {
        signup = false;
        signup = await App.SignupAvailable();
        Rebuild();
    }

    private async Task Verify(TextBox box, string factor)
    {
        App.VerificationCode = box.Text;
        box.Text = "";
        await App.Verify(factor);
    }

    private string Kind(Account profile)
    {
        if (profile.Staff) return L("Mixel team account");
        if (profile.Personal) return L("Personal account");
        if (Company.Tenant is { } tenant) return $"{tenant.Name} · {Format.Role(tenant.Role)}";
        return L("Mixel account");
    }

    private Border ProfileCard(Account profile)
    {
        var picture = new PersonPicture { DisplayName = profile.DisplayName ?? "", Width = 48, Height = 48 };
        var who = Ui.Column(3,
            Ui.Text(profile.DisplayName ?? L("Signed in"), size: 18, bold: true),
            Ui.Text(Kind(profile), secondary: true),
            profile.Remembered ? Ui.Row(6, Ui.Icon("\uE8D7", 12, Ui.Secondary), Ui.Caption(L("Stays signed in on this computer"))) : null);
        var items = new List<UIElement?> { Ui.Split(Ui.Row(14, picture, who), Ui.Plain(L("Sign out"), () => _ = App.SignOut())) };
        if (profile.HasPersonal && profile.HasCompany)
        {
            var choice = Ui.Choice([("company", L("Company devices")), ("personal", L("Personal devices"))], profile.Personal ? "personal" : "company",
                value => _ = App.SetWorkspace(value == "personal"));
            items.Add(Ui.Field(L("Devices of"), choice));
        }
        if (profile.Companies.Count > 0)
        {
            items.Add(Ui.Heading(L("Company administration")));
            foreach (var item in profile.Companies)
            {
                items.Add(Ui.Split(Ui.Text(item.CompanyName), Ui.Plain(L("Manage devices and company"), () => _ = Company.OpenCompanySettings(item.CompanyId))));
            }
        }
        return Ui.Card([.. items]);
    }

    private Border CompanyCard()
    {
        var company = Company;
        var me = App.Account.Account?.Profile;
        var items = new List<UIElement?>
        {
            Ui.Split(Ui.CardTitle(company.Tenant?.Name ?? "", "\uE731"), company.Administers ? Ui.Plain(L("Rename company"), () => _ = Rename(), !company.ReadOnly) : null),
            Ui.Row(18, Ui.Row(6, Ui.Icon("\uE8C7", 14, Ui.Secondary), Ui.Text(company.PlanLine, secondary: true)),
                company.UsageLine.Length > 0 ? Ui.Row(6, Ui.Icon("\uE9D2", 14, Ui.Secondary), Ui.Text(company.UsageLine, secondary: true)) : null),
            company.ReadOnly ? Ui.Message(L("This company is read-only. You can still remove devices; adding devices and other changes need an active plan."), error: false) : null,
            Ui.Split(Ui.Heading(L("Members")), company.Administers ? Ui.Plain(L("Invite a person"), () => _ = Invite(), !company.ReadOnly, "\uE8FA") : null),
        };
        foreach (var member in company.Members)
        {
            var who = Ui.Column(1, Ui.Text(member.Name.Length == 0 ? member.Email : member.Name), Ui.Caption(member.Email));
            UIElement right;
            if (company.Administers && member.UserId != me)
            {
                var menu = new MenuFlyout();
                foreach (var role in new[] { "owner", "admin", "technician", "member" })
                {
                    var item = new MenuFlyoutItem { Text = Format.Role(role), IsEnabled = role != member.Role };
                    item.Click += (_, _) => _ = company.SetRole(member, role);
                    menu.Items.Add(item);
                }
                menu.Items.Add(new MenuFlyoutSeparator());
                var remove = new MenuFlyoutItem { Text = L("Remove from company") };
                remove.Click += async (_, _) =>
                {
                    if (await Dialogs.Confirm(Root, L("Remove from company"), member.Email, L("Remove"))) await company.Remove(member);
                };
                menu.Items.Add(remove);
                var button = new DropDownButton { Content = Format.Role(member.Role), Flyout = menu };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, L("Role") + " " + member.Email);
                right = button;
            }
            else
            {
                right = Ui.Row(8, Ui.Text(Format.Role(member.Role), secondary: true),
                    member.UserId == me && !company.IsOwner ? Ui.Plain(L("Leave"), () => _ = company.Remove(member)) : null);
            }
            items.Add(Ui.Split(who, right));
        }
        if (company.Administers && company.Invites.Count > 0)
        {
            items.Add(Ui.Heading(L("Open invitations")));
            foreach (var invite in company.Invites)
            {
                items.Add(Ui.Split(
                    Ui.Column(1, Ui.Text(invite.Email), Ui.Caption($"{Format.Role(invite.Role)} · {CompanyModel.MailState(invite)}")),
                    Ui.Row(8, Ui.Plain(L("Send again"), () => _ = company.Resend(invite)), Ui.Plain(L("Revoke"), () => _ = company.Revoke(invite)))));
            }
        }
        items.Add(Ui.Message(company.Notice, error: false));
        items.Add(Ui.Message(company.Error));
        if (company.IsOwner)
        {
            items.Add(Ui.Heading(L("Data and deletion")));
            items.Add(Ui.Row(8, Ui.Plain(L("Export data in the portal"), () => _ = company.OpenDataExport()), Ui.Plain(L("Delete company…"), () => _ = DeleteCompany())));
        }
        return Ui.Card([.. items]);
    }

    private Task Rename() => Dialogs.Form(Root, L("Rename company"), null, [(L("Company name"), Company.Tenant?.Name ?? "")], L("Rename"),
        async values => await Company.Rename(values[0].Trim()) ? null : Company.Error);

    private Task DeleteCompany() => Dialogs.Form(Root, L("Delete company"),
        L("Deletes the company at once: devices, invitations, members and their accounts. The subscription ends. Type the company name to confirm."),
        [(L("Company name"), "")], L("Delete company"), async values => await Company.DeleteCompany(values[0]) ? null : Company.Error, danger: true);

    /// <summary>Screenshots: the invitation dialog.</summary>
    public Task ShowInvite() => Invite();

    private async Task Invite()
    {
        var role = "technician";
        var choice = Ui.Choice(new[] { "admin", "technician", "member" }.Select(item => (item, Format.Role(item))), role, value => role = value ?? "technician");
        await Dialogs.Form(Root, L("Invite a person"), L("Mixel Remote e-mails the invitation link. Administrators and technicians count against the technicians of your plan."),
            [(L("E-mail"), "")], L("Send invitation"), async values => await Company.Invite(values[0], role) ? null : Company.Error, extra: Ui.Field(L("Role"), choice));
    }

    private Border CreateCompanyCard(Account profile)
    {
        var name = Keep("company-name", () => new TextBox { MaxWidth = 360, HorizontalAlignment = HorizontalAlignment.Left });
        var items = new List<UIElement?>
        {
            Ui.CardTitle(L("Create a company"), "\uE731"),
            Ui.Text(L("Your company gets a 14-day Business trial, no card needed. You become its owner and can invite your team."), secondary: true),
            Ui.Field(L("Company name"), name),
            Ui.Check(L("I accept the Terms of Service and the Data Processing Agreement"), terms, value => { terms = value; Rebuild(); }),
            Ui.Link(L("Read the Terms of Service, the Data Processing Agreement and the Privacy Policy"), () => _ = App.Settings.OpenLegal("legal")),
            Ui.Primary(L("Create company"), () => _ = Company.Create(name.Text, terms), !Company.Working),
        };
        if (!profile.HasPersonal)
        {
            var personal = Keep("personal-name", () => new TextBox { PlaceholderText = L("Name shown to your own devices"), MinWidth = 280 });
            items.Add(Ui.Heading(L("Or a personal space for your own devices")));
            items.Add(Ui.Row(8, personal, Ui.Plain(L("Create personal space"), () => { if (personal.Text.Trim().Length > 0) _ = Company.CreatePersonalSpace(personal.Text.Trim()); })));
        }
        items.Add(Ui.Message(Company.Error));
        return Ui.Card([.. items]);
    }

    private Border DangerCard() => Ui.Card(
        Ui.CardTitle(L("Delete my account"), "\uE74D"),
        Ui.Text(L("Deletes your Mixel Remote account and signs you out on every device. Company data stays with the company."), secondary: true),
        Ui.Plain(L("Delete my account…"), () => _ = Dialogs.Form(Root, L("Delete my account"), L("Type your e-mail address to confirm. This cannot be undone."),
            [(L("E-mail"), "")], L("Delete my account"), async values => await Company.DeleteAccount(values[0].Trim()) ? null : Company.Error, danger: true)));
}
