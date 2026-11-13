using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>Whether a listed device can be reached.</summary>
public enum Presence { This, Online, Offline, Checking, Unknown }

/// <summary>
/// The devices of the signed-in account: a company's devices (with groups, the Manage menu and the
/// connection service's last record), or the devices a Mixel team member or a personal account may reach.
/// Says for every device whether it can be reached, and why not.
/// </summary>
public sealed class DevicesModel : Observable
{
    public const int PageSize = 50;
    private readonly AppModel app;
    private List<Device> devices = [];
    private bool loading;
    private string search = "";
    private string? groupFilter;
    private List<SaasGroup> groups = [];
    private int page;
    private bool hasNext;
    private string message = "";
    private string error = "";
    private Dictionary<uint, SaasPresence> serverPresence = [];
    private DeployLinkCreated? deployLink;
    private readonly HashSet<uint> checkedHosts = [];
    private List<uint?> cursors = [null];
    private uint? nextCursor;
    private List<Device> companyAll = [];
    private DateTimeOffset lastPresence = DateTimeOffset.MinValue;

    internal DevicesModel(AppModel app) => this.app = app;

    public List<Device> Devices { get => devices; private set => SetList(ref devices, value); }
    public bool Loading { get => loading; private set => Set(ref loading, value); }
    public string Search { get => search; set => Set(ref search, value); }
    public string? GroupFilter { get => groupFilter; set => Set(ref groupFilter, value); }
    public List<SaasGroup> Groups { get => groups; private set => SetList(ref groups, value); }
    public int Page { get => page; private set => Set(ref page, value); }
    public bool HasNext { get => hasNext; private set => Set(ref hasNext, value); }
    public string Message { get => message; private set => Set(ref message, value); }
    public string Error { get => error; set => Set(ref error, value); }
    public Dictionary<uint, SaasPresence> ServerPresence { get => serverPresence; private set => SetList(ref serverPresence, value); }
    /// <summary>The deploy link just created (shown once).</summary>
    public DeployLinkCreated? DeployLink { get => deployLink; set => Set(ref deployLink, value); }

    public Tenant? Tenant => app.Company.Tenant;
    public string Role => app.Company.Overview?.Role ?? Tenant?.Role ?? "";
    public bool Manages => Tenant is not null && Role is "owner" or "admin" or "technician";
    public bool Deploys => Tenant is not null && Role is "owner" or "admin";
    public bool GroupsOn => app.Company.Overview?.Features?.GetValueOrDefault("groups") ?? false;
    public bool Writable => (app.Company.Overview?.Status ?? Tenant?.Status ?? "") is "active" or "trial" or "past_due";
    public int Room => app.Company.Overview is { Limits.Devices: { } limit, Usage: { } usage } ? limit - usage.Devices : int.MaxValue;

    /// <summary>Why adding devices is not possible right now ("" when it is).</summary>
    public string AddBlocked => !Writable ? L("This company is read-only") : Room <= 0 ? L("Device limit of your plan reached") : "";

    /// <summary>This computer is already one of the listed devices.</summary>
    public bool RegisteredHere => app.Remote.Host is > 0 || app.Enrollment.Current is not null;

    internal void Reset()
    {
        Devices = [];
        companyAll = [];
        Groups = [];
        GroupFilter = null;
        Page = 0;
        HasNext = false;
        cursors = [null];
        nextCursor = null;
        checkedHosts.Clear();
        ServerPresence = [];
        Message = "";
        Error = "";
        DeployLink = null;
    }

