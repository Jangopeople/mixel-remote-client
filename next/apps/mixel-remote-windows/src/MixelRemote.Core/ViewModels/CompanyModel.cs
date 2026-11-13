using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>
/// The signed-in person's company (Mixel Remote SaaS tenant): plan, trial and usage, members and their
/// roles, invitations, renaming, creating a company, and deleting the company or one's account.
/// </summary>
public sealed class CompanyModel : Observable
{
    private readonly AppModel app;
    private List<Tenant> tenants = [];
    private TenantOverview? overview;
    private List<Member> members = [];
    private List<Invite> invites = [];
    private bool loaded;
    private string error = "";
    private string notice = "";
    private bool working;

    internal CompanyModel(AppModel app) => this.app = app;

    public List<Tenant> Tenants { get => tenants; private set => SetList(ref tenants, value); }
    public TenantOverview? Overview { get => overview; private set => SetList(ref overview, value); }
    public List<Member> Members { get => members; private set => SetList(ref members, value); }
    public List<Invite> Invites { get => invites; private set => SetList(ref invites, value); }
    public bool Loaded { get => loaded; private set => Set(ref loaded, value); }
    public string Error { get => error; set => Set(ref error, value); }
    public string Notice { get => notice; set => Set(ref notice, value); }
    public bool Working { get => working; private set => Set(ref working, value); }

    public Tenant? Tenant => Tenants.FirstOrDefault();
    public string Role => Overview?.Role ?? Tenant?.Role ?? "";
    public bool Administers => Role is "owner" or "admin";
    public bool IsOwner => Role == "owner";
    public bool ReadOnly => (Overview?.Status ?? Tenant?.Status ?? "active") is not ("active" or "trial" or "past_due");

    internal void Reset()
    {
        Tenants = [];
        Overview = null;
        Members = [];
        Invites = [];
        Loaded = false;
        Error = "";
        Notice = "";
    }

    public async Task Load()
    {
        if (!app.SignedIn)
        {
            Reset();
            return;
        }
        try
        {
            Tenants = await app.Engine.RunAsync<List<Tenant>>("saas_my_tenants");
            await LoadOverview();
            await LoadPeople();
        }
        catch (EngineFailure)
        {
            // Mixel team accounts, ISM companies and personal spaces have no Remote company.
            Tenants = [];
        }
        Loaded = true;
    }

    internal async Task LoadOverview()
    {
        if (Tenant is not { } tenant)
        {
            Overview = null;
            return;
        }
        try { Overview = await app.Engine.RunAsync<TenantOverview>("saas_tenant_overview", ("tenant", tenant.TenantId)); }
        catch (EngineFailure) { }
    }

    internal async Task LoadPeople()
    {
        if (Tenant is not { } tenant) return;
        try { Members = await app.Engine.RunAsync<List<Member>>("saas_members", ("tenant", tenant.TenantId)); }
        catch (EngineFailure) { Members = []; }
        try { Invites = Administers ? await app.Engine.RunAsync<List<Invite>>("saas_invites", ("tenant", tenant.TenantId)) : []; }
        catch (EngineFailure) { Invites = []; }
    }

    /// <summary>"Business trial until 27 Nov 2026" and similar.</summary>
    public string PlanLine
    {
        get
        {
            if (Tenant is not { } tenant) return "";
            var plan = Format.Plan(Overview?.Plan ?? tenant.Plan);
            return (Overview?.Status ?? tenant.Status) switch
            {
                "trial" => L("%@ trial until %@", plan, Format.Day(Overview?.TrialEndsAt ?? tenant.TrialEndsAt)),
                "read_only" or "expired" or "canceled" => L("%@ · read-only", plan),
                _ => plan,
            };
        }
    }

