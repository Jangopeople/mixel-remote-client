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

/// <summary>One session tab: the device's Remote ID, the session's stable engine id (the tab never changes it), name, system and state (starting, ready, ending).</summary>
public sealed record SessionTab(uint Peer, string Name, string Os, string State, string Id = "");

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
/// Sessions are keyed by the other computer's Remote ID (one session per device) and bound to the engine's stable
/// session id (<see cref="SessionTab.Id"/>): a transport renewal changes the connection service's session id, not
/// these. The engine lists up to four sessions (remote_status.sessions); there is one tab (or window) for each, and
/// <see cref="SessionModel"/> per session. <see cref="Focused"/> is the one session that gets keyboard, mouse,
/// clipboard and toolbar actions; <see cref="IsVisible"/> says which pictures are fetched.
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

    public WorkspaceTab Selected
    {
        get => selected;
        set
        {
            if (Set(ref selected, value)) Raise(nameof(Focused));
        }
    }

    private uint? focusedWindow;

    /// <summary>
    /// The one session that receives input, clipboard and toolbar actions: the session of the active pop-out window, else
    /// the session of the tab in front while the main window is active. null: none (Devices is in front, or the app is not active).
    /// </summary>
    public uint? Focused
    {
        get
        {
            if (focusedWindow is { } window && windows.Contains(window)) return window;
            return selected.Peer is { } peer && !windows.Contains(peer) && tabs.Any(tab => tab.Peer == peer) ? peer : null;
        }
    }

    /// <summary>A session's pop-out window became the active window.</summary>
    public void WindowActivated(uint peer)
    {
        if (focusedWindow == peer) return;
        focusedWindow = peer;
        Raise(nameof(Focused));
    }

    /// <summary>A pop-out window lost the focus (another window or another app is active now).</summary>
    public void WindowDeactivated(uint peer)
    {
        if (focusedWindow != peer) return;
        focusedWindow = null;
        Raise(nameof(Focused));
    }

    /// <summary>The main window became the active window: the tab in front is the focused session.</summary>
    public void MainActivated()
    {
        if (focusedWindow is null) return;
        focusedWindow = null;
        Raise(nameof(Focused));
    }

    /// <summary>The session's picture is on screen (its tab is in front, or it has its own window): hidden sessions stop fetching pictures.</summary>
    public bool IsVisible(uint peer) => windows.Contains(peer) || selected.Peer == peer;
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
    /// One tab for each technician session the engine lists (remote_status.sessions, or the one session of the
    /// single-session fields of an older engine). A session shows as a tab once it runs (starting until its first
    /// picture arrived, ready, ending); while it is only asked for, the connect panel shows it.
    /// </summary>
    internal List<SessionTab> SessionTabs(RemoteStatus status)
    {
        List<SessionTab> result = [];
        foreach (var item in status.TechnicianSessions)
        {
            if (item.Id.Length == 0) continue;
            var state = item.Phase switch
            {
                "active" => app.SessionFor(item.Peer)?.Frame is not null ? "ready" : "starting",
                "closing" => "ending",
                _ => "",
            };
            if (state.Length == 0) continue;
            result.Add(new SessionTab(item.Peer, app.Devices.NameOf(item.Peer), app.Devices.OsOf(item.Peer), state, item.Id));
        }
        return result;
    }

    /// <summary>Called when the remote status or the first picture changes.</summary>
    internal void Refresh(RemoteStatus? status = null)
    {
        status ??= app.Remote;
        var next = SessionTabs(status);
        var before = tabs.ToDictionary(tab => tab.Peer);
        if (!next.SequenceEqual(tabs))
        {
            tabs = next;
            Raise(nameof(Tabs));
            Raise(nameof(Focused));
        }
        var open = next.Select(tab => tab.Peer).ToHashSet();
        // Ended sessions: their tab, window and notice go away; the Devices list comes back.
        foreach (var peer in before.Keys.Where(peer => !open.Contains(peer)).ToList())
        {
            if (windows.Remove(peer))
            {
                if (focusedWindow == peer) focusedWindow = null;
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
        var mine = status.TechnicianSessions.FirstOrDefault(item => item.Peer == current.Peer);
        var matches = mine is not null;
        var step = current.Step;
        if (mine is not null)
        {
            switch (mine.Phase)
            {
                case "outgoing": step = Math.Max(step, 1); break;
                case "negotiating": step = Math.Max(step, 2); break;
                case "active": step = Math.Max(step, app.SessionFor(current.Peer)?.Frame is null ? 3 : 4); break;
            }
        }
        if (step == 4)
        {
            // Ready: the notice (or the session itself) takes over from the panel.
            Attempt = null;
            return;
        }
        var running = mine?.Running == true;
        var failure = current.Failure;
        if (failure.Length == 0 && !app.Busy && !running)
        {
            if (app.Error.Length > 0) failure = app.Error;
            else if (step >= 1)
            {
                // The request was sent and the session is gone again: declined, timed out or refused.
                // With other sessions open the connection's own message is theirs, so only a lone connection has a sentence to give.
                failure = status.Message.Length == 0 || status.Phase == "ready" || status.TechnicianSessions.Count > 0
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
        return app.SessionFor(current.Peer)?.End() ?? Task.CompletedTask;
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

    /// <summary>The session model of a tab: one per session the engine lists.</summary>
    public SessionModel? SessionFor(uint peer) => app.SessionFor(peer);

    /// <summary>Ends one session; the others go on (remote_close with its stable id).</summary>
    public Task End(uint peer) => SessionFor(peer)?.End() ?? Task.CompletedTask;

    /// <summary>The device of a Remote ID is in a session with this app, or being connected.</summary>
    public string? StateOf(uint peer)
    {
        if (Tab(peer) is { } tab) return tab.State;
        if (attempt is { } current && current.Peer == peer && current.Failure.Length == 0) return "connecting";
        return null;
    }
}
