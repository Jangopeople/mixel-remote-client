using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>What happens when a session is ready to be shown.</summary>
public enum WhenReady { Stay, Open }

/// <summary>Where a session opens.</summary>
public enum OpenIn { Tab, Window }

/// <summary>The tab in the title bar: the Devices console, or one session (by the other computer's Remote ID).</summary>
public readonly record struct WorkspaceTab(uint? Peer)
{
    public static readonly WorkspaceTab Console = new(null);
    public static WorkspaceTab Session(uint peer) => new(peer);
    public bool IsConsole => Peer is null;
}

/// <summary>One session tab.</summary>
public sealed record SessionTab(uint Peer, string Name, string Os, string State);

/// <summary>A connection being prepared from the Devices list or "Connect by ID".</summary>
public sealed record ConnectAttempt
{
    public uint Peer { get; init; }
    public string Name { get; init; } = "";
    public string Os { get; init; } = "";
    public DateTimeOffset Started { get; init; }
    /// <summary>The step reached: 0 reaching the device, 1 waiting for approval or unattended access, 2 encrypting, 3 starting video, 4 ready.</summary>
    public int Step { get; init; }
    public bool Unattended { get; init; }
    public string Failure { get; init; } = "";
    /// <summary>"Open in window instead" was chosen for this connection.</summary>
    public bool InWindow { get; init; }
}

/// <summary>
/// The technician's workspace (redesign "Pro Console"): the session tabs in the title bar, which tab is in
/// front, which sessions are in their own window, the connection being prepared, and the two remembered
/// choices ("when a session is ready" and "open sessions in"). Same rules as the Mac app's WorkspaceModel.
///
/// Sessions are keyed by the other computer's Remote ID, not by the engine's session id: the session id
/// changes when the connection is renewed, the computer does not. Everything is a list so several sessions
/// at once only need <see cref="SessionTabs"/> to read more than one session from the engine.
/// </summary>
public sealed class WorkspaceModel : Observable
{
    public const string WhenReadyKey = "mixel.remote.workspace.whenReady";
    public const string OpenInKey = "mixel.remote.workspace.openIn";

    private readonly AppModel app;
    private WhenReady whenReady;
    private OpenIn openIn;
    private WorkspaceTab selected = WorkspaceTab.Console;
    private List<SessionTab> tabs = [];
    private HashSet<uint> windows = [];
    private ConnectAttempt? attempt;
    private List<uint> readyNotices = [];
    private readonly HashSet<uint> attemptWindow = [];

    internal WorkspaceModel(AppModel app)
    {
        this.app = app;
        whenReady = app.Preferences.Get(WhenReadyKey) == "open" ? WhenReady.Open : WhenReady.Stay;
        openIn = app.Preferences.Get(OpenInKey) == "window" ? OpenIn.Window : OpenIn.Tab;
    }

    public WhenReady WhenReady
    {
        get => whenReady;
        set
        {
            if (Set(ref whenReady, value)) app.Preferences.Set(WhenReadyKey, value == WhenReady.Open ? "open" : "stay");
        }
    }

    public OpenIn OpenIn
    {
        get => openIn;
        set
        {
            if (Set(ref openIn, value)) app.Preferences.Set(OpenInKey, value == OpenIn.Window ? "window" : "tab");
        }
    }

    public WorkspaceTab Selected { get => selected; set => Set(ref selected, value); }
    public IReadOnlyList<SessionTab> Tabs => tabs;
    /// <summary>Sessions shown in their own window.</summary>
    public IReadOnlyCollection<uint> Windows => windows;
    public ConnectAttempt? Attempt { get => attempt; private set => Set(ref attempt, value); }
    /// <summary>Sessions that became ready while the person stayed elsewhere: "SRV-DC-02 is ready · Open".</summary>
    public IReadOnlyList<uint> ReadyNotices => readyNotices;