    public string UsageLine
    {
        get
        {
            if (Overview?.Usage is not { } usage) return "";
            var devices = Overview.Limits?.Devices is { } deviceLimit ? L("%d of %d devices", usage.Devices, deviceLimit) : L("%d devices", usage.Devices);
            var technicians = Overview.Limits?.Technicians is { } technicianLimit ? L("%d of %d technicians", usage.Technicians, technicianLimit) : L("%d technicians", usage.Technicians);
            return $"{devices} · {technicians}";
        }
    }

    public static string MailState(Invite invite) => invite.MailState switch
    {
        "sent" or "delivered" => L("E-mail sent"),
        "queued" or "sending" => L("E-mail on its way"),
        "failed" or "bounced" => L("E-mail failed"),
        _ => L("Expires %@", Format.Day(invite.ExpiresAt)),
    };

    public async Task<bool> Invite(string email, string role)
    {
        if (Tenant is not { } tenant) return false;
        email = email.Trim();
        if (!email.Contains('@', StringComparison.Ordinal) || email.Length > 254)
        {
            Error = L("Enter an e-mail address");
            return false;
        }
        return await Change(async () =>
        {
            await app.Engine.RunAsync<string>("saas_invite_member", ("tenant", tenant.TenantId), ("email", email), ("role", role), ("locale", Translator.Shared.Language));
            Notice = L("Invitation sent to %@.", email);
        });
    }

    public Task<bool> Resend(Invite invite) => Tenant is { } tenant
        ? Change(async () =>
        {
            await app.Engine.PerformAsync("saas_resend_invite", ("tenant", tenant.TenantId), ("invite", invite.Id), ("locale", Translator.Shared.Language));
            Notice = L("Invitation sent again to %@.", invite.Email);
        })
        : Task.FromResult(false);

    public Task<bool> Revoke(Invite invite) => Tenant is { } tenant
        ? Change(() => app.Engine.PerformAsync("saas_revoke_invite", ("tenant", tenant.TenantId), ("invite", invite.Id)))
        : Task.FromResult(false);

    public Task<bool> SetRole(Member member, string role) => Tenant is { } tenant
        ? Change(() => app.Engine.PerformAsync("saas_set_member_role", ("tenant", tenant.TenantId), ("member", member.UserId), ("role", role)))
        : Task.FromResult(false);

    /// <summary>Removes a member; for oneself: leaves the company.</summary>
    public async Task<bool> Remove(Member member)
    {
        if (Tenant is not { } tenant) return false;
        var done = await Change(() => app.Engine.PerformAsync("saas_remove_member", ("tenant", tenant.TenantId), ("member", member.UserId)));
        if (done && member.UserId == app.Account.Account?.Profile)
        {
            await app.RefreshAccount();
            await Load();
        }
        return done;
    }

    public Task<bool> Rename(string name) => Tenant is { } tenant
        ? Change(async () =>
        {
            await app.Engine.RunAsync<string>("saas_rename_tenant", ("tenant", tenant.TenantId), ("name", name));
            Tenants = await app.Engine.RunAsync<List<Tenant>>("saas_my_tenants");
        })
        : Task.FromResult(false);

    /// <summary>Creates a company with a 14-day Business trial; this account becomes its owner.</summary>
    public async Task<bool> Create(string name, bool acceptedTerms)
    {
        name = name.Trim();
        if (!acceptedTerms)
        {
            Error = L("Accept the Terms of Service and the Data Processing Agreement to create a company.");
            return false;
        }
        if (name.Length == 0)
        {
            Error = L("Enter a company name");
            return false;
        }
        var done = await Change(async () =>
        {
            if (!app.Remote.Disconnected) await app.Engine.PerformAsync("remote_disconnect");
            await app.Engine.RunAsync<SignupResult>("saas_signup", ("company", name), ("terms", true));
        });
        if (done)
        {
            await app.RefreshAccount();
            await Load();
        }
        return done;
    }

    public async Task<bool> DeleteCompany(string confirmName)
    {
        if (Tenant is not { } tenant) return false;
        var done = await Change(() => app.Engine.PerformAsync("saas_delete_company", ("tenant", tenant.TenantId), ("name", confirmName)));
        if (done)
        {
            await app.RefreshAccount();
            await Load();
        }
        return done;
    }

