using System.Globalization;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>The views chosen in the sidebar.</summary>
public enum DeviceViewKind { All, Online, Favorites, Recent, Group }

/// <summary>A view of the device list: one of the four built-in ones, or a group.</summary>
public readonly record struct DeviceView(DeviceViewKind Kind, string Group = "")
{
    public static readonly DeviceView All = new(DeviceViewKind.All);
    public static readonly DeviceView Online = new(DeviceViewKind.Online);
    public static readonly DeviceView Favorites = new(DeviceViewKind.Favorites);
    public static readonly DeviceView Recent = new(DeviceViewKind.Recent);
    public static DeviceView InGroup(string name) => new(DeviceViewKind.Group, name);
}

/// <summary>A group of rows in the table.</summary>
public sealed record RowGroup(string Id, string Name, List<Device> Devices, int Online);

// The Devices console ("Pro Console"): views, quick filters, the grouped table, and the admin badges.
public sealed partial class DevicesModel
{
    private DeviceView view = DeviceView.All;
    private bool onlineOnly;
    private bool unattendedOnly;
    private bool updateOnly;
    private HashSet<string> systems = [];
    private string filterText = "";
    private Dictionary<uint, AdminDevice> admin = [];
    private bool adminAvailable;

    public DeviceView View { get => view; set => Set(ref view, value); }
    public bool OnlineOnly { get => onlineOnly; set => Set(ref onlineOnly, value); }
    public bool UnattendedOnly { get => unattendedOnly; set => Set(ref unattendedOnly, value); }
    public bool UpdateOnly { get => updateOnly; set => Set(ref updateOnly, value); }
    public HashSet<string> Systems { get => systems; private set => SetList(ref systems, value); }
    /// <summary>Typed into the console's search field: name, ID, group or note (filters at once, no round trip).</summary>
    public string FilterText { get => filterText; set => Set(ref filterText, value); }

    /// <summary>The device administration behind the badges; replace it to wire a different source.</summary>
    public IDeviceAdmin DeviceAdmin { get; set; } = null!;
    /// <summary>The engine answers the remote_admin_* commands (badges and notes are shown only then).</summary>
    public bool AdminAvailable { get => adminAvailable; private set => Set(ref adminAvailable, value); }

    public void ToggleSystem(string system)
    {
        var next = new HashSet<string>(Systems);
        if (!next.Remove(system)) next.Add(system);
        Systems = next;
    }

    public bool AnyFilter => FilterText.Trim().Length > 0 || OnlineOnly || UnattendedOnly || UpdateOnly || Systems.Count > 0;

    /// <summary>Every listed device: all of the company's devices, or this page of the directory.</summary>
    public IReadOnlyList<Device> AllDevices => Tenant is not null ? companyAll : Devices;

    /// <summary>The device's name (its Remote ID when it is not in the list).</summary>
    public string NameOf(uint connection) => AllDevices.FirstOrDefault(device => device.ConnectionId == connection)?.Name ?? Format.RemoteId(connection);

    /// <summary>The device's operating system ("" when it is not in the list).</summary>
    public string OsOf(uint connection) => AllDevices.FirstOrDefault(device => device.ConnectionId == connection)?.Os ?? "";

    /// <summary>The time the connection service last saw the device.</summary>
    public DateTimeOffset? LastSeenDate(Device device)
    {
        if (ServerPresence.TryGetValue(device.ConnectionId, out var row) && Format.ParseDate(row.LastSeen) is { } seen) return seen;
        return AdminOf(device)?.LastSeen is { } text ? Format.ParseDate(text) : null;
    }

    /// <summary>Online, or this computer.</summary>
    public bool IsOnline(Device device) => PresenceOf(device) is Presence.Online or Presence.This;

    private bool InSessionWithThis(Device device) => app.Workspace.StateOf(device.ConnectionId) is not null;

    public List<Device> DevicesIn(DeviceView wanted)
    {
        var all = AllDevices;
        return wanted.Kind switch
        {
            DeviceViewKind.Online => [.. all.Where(device => IsOnline(device) || InSessionWithThis(device))],
            DeviceViewKind.Favorites => [.. all.Where(device => Favorites.Contains(device.DeviceId))],
            DeviceViewKind.Recent => [.. Recent.Select(row => all.FirstOrDefault(device => device.DeviceId == row.DeviceId)).OfType<Device>()],
            DeviceViewKind.Group => [.. all.Where(device => (device.GroupName ?? "") == wanted.Group)],
            _ => [.. all],
        };
    }