    /// <summary>Opens a session window (set by the app).</summary>
    public Action<uint> OpenWindow { get; set; } = _ => { };
    /// <summary>Brings an open session window to the front (set by the app).</summary>
    public Action<uint> FocusWindow { get; set; } = _ => { };
    public Action<uint> CloseWindow { get; set; } = _ => { };

    // ---- Reading the engine ----

    /// <summary>
    /// The sessions this app views as technician. Today the engine runs one session at a time; when it reports
    /// several (remote_status.sessions, see docs/mixel-remote-ui-engine-requests.md) this is the only place
    /// that reads them.
    /// </summary>
    internal List<SessionTab> SessionTabs(RemoteStatus status, bool frameShown)
    {
        if (status.Peer is not { } peer || status.Sharing || status.Session is null) return [];
        string state;
        switch (status.Phase)
        {
            case "active": state = frameShown ? "ready" : "starting"; break;
            case "closing": state = "ending"; break;
            default: return [];
        }
        return [new SessionTab(peer, app.Devices.NameOf(peer), app.Devices.OsOf(peer), state)];
    }

    /// <summary>Called when the remote status or the first picture changes.</summary>
    internal void Refresh(RemoteStatus? status = null)
    {
        status ??= app.Remote;
        var next = SessionTabs(status, app.Session.Frame is not null);
        var before = tabs.ToDictionary(tab => tab.Peer);
        if (!next.SequenceEqual(tabs))
        {
            tabs = next;
            Raise(nameof(Tabs));
        }
        var open = next.Select(tab => tab.Peer).ToHashSet();
        // Ended sessions: their tab, window and notice go away; the Devices list comes back.
        foreach (var peer in before.Keys.Where(peer => !open.Contains(peer)).ToList())
        {
            if (windows.Remove(peer))
            {
                Raise(nameof(Windows));
                CloseWindow(peer);
            }
            if (readyNotices.Remove(peer)) Raise(nameof(ReadyNotices));
            if (selected.Peer == peer) Selected = WorkspaceTab.Console;
        }
        UpdateAttempt(status);
        foreach (var tab in next.Where(tab => tab.State == "ready" && !(before.TryGetValue(tab.Peer, out var old) && old.State == "ready")))
        {
            BecameReady(tab.Peer);
        }
    }

    private void UpdateAttempt(RemoteStatus status)
    {
        if (attempt is not { } current) return;
        var matches = status.Peer == current.Peer;
        var step = current.Step;
        if (matches)
        {
            switch (status.Phase)
            {
                case "outgoing": step = Math.Max(step, 1); break;
                case "negotiating": step = Math.Max(step, 2); break;
                case "active": step = Math.Max(step, app.Session.Frame is null ? 3 : 4); break;
            }
        }
        if (step == 4)
        {
            // Ready: the notice (or the session itself) takes over from the panel.
            Attempt = null;
            return;
        }
        var running = matches && status.InSession;
        var failure = current.Failure;
        if (failure.Length == 0 && !app.Busy && !running)
        {
            if (app.Error.Length > 0) failure = app.Error;
            else if (step >= 1)
            {
                // The request was sent and the session is gone again: declined, timed out or refused.
                failure = status.Message.Length == 0 || status.Phase == "ready"
                    ? L("The connection was not established. The other side declined, did not answer, or unattended access is not allowed.")
                    : Translator.Shared.Translate(status.Message);
            }
        }
        Attempt = current with { Step = step, Failure = failure };
    }

    private void BecameReady(uint peer)
    {
        if (attemptWindow.Remove(peer)) Show(peer, inWindow: true);
        else if (whenReady == WhenReady.Open) Show(peer);
        else if (selected.Peer != peer && !windows.Contains(peer) && !readyNotices.Contains(peer))
        {
            readyNotices = [.. readyNotices, peer];
            Raise(nameof(ReadyNotices));
        }
    }

    // ---- Actions ----