    public async Task<bool> DeleteAccount(string confirmEmail)
    {
        var done = await Change(() => app.Engine.PerformAsync("saas_delete_my_account", ("email", confirmEmail)));
        if (done) await app.SignOut();
        return done;
    }

    public async Task<bool> CreatePersonalSpace(string name)
    {
        var done = await Change(async () =>
        {
            if (!app.Remote.Disconnected) await app.Engine.PerformAsync("remote_disconnect");
            await app.Engine.PerformAsync("account_create_personal", ("name", name));
        });
        if (done) await app.RefreshAccount();
        return done;
    }

    // ---- Session report ----

    private List<SessionRow> report = [];
    private bool reportLoaded;
    public List<SessionRow> Report { get => report; private set => SetList(ref report, value); }
    public bool ReportLoaded { get => reportLoaded; private set => Set(ref reportLoaded, value); }

    /// <summary>Sessions of the last <paramref name="days"/> days (owners and administrators see every technician's).</summary>
    public async Task LoadReport(int days = 30)
    {
        if (Tenant is not { } tenant) return;
        Error = "";
        try
        {
            var from = DateTimeOffset.UtcNow.AddDays(-days).ToString("yyyy-MM-ddTHH:mm:ssZ", System.Globalization.CultureInfo.InvariantCulture);
            Report = await app.Engine.RunAsync<List<SessionRow>>("saas_session_report", ("tenant", tenant.TenantId), ("from", from), ("to", null), ("technician", null), ("device", null));
        }
        catch (EngineFailure failure) { Error = failure.Translated; Report = []; }
        ReportLoaded = true;
    }

    /// <summary>The report as CSV (UTF-8, semicolons for Swiss spreadsheets, a header row).</summary>
    public static string ReportCsv(IEnumerable<SessionRow> rows)
    {
        static string Cell(string? value) => "\"" + (value ?? "").Replace("\"", "\"\"", StringComparison.Ordinal) + "\"";
        var lines = new List<string> { "Started;Ended;Minutes;Technician;E-mail;Device;Remote ID;Kind;Colleague;Note" };
        foreach (var row in rows)
        {
            lines.Add(string.Join(';', Cell(row.StartedAt), Cell(row.EndedAt), (row.DurationSeconds / 60.0).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture),
                Cell(row.TechnicianName), Cell(row.TechnicianEmail), Cell(row.DeviceName), row.ConnectionId.ToString(System.Globalization.CultureInfo.InvariantCulture),
                row.Kind, row.Colleague ? "yes" : "no", Cell(row.Note)));
        }
        return string.Join("\r\n", lines) + "\r\n";
    }

    /// <summary>A note on a session: by its id, or the newest session to this Remote ID.</summary>
    public async Task<bool> AddSessionNote(string note, string? session, uint? connection)
    {
        if (Tenant is not { } tenant || note.Trim().Length == 0) return false;
        try
        {
            await app.Engine.PerformAsync("saas_session_note", ("tenant", tenant.TenantId), ("note", note.Trim()), ("session", session), ("connection", connection));
            return true;
        }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
            return false;
        }
    }

    /// <summary>Opens the portal page of a company the account administers (ISM companies).</summary>
    public Task OpenCompanySettings(string company) => app.Act(async () =>
    {
        app.OpenUrl(await app.Engine.RunAsync<string>("company_settings_url", ("company_id", company)));
    });

    public Task OpenDataExport() => app.Act(async () =>
    {
        app.OpenUrl(await app.Engine.RunAsync<string>("legal_url", ("page", "company-data"), ("lang", Translator.Shared.Language)));
    });

    private async Task<bool> Change(Func<Task> work)
    {
        Error = "";
        Notice = "";
        Working = true;
        try
        {
            await work();
            await LoadOverview();
            await LoadPeople();
            return true;
        }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
            return false;
        }
        finally
        {
            Working = false;
        }
    }
}