    /// <summary>The groups of the listed devices by name, with their counts.</summary>
    public IReadOnlyList<string> GroupNames => [.. AllDevices.Select(device => device.GroupName).OfType<string>().Where(name => name.Length > 0)
        .Distinct().OrderBy(name => name, StringComparer.Create(Translator.Shared.Culture, CompareOptions.IgnoreCase | CompareOptions.NumericOrdering))];

    /// <summary>The devices of the current view after the filters.</summary>
    public List<Device> Filtered
    {
        get
        {
            var text = FilterText.Trim().ToLowerInvariant();
            var digits = new string([.. text.Where(char.IsAsciiDigit)]);
            var spaceless = new string([.. text.Where(character => !char.IsWhiteSpace(character))]);
            return [.. DevicesIn(View).Where(device =>
            {
                var info = AdminOf(device);
                if (OnlineOnly && !IsOnline(device) && !InSessionWithThis(device)) return false;
                if (UnattendedOnly && info?.Unattended != true) return false;
                if (UpdateOnly && !UpdateAvailable(device)) return false;
                if (Systems.Count > 0 && !Systems.Contains(device.Os.ToLowerInvariant())) return false;
                if (text.Length == 0) return true;
                if (device.Name.Contains(text, StringComparison.OrdinalIgnoreCase)) return true;
                if (device.GroupName?.Contains(text, StringComparison.OrdinalIgnoreCase) == true) return true;
                if (info?.Note?.Contains(text, StringComparison.OrdinalIgnoreCase) == true) return true;
                return digits.Length > 0 && digits.Length == spaceless.Length && device.ConnectionId.ToString(System.Globalization.CultureInfo.InvariantCulture).Contains(digits, StringComparison.Ordinal);
            })];
        }
    }

    /// <summary>Rank for "sorted by status": in session, online, this computer, checking, unknown, offline.</summary>
    internal int StatusRank(Device device)
    {
        if (InSessionWithThis(device)) return 0;
        return PresenceOf(device) switch
        {
            Presence.Online => 1,
            Presence.This => 2,
            Presence.Checking => 3,
            Presence.Unknown => 4,
            _ => 5,
        };
    }

    /// <summary>The table: grouped by group (no group last), each sorted by status, then name.</summary>
    public List<RowGroup> RowGroups
    {
        get
        {
            var comparer = StringComparer.Create(Translator.Shared.Culture, CompareOptions.IgnoreCase | CompareOptions.NumericOrdering);
            var byGroup = Filtered.GroupBy(device => device.GroupName ?? "").ToList();
            var names = byGroup.Select(group => group.Key).OrderBy(name => name.Length == 0).ThenBy(name => name, comparer).ToList();
            return [.. names.Select(name =>
            {
                var sorted = byGroup.First(group => group.Key == name).OrderBy(StatusRank).ThenBy(device => device.Name, comparer).ToList();
                var title = name.Length == 0 ? (names.Count == 1 ? L("All devices") : L("No group")) : name;
                return new RowGroup(name.Length == 0 ? "-" : name, title, sorted, sorted.Count(IsOnline));
            })];
        }
    }

    // ---- Admin data: the "Unattended" and "Update" badges, version, note ----

    internal void ResetAdmin()
    {
        admin = [];
        AdminAvailable = false;
    }

    public AdminDevice? AdminOf(Device device) => admin.GetValueOrDefault(device.ConnectionId);

    internal async Task LoadAdmin()
    {
        DeviceAdmin ??= new EngineDeviceAdmin(app.Engine);
        if (!app.SignedIn) return;
        try
        {
            // "company" of the API is the ISM client id, not the tenant id; every row is matched by its 9-digit ID anyway.
            var rows = await DeviceAdmin.Devices(null, null);
            admin = rows.GroupBy(row => row.ConnectionId).ToDictionary(group => group.Key, group => group.First());
            AdminAvailable = true;
        }
        catch (EngineFailure)
        {
            // The engine has no device administration yet (or the person may not see it): no badges.
            admin = [];
            AdminAvailable = false;
        }
        Raise(nameof(Devices));
    }

    /// <summary>The newest app version seen: this app, or any listed device.</summary>
    public Version? NewestVersion
    {
        get
        {
            var versions = admin.Values.Select(row => ParseVersion(row.AppVersion)).Append(ParseVersion(app.Info.Version)).OfType<Version>().ToList();
            return versions.Count == 0 ? null : versions.Max();
        }
    }

    private static Version? ParseVersion(string? text) => Version.TryParse(text, out var version) ? version : null;

    /// <summary>The device runs an older version than the newest one seen.</summary>
    public bool UpdateAvailable(Device device) =>
        AdminOf(device) is { } info && ParseVersion(info.AppVersion) is { } have && NewestVersion is { } newest && have < newest;

