using System.Globalization;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>The views chosen in the sidebar.</summary>
public enum ListViewKind { All, Online, Favorites, Recent, Group }

/// <summary>A view of the device list: one of the four built-in ones, or a group.</summary>
public readonly record struct ListView(ListViewKind Kind, string Group = "")
{
    public static readonly ListView All = new(ListViewKind.All);
    public static readonly ListView Online = new(ListViewKind.Online);
    public static readonly ListView Favorites = new(ListViewKind.Favorites);
    public static readonly ListView Recent = new(ListViewKind.Recent);
    public static ListView InGroup(string name) => new(ListViewKind.Group, name);
}

/// <summary>A group of rows in the table.</summary>
public sealed record RowGroup(string Id, string Name, List<Device> Devices, int Online);

// The Devices console ("Pro Console"): views, quick filters, the grouped table, and the admin badges.
public sealed partial class DevicesModel
{
    private ListView view = ListView.All;
    private bool onlineOnly;
    private bool unattendedOnly;
    private bool updateOnly;
    private HashSet<string> systems = [];
    private string filterText = "";
    private Dictionary<uint, AdminDevice> admin = [];
    private bool adminAvailable;

    public ListView View { get => view; set => Set(ref view, value); }
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

    public List<Device> DevicesIn(ListView wanted)
    {
        var all = AllDevices;
        return wanted.Kind switch
        {
            ListViewKind.Online => [.. all.Where(device => IsOnline(device) || InSessionWithThis(device))],
            ListViewKind.Favorites => [.. all.Where(device => Favorites.Contains(device.DeviceId))],
            ListViewKind.Recent => [.. Recent.Select(row => all.FirstOrDefault(device => device.DeviceId == row.DeviceId)).OfType<Device>()],
            ListViewKind.Group => [.. all.Where(device => (device.GroupName ?? "") == wanted.Group)],
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
            var rows = await DeviceAdmin.Devices(Tenant?.TenantId, null);
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
