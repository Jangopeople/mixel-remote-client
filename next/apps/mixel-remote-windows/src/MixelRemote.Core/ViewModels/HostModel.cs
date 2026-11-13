using System.Text.Json;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>
/// This computer as the one being helped: the consent dialog (screen, control, clipboard, files, chat,
/// recording), a second technician asking to join, unattended access, the one-time password and pairing.
/// </summary>
public sealed class HostModel : Observable
{
    private const string UnattendedKey = "mixel.remote.unattended.v1";
    private readonly AppModel app;
    private List<CaptureTarget> sources = [];
    private string source = "";
    private bool confirmDisplay, allowControl, allowClipboard, allowFiles, allowChat, allowRecording;
    private string error = "";
    private List<CaptureTarget> unattendedSources = [];
    private string unattendedSource = "";
    private bool unattendedConfirm, unattendedControl;
    private bool restored;

    internal HostModel(AppModel app) => this.app = app;

    public List<CaptureTarget> Sources { get => sources; private set => SetList(ref sources, value); }
    public string Source { get => source; set => Set(ref source, value); }
    public bool ConfirmDisplay { get => confirmDisplay; set => Set(ref confirmDisplay, value); }
    public bool AllowControl { get => allowControl; set => Set(ref allowControl, value); }
    public bool AllowClipboard { get => allowClipboard; set => Set(ref allowClipboard, value); }
    public bool AllowFiles { get => allowFiles; set => Set(ref allowFiles, value); }
    public bool AllowChat { get => allowChat; set => Set(ref allowChat, value); }
    public bool AllowRecording { get => allowRecording; set => Set(ref allowRecording, value); }
    public string Error { get => error; set => Set(ref error, value); }
    public List<CaptureTarget> UnattendedSources { get => unattendedSources; private set => SetList(ref unattendedSources, value); }
    public string UnattendedSource { get => unattendedSource; set => Set(ref unattendedSource, value); }
    public bool UnattendedConfirm { get => unattendedConfirm; set => Set(ref unattendedConfirm, value); }
    public bool UnattendedControl { get => unattendedControl; set => Set(ref unattendedControl, value); }

    public RemoteStatus Remote => app.Remote;
    /// <summary>A technician asks to connect and this person decides.</summary>
    public bool Asking => Remote.Phase == "incoming" && !Remote.UnattendedSession;
    public CaptureTarget? Selected => Sources.FirstOrDefault(target => target.Id == Source);
    public bool CanAccept => Asking && !app.Busy && Selected is { } selected && (!selected.FullDisplay || ConfirmDisplay);
    public bool ControlAvailable => Asking && (Selected?.FullDisplay ?? false) && Remote.InputAccess;

    internal void StatusChanged(RemoteStatus previous, RemoteStatus next)
    {
        if (next.Phase == "incoming" && previous.Phase != "incoming" && !next.ControllerOnly)
        {
            AllowChat = false;
            AllowRecording = false;
            AllowFiles = false;
            AllowClipboard = false;
            _ = LoadSources();
        }
        if (next.Phase == "ready" && previous.Phase != "ready") _ = RestoreUnattended();
    }

