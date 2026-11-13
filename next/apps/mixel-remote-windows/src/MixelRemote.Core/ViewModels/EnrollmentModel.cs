using System.Text.Json;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>How this account sets up this computer.</summary>
public enum EnrollmentPath { Company, CompanyMember, Team, Personal, None }

/// <summary>
/// Setting up this computer once, so it can connect and be reached: add it to the company, enrol it against
/// an existing record (Mixel team), or register it as a personal device.
/// </summary>
public sealed class EnrollmentModel : Observable
{
    private const string Key = "mixel.remote.this-computer.v1";
    private readonly AppModel app;
    private bool needsSetup;
    private Registration? current;
    private string name = "";
    private string error = "";
    private bool working;
    private List<EnrollmentCompany> companies = [];
    private List<InventoryDevice> records = [];
    private string recordSearch = "";

    internal EnrollmentModel(AppModel app)
    {
        this.app = app;
        if (app.Preferences.Get(Key) is { } saved)
        {
            try { current = JsonSerializer.Deserialize<Registration>(saved, Json.Options); }
            catch (JsonException) { current = null; }
        }
    }

    /// <summary>The engine found no device identity, or the service does not know this device yet.</summary>
    public bool NeedsSetup { get => needsSetup; private set => Set(ref needsSetup, value); }
    public Registration? Current { get => current; private set => Set(ref current, value); }
    public string Name { get => name; set => Set(ref name, value); }
    public string Error { get => error; set => Set(ref error, value); }
    public bool Working { get => working; private set => Set(ref working, value); }
    public List<EnrollmentCompany> Companies { get => companies; private set => SetList(ref companies, value); }
    public List<InventoryDevice> Records { get => records; private set => SetList(ref records, value); }
    public string RecordSearch { get => recordSearch; set => Set(ref recordSearch, value); }
    /// <summary>The last action (tests await it).</summary>
    public Task LastRun { get; private set; } = Task.CompletedTask;

    public EnrollmentPath Path
    {
        get
        {
            if (app.Account.Account is not { } account) return EnrollmentPath.None;
            if (account.Staff) return EnrollmentPath.Team;
            if (app.Company.Tenant is { } tenant) return tenant.Role is "owner" or "admin" or "technician" ? EnrollmentPath.Company : EnrollmentPath.CompanyMember;
            if (account.Personal || account.HasPersonal) return EnrollmentPath.Personal;
            return EnrollmentPath.None;
        }
    }

    internal void Reset()
    {
        NeedsSetup = false;
        Error = "";
        Companies = [];
        Records = [];
    }

    /// <summary>Reads the state after sign-in: suggested name, the team's enrolment record.</summary>
    internal async Task Check()
    {
        if (Name.Length == 0)
        {
            try { Name = await app.Engine.RunAsync<string>("this_computer_name", ("device_name", Environment.MachineName)); }
            catch (EngineFailure) { }
        }
        if (app.Account.Account?.Staff == true)
        {
            try
            {
                if (await app.Engine.RunAsync<CurrentEnrollment?>("enrollment_current") is { } enrolled)
                {
                    Remember(enrolled.Device, enrolled.ConnectionId, Current?.Generation ?? 0, null);
                }
            }
            catch (EngineFailure) { }
        }
    }

    internal void RegisterFailed(EngineFailure failure)
    {
        var text = failure.Message;
        if (text.Contains("No saved device identity", StringComparison.Ordinal) || text.Contains("not enrolled", StringComparison.Ordinal)
            || text.Contains("No saved controller identity", StringComparison.Ordinal))
        {
            NeedsSetup = true;
        }
        else
        {
            app.Error = failure.Translated;
        }
    }

    internal void Remember(string device, uint connection, ulong generation, string? tenant)
    {
        Current = new Registration { Device = device, ConnectionId = connection, Generation = generation, Tenant = tenant };
        NeedsSetup = false;
        app.Preferences.Set(Key, JsonSerializer.Serialize(Current, Json.Options));
    }