    /// <summary>"2.0.4", or "—" until the device reports its version.</summary>
    public string VersionOf(Device device) => AdminOf(device)?.AppVersion is { Length: > 0 } version ? version : "—";

    public int UnattendedCount => AllDevices.Count(device => AdminOf(device)?.Unattended == true);

    // Device administration commands: only for people who may administer (can_admin); errors key on the hint.

    public bool CanAdmin(Device device) => AdminOf(device)?.CanAdmin == true;

    public Task<bool> SetNote(Device device, string? note) => AdminChange(device, info => DeviceAdmin.SetNote(info, note));

    public Task<bool> SetUnattended(Device device, string setting) => AdminChange(device, info => DeviceAdmin.SetUnattended(info, setting));

    /// <summary>
    /// The people of the company who may connect to this device without being asked: the owners, administrators and
    /// technicians, each with their grant (engine: remote_admin_unattended_grants + saas_members). Null with <see cref="Error"/> set when it cannot be read.
    /// </summary>
    public async Task<List<GrantRow>?> UnattendedWho(Device device)
    {
        if (AdminOf(device) is not { } info || Tenant is not { } tenant) return null;
        Error = "";
        try
        {
            var grants = await DeviceAdmin.UnattendedGrants(info);
            var members = await app.Engine.RunAsync<List<Member>?>("saas_members", ("tenant", tenant.TenantId)) ?? [];
            return members.Where(member => member.Role is "owner" or "admin" or "technician").Select(member =>
            {
                var own = grants.FirstOrDefault(grant => grant.UserId == member.UserId && grant.Source != "company_setting");
                var automatic = grants.Any(grant => grant.UserId == member.UserId && grant.Source == "company_setting");
                return new GrantRow(member.UserId, member.Name.Length > 0 ? member.Name : member.Email, member.Email, member.Role, own is not null, automatic);
            }).OrderBy(row => row.Name, StringComparer.CurrentCultureIgnoreCase).ToList();
        }
        catch (EngineFailure failure)
        {
            Error = failure.Message.Contains("not_saas", StringComparison.Ordinal)
                ? L("The people who may connect to this computer are managed in the administration console of its company.")
                : AdminError(failure);
            return null;
        }
    }

    public async Task<bool> SetUnattendedGrant(Device device, string userId, bool granted)
    {
        if (AdminOf(device) is not { } info) return false;
        Error = "";
        try
        {
            await DeviceAdmin.SetUnattendedGrant(info, userId, granted);
            return true;
        }
        catch (EngineFailure failure)
        {
            Error = failure.Message.Contains("invalid_user", StringComparison.Ordinal) ? L("This person cannot be given access to this computer.") : AdminError(failure);
            return false;
        }
    }

    /// <summary>
    /// The sentence for a refused unattended request: the engine's "not available to you" does not say what to do.
    /// Null when the message is not that refusal.
    /// </summary>
    public static string? UnattendedRefusalAdvice(string engineMessage) =>
        engineMessage.StartsWith("The remote device is not available to you", StringComparison.Ordinal)
            ? L("This computer did not accept an unattended connection from you. Ask an admin to allow you unattended access to this computer (its menu in Devices, Unattended access, Who may connect…), or connect and ask the person there to accept.")
            : null;

    private async Task<bool> AdminChange(Device device, Func<AdminDevice, Task> work)
    {
        if (AdminOf(device) is not { } info) return false;
        Error = "";
        try
        {
            await work(info);
            await Load(0);
            return true;
        }
        catch (EngineFailure failure)
        {
            Error = AdminError(failure);
            return false;
        }
    }

    /// <summary>Removes a device with the typed ID as confirmation (the admin API checks it); falls back to the company call without admin data.</summary>
    public async Task<bool> RemoveConfirmed(Device device, string typedId)
    {
        if (Format.ParseRemoteId(typedId) != device.ConnectionId)
        {
            Error = L("The ID does not match the device.");
            return false;
        }
        if (AdminOf(device) is not { } info) return await Remove(device);
        return await AdminChange(device, _ => DeviceAdmin.Remove(info, device.ConnectionId));
    }

    /// <summary>The stable error hint of the admin API as a sentence.</summary>
    public static string AdminError(EngineFailure failure)
    {
        var text = failure.Message;
        if (text.Contains("not_admin", StringComparison.Ordinal)) return L("Only an administrator of the company can change devices.");
        if (text.Contains("mfa_required", StringComparison.Ordinal)) return L("Sign in again with the second confirmation step to change devices.");
        if (text.Contains("changed", StringComparison.Ordinal)) return L("The device was changed in the meantime. The list is read again.");
        if (text.Contains("confirm_mismatch", StringComparison.Ordinal)) return L("The ID does not match the device.");
        return failure.Translated;
    }
}