    /// <summary>Connect from the Devices list or by ID; the person stays in Devices.</summary>
    public Task Connect(uint peer, string? name = null, string os = "", bool unattended = true, string? password = null, bool checkPhone = false)
    {
        attemptWindow.Remove(peer);
        var next = new ConnectAttempt
        {
            Peer = peer, Name = name ?? app.Devices.NameOf(peer), Os = os.Length == 0 ? app.Devices.OsOf(peer) : os,
            Started = DateTimeOffset.UtcNow, Step = 0, Unattended = unattended,
        };
        if (app.Busy)
        {
            Attempt = next with { Failure = L("Please wait a moment, the previous step is still running.") };
            return Task.CompletedTask;
        }
        Attempt = next;
        return app.Connect(peer, password, unattended, checkPhone);
    }

    /// <summary>"Open in window instead" while connecting.</summary>
    public void OpenAttemptInWindow()
    {
        if (attempt is not { } current) return;
        Attempt = current with { InWindow = true };
        attemptWindow.Add(current.Peer);
    }

    /// <summary>Cancel while connecting (ends the request for both sides), or close a failed attempt.</summary>
    public Task CancelAttempt()
    {
        if (attempt is not { } current) return Task.CompletedTask;
        Attempt = null;
        attemptWindow.Remove(current.Peer);
        return app.Remote.Peer == current.Peer && app.Remote.InSession ? app.Session.End() : Task.CompletedTask;
    }

    public void DismissNotice(uint peer)
    {
        if (readyNotices.Remove(peer)) Raise(nameof(ReadyNotices));
    }

    /// <summary>Shows a session: in its window if it has one, else as the chosen default (tab or own window).</summary>
    public void Show(uint peer, bool? inWindow = null)
    {
        if (tabs.All(tab => tab.Peer != peer)) return;
        DismissNotice(peer);
        if (windows.Contains(peer))
        {
            FocusWindow(peer);
            return;
        }
        if (inWindow ?? openIn == OpenIn.Window) PopOut(peer);
        else Selected = WorkspaceTab.Session(peer);
    }

    /// <summary>Moves the session from its tab into its own window.</summary>
    public void PopOut(uint peer)
    {
        if (tabs.All(tab => tab.Peer != peer)) return;
        DismissNotice(peer);
        if (selected.Peer == peer) Selected = WorkspaceTab.Console;
        if (windows.Add(peer))
        {
            Raise(nameof(Windows));
            OpenWindow(peer);
        }
        else FocusWindow(peer);
    }

    /// <summary>Puts the session's window back into its tab.</summary>
    public void Dock(uint peer, bool select = true)
    {
        if (!windows.Remove(peer)) return;
        Raise(nameof(Windows));
        CloseWindow(peer);
        if (select && tabs.Any(tab => tab.Peer == peer)) Selected = WorkspaceTab.Session(peer);
    }

    /// <summary>The session window was closed by the person (title bar button): it goes back into its tab.</summary>
    public void WindowClosed(uint peer)
    {
        if (!windows.Remove(peer)) return;
        Raise(nameof(Windows));
        if (tabs.Any(tab => tab.Peer == peer)) Selected = WorkspaceTab.Session(peer);
    }

    public SessionTab? Tab(uint peer) => tabs.FirstOrDefault(tab => tab.Peer == peer);
    public bool IsInWindow(uint peer) => windows.Contains(peer);

    /// <summary>The session model of a tab. One today; one per session once the engine runs several.</summary>
    public SessionModel? SessionFor(uint peer) => app.Remote.Peer == peer ? app.Session : null;

    /// <summary>Ends one session.</summary>
    public Task End(uint peer) => SessionFor(peer)?.End() ?? Task.CompletedTask;

    /// <summary>The device of a Remote ID is in a session with this app, or being connected.</summary>
    public string? StateOf(uint peer)
    {
        if (Tab(peer) is { } tab) return tab.State;
        if (attempt is { } current && current.Peer == peer && current.Failure.Length == 0) return "connecting";
        return null;
    }
}