    internal void Forget()
    {
        Current = null;
        app.Preferences.Set(Key, null);
    }

    // ---- Paths ----

    public Task AddToCompany(string? group = null)
    {
        var chosen = Name.Trim();
        if (chosen.Length == 0 || chosen.Length > 60)
        {
            Error = L("Enter a computer name of up to 60 characters");
            return Task.CompletedTask;
        }
        return Run(async () =>
        {
            await app.Devices.AddThisComputer(chosen, group);
            if (app.Devices.Error.Length > 0) throw new EngineFailure(app.Devices.Error);
        });
    }

    public Task RegisterPersonal()
    {
        var chosen = Name.Trim();
        if (chosen.Length == 0)
        {
            Error = L("Enter a device name");
            return Task.CompletedTask;
        }
        return Run(async () =>
        {
            if (!app.Remote.Disconnected) await app.Engine.PerformAsync("remote_disconnect");
            var id = await app.Engine.RunAsync<uint>("register_personal_device", ("name", chosen));
            Remember("", id, 0, null);
            await app.GoOnline();
        });
    }

    public Task LoadTeamRecords() => Run(async () =>
    {
        Companies = await app.Engine.RunAsync<List<EnrollmentCompany>>("enrollment_companies");
        var page = await app.Engine.RunAsync<InventoryPage>("enrollment_devices", ("after", null), ("search", RecordSearch));
        Records = page.Devices;
    });

    /// <summary>Mixel team: enrol this computer against an existing record.</summary>
    public Task Enroll(InventoryDevice record) => Run(async () =>
    {
        if (!app.Remote.Disconnected) await app.Engine.PerformAsync("remote_disconnect");
        var id = await app.Engine.RunAsync<uint>("enroll_this_device", ("device", record.Id), ("confirmed", true));
        Remember(record.Id, id, 0, null);
        await app.GoOnline();
    });

    /// <summary>Mixel team: add a new record for this computer in a company, then enrol it.</summary>
    public Task AddAndEnroll(string company)
    {
        var chosen = Name.Trim();
        if (chosen.Length == 0)
        {
            Error = L("Enter a device name");
            return Task.CompletedTask;
        }
        return Run(async () =>
        {
            if (!app.Remote.Disconnected) await app.Engine.PerformAsync("remote_disconnect");
            var device = await app.Engine.RunAsync<string>("add_this_device", ("company", company), ("name", chosen));
            var id = await app.Engine.RunAsync<uint>("enroll_this_device", ("device", device), ("confirmed", true));
            Remember(device, id, 0, null);
            await app.GoOnline();
        });
    }

    /// <summary>Removes this computer from Mixel Remote (signed with its own key; queued while offline).</summary>
    public async Task<SelfRemovalStatus?> RemoveThisComputer()
    {
        Error = "";
        Working = true;
        try
        {
            app.WentOffline = true;
            if (!app.Remote.Disconnected) await app.Engine.PerformAsync("remote_disconnect");
            object? saved = Current is { Device.Length: > 0, Generation: > 0 } known
                ? new Dictionary<string, object> { ["device"] = known.Device, ["connection_id"] = known.ConnectionId, ["generation"] = known.Generation }
                : null;
            var status = SelfRemoval.Parse(await app.Engine.RunAsync<string>("device_self_remove", ("registration", saved), ("confirmed", true)));
            if (status != SelfRemovalStatus.Stale)
            {
                Forget();
                NeedsSetup = true;
            }
            app.WentOffline = false;
            await app.Devices.Load();
            return status;
        }
        catch (EngineFailure failure)
        {
            app.WentOffline = false;
            Error = failure.Translated;
            return null;
        }
        finally
        {
            Working = false;
        }
    }

    private Task Run(Func<Task> work)
    {
        if (Working) return Task.CompletedTask;
        Working = true;
        Error = "";
        LastRun = Go();
        return LastRun;

        async Task Go()
        {
            try { await work(); }
            catch (EngineFailure failure) { Error = failure.Translated; }
            finally { Working = false; }
        }
    }
}