    public async Task Load(int wanted = 0)
    {
        if (!app.SignedIn)
        {
            Reset();
            return;
        }
        Loading = true;
        Error = "";
        try
        {
            if (Tenant is { } tenant)
            {
                if (wanted == 0 || companyAll.Count == 0)
                {
                    var rows = await app.Engine.RunAsync<List<SaasDevice>?>("saas_devices", ("tenant", tenant.TenantId), ("group", GroupFilter), ("search", Search.Length == 0 ? null : Search));
                    companyAll = [.. (rows ?? []).Select(row => row.AsDevice()).OfType<Device>()];
                    if (Manages && GroupsOn)
                    {
                        try { Groups = await app.Engine.RunAsync<List<SaasGroup>>("saas_groups", ("tenant", tenant.TenantId)); }
                        catch (EngineFailure) { Groups = []; }
                    }
                    await LoadServerPresence();
                }
                Page = Math.Max(0, Math.Min(wanted, Math.Max(0, (companyAll.Count - 1) / PageSize)));
                Devices = [.. companyAll.Skip(Page * PageSize).Take(PageSize)];
                HasNext = (Page + 1) * PageSize < companyAll.Count;
            }
            else
            {
                if (wanted == 0) cursors = [null];
                var cursor = wanted < cursors.Count ? cursors[wanted] : nextCursor;
                var result = await app.Engine.RunAsync<DevicePage?>("remote_devices", ("after", cursor), ("search", Search)) ?? new DevicePage();
                Devices = result.Devices;
                Page = wanted;
                nextCursor = result.Next;
                HasNext = result.Next is not null;
                if (cursors.Count <= wanted + 1) cursors.Add(result.Next);
                else cursors[wanted + 1] = result.Next;
            }
            Message = Devices.Count == 0 ? "" : L("Page %d · %d devices.", Page + 1, Devices.Count);
            await CheckPresence(force: true);
        }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
        }
        finally
        {
            Loading = false;
        }
    }

    public Task NextPage() => Load(Page + 1);
    public Task PreviousPage() => Load(Math.Max(0, Page - 1));

    private async Task LoadServerPresence()
    {
        if (Tenant is not { } tenant) return;
        try
        {
            var rows = await app.Engine.RunAsync<List<SaasPresence>>("saas_device_presence", ("tenant", tenant.TenantId));
            var map = new Dictionary<uint, SaasPresence>();
            foreach (var row in rows)
            {
                if (row.ConnectionId is { } id) map.TryAdd(id, row);
            }
            ServerPresence = map;
        }
        catch (EngineFailure) { }
    }

    /// <summary>Asks the connection service which listed devices are online (every 15 s while connected).</summary>
    internal async Task CheckPresence(bool force = false)
    {
        if (!app.Remote.Connected || Devices.Count == 0 || (!force && DateTimeOffset.UtcNow - lastPresence < TimeSpan.FromSeconds(15))) return;
        lastPresence = DateTimeOffset.UtcNow;
        var hosts = Devices.Select(device => device.ConnectionId).Where(id => id != app.Remote.Host).Take(100).ToArray();
        if (hosts.Length == 0) return;
        try { await app.Engine.PerformAsync("remote_presence", ("hosts", hosts)); } catch (EngineFailure) { }
        // The answer arrives in remote_status.online; the next read shows it.
        await Task.Delay(force ? 0 : 600);
        foreach (var host in hosts) checkedHosts.Add(host);
        Raise(nameof(Devices));
    }

    internal void StatusChanged(RemoteStatus previous, RemoteStatus next)
    {
        if (next.Connected && !previous.Connected) lastPresence = DateTimeOffset.MinValue;
        if (next.Connected) _ = CheckPresence();
        if (!next.Online.SequenceEqual(previous.Online) || next.Connected != previous.Connected || next.Host != previous.Host) Raise(nameof(Devices));
    }

    // ---- What a row shows ----

    public Presence PresenceOf(Device device)
    {
        var connected = app.Remote.Connected;
        if (device.ConnectionId == app.Remote.Host) return Presence.This;
        if (!connected)
        {
            return ServerPresence.TryGetValue(device.ConnectionId, out var recorded)
                ? recorded.Status == "online" ? Presence.Online : Presence.Offline
                : Presence.Unknown;
        }
        if (app.Remote.Online.Contains(device.ConnectionId)) return Presence.Online;
        return checkedHosts.Contains(device.ConnectionId) ? Presence.Offline : Presence.Checking;
    }

    public string Label(Presence presence) => presence switch
    {
        Presence.This => app.Remote.Connected ? L("This computer") : L("This computer · not connected"),
        Presence.Online => L("Online"),
        Presence.Offline => L("Offline"),
        Presence.Checking => L("Checking…"),
        _ => L("Status unknown"),
    };

    public string Reason(Device device)
    {
        switch (PresenceOf(device))
        {
            case Presence.This:
                return app.Remote.Connected ? L("You are using this device.") : L("Reconnecting automatically.");
            case Presence.Offline:
                var seen = ServerPresence.TryGetValue(device.ConnectionId, out var row) ? Format.LastSeen(row.LastSeen) : "";
                return (seen.Length == 0 ? "" : seen + ". ") + L("Turned off, asleep or without internet. It must be online to connect.");
            case Presence.Unknown:
                return L("Shown once this computer is connected.");
            case Presence.Online:
                return L("Ready. The person there accepts your request, unless unattended access is set up.");
            default:
                return "";
        }
    }

    /// <summary>Why Connect is not possible ("" when it is).</summary>
    public string ConnectBlocked(Device device)
    {
        if (device.ConnectionId == app.Remote.Host) return L("This is the computer you are using.");
        if (app.Remote.InSession) return L("End the current session first.");
        if (PresenceOf(device) == Presence.Offline) return L("This device is offline.");
        if (app.Busy) return L("Please wait…");
        if (!app.SignedIn) return L("Sign in first.");
        return "";
    }

    public Task Connect(Device device)
    {
        Message = L("Connecting to %@…", device.Name);
        app.Section = AppSection.Remote;
        return app.Connect(device.ConnectionId);
    }

    private const string MacsKey = "mixel.remote.macs.v1";

    private Dictionary<string, List<string>> Macs()
    {
        try { return System.Text.Json.JsonSerializer.Deserialize<Dictionary<string, List<string>>>(app.Preferences.Get(MacsKey) ?? "{}") ?? []; }
        catch (System.Text.Json.JsonException) { return []; }
    }

    /// <summary>The network addresses a computer was seen with in a session (for Wake-on-LAN), as the 0.1 app kept them.</summary>
    public string[] KnownMacs(Device device) =>
        Macs().TryGetValue(device.ConnectionId.ToString(System.Globalization.CultureInfo.InvariantCulture), out var macs) ? [.. macs.Take(4)] : [];

    internal void RememberMacs(uint host, IReadOnlyList<string> macs)
    {
        var known = Macs();
        var key = host.ToString(System.Globalization.CultureInfo.InvariantCulture);
        if (known.TryGetValue(key, out var before) && before.SequenceEqual(macs)) return;
        known[key] = [.. macs.Take(4)];
        app.Preferences.Set(MacsKey, System.Text.Json.JsonSerializer.Serialize(known));
    }

    // ---- Manage (company devices) ----

    public Task<bool> Rename(Device device, string name) => Tenant is { } tenant
        ? Change(() => app.Engine.RunAsync<string>("saas_rename_device", ("tenant", tenant.TenantId), ("device", device.DeviceId), ("name", name)))
        : Task.FromResult(false);

    public Task<bool> Move(Device device, string? group) => Tenant is { } tenant
        ? Change(() => app.Engine.PerformAsync("saas_move_device", ("tenant", tenant.TenantId), ("device", device.DeviceId), ("group", group)))
        : Task.FromResult(false);

    public Task<bool> Remove(Device device) => Tenant is { } tenant
        ? Change(() => app.Engine.PerformAsync("saas_remove_device", ("tenant", tenant.TenantId), ("device", device.DeviceId)))
        : Task.FromResult(false);

    public Task<bool> CreateGroup(string name) => Tenant is { } tenant
        ? Change(() => app.Engine.PerformAsync("saas_create_group", ("tenant", tenant.TenantId), ("name", name)))
        : Task.FromResult(false);

    /// <summary>"Add this computer": this computer's device key becomes one of the company's devices.</summary>
    public async Task<bool> AddThisComputer(string name, string? group)
    {
        if (Tenant is not { } tenant) return false;
        var wasOnline = !app.Remote.Disconnected;
        var done = await Change(async () =>
        {
            if (wasOnline) await app.Engine.PerformAsync("remote_disconnect");
            var enrolled = await app.Engine.RunAsync<SaasEnrollment>("saas_add_this_device", ("tenant", tenant.TenantId), ("name", name), ("group", group));
            app.Enrollment.Remember(enrolled.DeviceId, enrolled.ConnectionId, enrolled.Generation, tenant.TenantId);
        });
        if (done)
        {
            await app.GoOnline();
            await app.Company.LoadOverview();
        }
        return done;
    }

    /// <summary>"Add other computers": a deploy link with the command for each system (shown once).</summary>
    public Task<bool> CreateDeployLink(string label, int days, int computers, string? group) => Tenant is { } tenant
        ? Change(async () =>
        {
            DeployLink = await app.Engine.RunAsync<DeployLinkCreated>("saas_create_deploy_link",
                ("tenant", tenant.TenantId), ("label", label), ("days", days), ("computers", computers), ("group", group));
        })
        : Task.FromResult(false);

    // ---- Mixel team: mass rollout for an ISM company ----

    private List<RolloutToken> rollouts = [];
    public List<RolloutToken> Rollouts { get => rollouts; private set => SetList(ref rollouts, value); }
    /// <summary>Mixel team accounts roll Mixel Remote out to an ISM company's computers.</summary>
    public bool RollsOut => Tenant is null && app.Account.Account?.Staff == true;

    public async Task<List<EnrollmentCompany>> RolloutCompanies()
    {
        try { return await app.Engine.RunAsync<List<EnrollmentCompany>>("enrollment_companies"); }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
            return [];
        }
    }

    public async Task LoadRollouts(string company)
    {
        try { Rollouts = await app.Engine.RunAsync<List<RolloutToken>>("rollout_list", ("company", company)); }
        catch (EngineFailure failure) { Error = failure.Translated; }
    }

    public async Task<bool> CreateRollout(string company, string label, int days, int computers)
    {
        Error = "";
        try
        {
            DeployLink = await app.Engine.RunAsync<DeployLinkCreated>("rollout_create", ("company", company), ("label", label), ("days", days), ("computers", computers));
            await LoadRollouts(company);
            return true;
        }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
            return false;
        }
    }

    public async Task RevokeRollout(string company, RolloutToken token)
    {
        try { await app.Engine.PerformAsync("rollout_revoke", ("id", token.Id)); }
        catch (EngineFailure failure) { Error = failure.Translated; }
        await LoadRollouts(company);
    }

    public Task Wake(Device device, string[] macs) => app.Act(async () =>
    {
        if (macs.Length == 0) throw new EngineFailure("No network address is known for this computer yet");
        await app.Engine.PerformAsync("wake_on_lan", ("macs", macs));
        Message = L("Wake signal sent to %@. It shows Online when it has started.", device.Name);
    });

    private async Task<bool> Change(Func<Task> work)
    {
        Error = "";
        try
        {
            await work();
            await Load(0);
            return true;
        }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
            return false;
        }
    }
}