    public async Task LoadSources()
    {
        try
        {
            var targets = await app.Engine.RunAsync<List<CaptureTarget>>("capture_targets");
            Sources = [.. targets.Where(target => target.FullDisplay)];
            if (!Sources.Any(target => target.Id == Source)) Source = Sources.FirstOrDefault()?.Id ?? "";
            // One screen: it is the one to share. Keyboard and mouse are pre-selected when they can be
            // allowed; the person still decides with Allow.
            ConfirmDisplay = Sources.Count == 1;
            AllowControl = ConfirmDisplay && Remote.InputAccess;
        }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
        }
    }

    public Task Decide(bool accept)
    {
        if (Remote.Session is not { } session) return Task.CompletedTask;
        var decision = new Decision
        {
            Session = session,
            Accept = accept,
            Source = Source,
            Confirm = ConfirmDisplay,
            Control = accept && AllowControl && ControlAvailable,
            Clipboard = accept && AllowClipboard,
            Files = accept && AllowFiles,
            Chat = accept && AllowChat,
            Recording = accept && AllowRecording,
        };
        return app.Act(() => app.Engine.PerformAsync("remote_decide", ("decision", decision)));
    }

    // ---- During a hosted session ----

    public Task DecideJoin(bool accept, bool control) => Remote.JoinRequest is { } request
        ? app.Act(() => app.Engine.PerformAsync("remote_decide_join", ("session", request.Session), ("accept", accept), ("control", control)))
        : Task.CompletedTask;

    public Task RemoveColleague(Colleague colleague) =>
        app.Act(() => app.Engine.PerformAsync("remote_remove_colleague", ("session", colleague.Session)));

    public string? MeetingOffer => Remote is { MeetingOffer: [var offerSession, var room], Phase: "active", Sharing: true } && offerSession == Remote.Session && room != app.Session.DismissedMeetingRoom
        ? room
        : null;

    public void DismissMeetingOffer()
    {
        if (MeetingOffer is { } room) app.Session.DismissedMeetingRoom = room;
        Raise(nameof(MeetingOffer));
    }

    public Task OpenMeetingOffer()
    {
        if (MeetingOffer is not { } room) return Task.CompletedTask;
        app.Session.DismissedMeetingRoom = room;
        return app.Act(async () =>
        {
            var link = await app.Engine.RunAsync<string>("meeting_link", ("link", $"https://rs.mixel.ch/meet/#{room}"));
            app.OpenUrl(link);
        });
    }

    /// <summary>The helped person ends the session.</summary>
    public Task StopSharing() => app.Session.End();

    // ---- Unattended access, one-time password, pairing ----

    public Task LoadUnattendedSources() => app.Act(async () =>
    {
        var targets = await app.Engine.RunAsync<List<CaptureTarget>>("capture_targets");
        UnattendedSources = [.. targets.Where(target => target.FullDisplay)];
        UnattendedSource = UnattendedSources.FirstOrDefault()?.Id ?? "";
        UnattendedConfirm = false;
        if (UnattendedSources.Count == 0) throw new EngineFailure("An available display is required before enabling unattended sharing.");
    });

    public Task EnableUnattended()
    {
        if (UnattendedSource.Length == 0 || !UnattendedConfirm)
        {
            Error = L("Choose a display and confirm it before enabling unattended sharing.");
            return Task.CompletedTask;
        }
        var (chosen, control) = (UnattendedSource, UnattendedControl && Remote.InputAccess);
        UnattendedConfirm = false;
        app.Preferences.Set(UnattendedKey, JsonSerializer.Serialize(new Dictionary<string, object> { ["source"] = chosen, ["control"] = control }));
        return app.Act(() => app.Engine.PerformAsync("remote_unattended", ("source", chosen), ("control", control)));
    }

    public Task DisableUnattended()
    {
        app.Preferences.Set(UnattendedKey, null);
        return app.Act(() => app.Engine.PerformAsync("remote_unattended", ("source", null), ("control", false)));
    }

    /// <summary>After a start with a remembered sign-in, unattended sharing comes back as the owner left it.</summary>
    internal async Task RestoreUnattended()
    {
        if (restored || Remote.UnattendedEnabled || Remote.UnattendedPending is not null) return;
        if (app.Preferences.Get(UnattendedKey) is not { } saved) return;
        string? chosen;
        bool control;
        try
        {
            using var document = JsonDocument.Parse(saved);
            chosen = document.RootElement.GetProperty("source").GetString();
            control = document.RootElement.GetProperty("control").GetBoolean();
        }
        catch (Exception failure) when (failure is JsonException or KeyNotFoundException or InvalidOperationException)
        {
            return;
        }
        try
        {
            var startup = await app.Engine.RunAsync<StartupSettings>("startup_settings");
            if (!startup.Restored && !startup.LaunchedAtStartup) return;
            var targets = await app.Engine.RunAsync<List<CaptureTarget>>("capture_targets");
            if (!targets.Any(target => target.Id == chosen && target.FullDisplay)) return;
            restored = true;
            await app.Engine.PerformAsync("remote_unattended", ("source", chosen), ("control", control));
        }
        catch (EngineFailure) { }
    }

    public Task OneTimePassword(bool required) => app.Act(() => app.Engine.PerformAsync("remote_support_code", ("required", required)));

    public Task CheckPairing() => app.Act(() => app.Engine.PerformAsync("remote_pairing", ("confirmation", null)));

    public Task ConfirmPairing() => Remote.Pairing is { } pairing
        ? app.Act(() => app.Engine.PerformAsync("remote_pairing", ("confirmation", pairing.RequestId)))
        : Task.CompletedTask;

    public Task CopyId() => app.Act(() => app.Engine.PerformAsync("copy_connection_id"));
}
