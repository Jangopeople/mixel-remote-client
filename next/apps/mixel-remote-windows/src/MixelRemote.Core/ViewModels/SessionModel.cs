using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>
/// One session on the technician's side: the remote picture, keyboard and mouse, and the toolbar
/// (displays, privacy screen, block input, pointer, lock, Ctrl+Alt+Del, restart, sound, clipboard, files,
/// chat, recording, invite a colleague, switch sides, end). There is one model per session the engine reports
/// (<see cref="AppModel.Sessions"/>, keyed by the device's Remote ID); it is bound to the session's stable id, so
/// a transport renewal does not touch it. Input, clipboard and toolbar actions are sent only while the session is
/// the focused one of the workspace (<see cref="WorkspaceModel.Focused"/>). The model without a session
/// (<see cref="AppModel.Session"/> when nothing is viewed) reads the connection's own status, which is what the
/// hosted side uses.
/// </summary>
public sealed class SessionModel : Observable
{
    private readonly AppModel app;
    private VideoFrame? frame;
    private int fps;
    private bool stopping;
    private string error = "";
    private string notice = "";
    private bool privacy, blockInput, lockWhenDone, pointerMode, restartArmed, recordingPending;
    private bool sound = true;
    private bool voice;
    private List<ChatMessage> chat = [];
    private string chatDraft = "";
    private FileView? files;
    private string clipboardResult = "";
    private string textToSend = "";
    private string meetingNotice = "";
    private CancellationTokenSource? video;
    private CancellationTokenSource? tools;
    private string? viewingSession;
    private string? toggleSession;
    private string? stoppedConsent;
    private readonly Queue<DateTimeOffset> shownTimes = new();
    private readonly List<InputEvent> inputQueue = [];
    private bool sending;
    private Reconnect? reconnect;
    private SessionStatus? item;
    private RemoteStatus? projected;
    private bool inputSent;

    private sealed class Reconnect
    {
        public uint Host;
        public uint? Signin;
        public bool Unattended;
        public DateTimeOffset Until;
        public DateTimeOffset Since;
        public bool WentOffline;
        public DateTimeOffset LastTry = DateTimeOffset.MinValue;
        public DateTimeOffset LastPresence = DateTimeOffset.MinValue;
        public string Session = "";
    }

    internal SessionModel(AppModel app, SessionStatus? item = null)
    {
        this.app = app;
        this.item = item;
    }

    /// <summary>The engine's item of this session; null for the model without a session and for a session that ended while a restart reconnects.</summary>
    public SessionStatus? Item => item;
    /// <summary>The other computer's Remote ID.</summary>
    public uint? Peer => item?.Peer;
    /// <summary>The Remote ID this model belongs to; kept while a restart of the remote computer is waited for.</summary>
    internal uint Key { get; private set; }
    /// <summary>A restart of the remote computer is being waited for.</summary>
    internal bool Reconnecting => reconnect is not null;
    /// <summary>The session is the one that gets keyboard, mouse, clipboard and toolbar actions (its tab is in front, or its window is active).</summary>
    public bool Focused => item is { } mine && app.Workspace.Focused == mine.Peer;
    /// <summary>Actions may go to this session: it is focused (the model without a session is the hosted side and always may).</summary>
    private bool MayAct => item is null || Focused;
    /// <summary>The picture facts of the engine (pictures per second, age of the last picture).</summary>
    public SessionQuality? Quality => item?.Quality;

    /// <summary>The newest picture (the screen draws it).</summary>
    public VideoFrame? Frame { get => frame; private set => Set(ref frame, value); }
    /// <summary>Properties that change with the pictures (many times a second): pages must not rebuild for them; the toolbar updates its own text.</summary>
    public static readonly string[] PerPictureProperties = [nameof(Fps)];
    public int Fps { get => fps; private set => Set(ref fps, value); }
    public bool Stopping { get => stopping; private set => Set(ref stopping, value); }
    public string Error { get => error; set => Set(ref error, value); }
    public string Notice { get => notice; set => Set(ref notice, value); }
    public bool Privacy { get => privacy; private set => Set(ref privacy, value); }
    public bool BlockInput { get => blockInput; private set => Set(ref blockInput, value); }
    public bool LockWhenDone { get => lockWhenDone; private set => Set(ref lockWhenDone, value); }
    public bool PointerMode { get => pointerMode; set => Set(ref pointerMode, value); }
    public bool Sound { get => sound; private set => Set(ref sound, value); }
    public bool Voice { get => voice; private set => Set(ref voice, value); }
    public bool RestartArmed { get => restartArmed; private set => Set(ref restartArmed, value); }
    public List<ChatMessage> Chat { get => chat; private set => SetList(ref chat, value); }
    public string ChatDraft { get => chatDraft; set => Set(ref chatDraft, value); }
    public FileView? Files { get => files; private set => SetList(ref files, value); }
    public string ClipboardResult { get => clipboardResult; set => Set(ref clipboardResult, value); }
    public string TextToSend { get => textToSend; set => Set(ref textToSend, value); }
    public bool RecordingPending { get => recordingPending; private set => Set(ref recordingPending, value); }
    public string MeetingNotice { get => meetingNotice; private set => Set(ref meetingNotice, value); }
    public string DismissedMeetingRoom { get; set; } = "";
    private DateTimeOffset? recordingSince;
    /// <summary>When the running recording started (for "REC 00:42" over the picture).</summary>
    public DateTimeOffset? RecordingSince { get => recordingSince; private set => Set(ref recordingSince, value); }
    /// <summary>A print job of the other computer arrived (the session window opens the print jobs).</summary>
    public event EventHandler? PrintJobArrived;
    /// <summary>Raised for every new picture (the screen redraws without going through bindings).</summary>
    public event EventHandler<VideoFrame>? FrameArrived;

    internal bool LastRequestUnattended { get; set; }
    /// <summary>Stops this model for good (its session is gone).</summary>
    internal void Dispose()
    {
        Clear();
        StopTools();
    }
    /// <summary>This session was requested as unattended (without asking the person at the other computer).</summary>
    public bool RequestedUnattended => LastRequestUnattended;
    /// <summary>Milliseconds to wait when no picture is ready yet.</summary>
    internal int IdleDelay { get; set; } = 4;

    /// <summary>This session in the shape of the single-session status (the connection-wide fields are the connection's).</summary>
    public RemoteStatus Remote => item is null ? app.Remote : projected ?? item.Project(app.Remote);
    /// <summary>The stable id every engine call of this session carries.</summary>
    public string? SessionId => item is null ? app.Remote.Session : item.Id.Length > 0 ? item.Id : null;
    /// <summary>The picture is shown and keyboard and mouse go to the other computer (whether they are sent is also up to focus).</summary>
    public bool Controllable => !Stopping && Remote.Viewing && Remote.Control && Frame is not null && !LocallyStopped;
    private bool LocallyStopped => Remote.Consent is not null && Remote.Consent == stoppedConsent;
    public bool CanSwitchSides => Remote.Phase == "active" && Remote.PeerFeatures.Contains("switch");

    /// <summary>The engine reported this session (or, for the model without a session, the connection status) again.</summary>
    internal void Update(SessionStatus mine, RemoteStatus top)
    {
        var previous = item is null ? RemoteStatus.Offline : projected ?? RemoteStatus.Offline;
        item = mine;
        Key = mine.Peer;
        var next = mine.Project(top);
        projected = next;
        StatusChanged(previous, next);
    }

    /// <summary>The engine no longer lists this session: the picture stops, the person may add a note, a restart may reconnect.</summary>
    internal void Ended()
    {
        if (item is not null && projected is { Viewing: true } && app.Company.Tenant is not null) app.NoteFor = item.Peer;
        Clear();
        StopTools();
        stoppedConsent = null;
        toggleSession = null;
        viewingSession = null;
        item = null;
        projected = null;
    }

    /// <summary>The hosted side: the connection's own status changed (the model without a session).</summary>
    internal void StatusChanged(RemoteStatus previous, RemoteStatus next)
    {
        if (next.Session != toggleSession)
        {
            toggleSession = next.Session;
            strokes.Clear();
            current = null;
            Drawing = false;
            Privacy = false;
            BlockInput = false;
            LockWhenDone = false;
            PointerMode = false;
            Sound = true;
            Chat = [];
            Files = null;
            ClipboardResult = "";
            inputSent = false;
        }
        if (next.Viewing && !Stopping && !LocallyStopped && next.Session is { } session)
        {
            if (viewingSession != session) StartVideo(session);
        }
        else if (viewingSession is not null)
        {
            Clear();
        }
        var recording = next.Recording?.Running == true;
        if (recording && RecordingSince is null) RecordingSince = DateTimeOffset.UtcNow;
        else if (!recording && RecordingSince is not null) RecordingSince = null;
        CursorShape = next.CursorShape ?? "arrow";
        if (next.Phase is "active" or "incoming" or "outgoing") StartTools();
        else StopTools();
        if (!next.InSession) stoppedConsent = null;
        if (next.Peer is { } peer && next.HostMacs.Count > 0) app.Devices.RememberMacs(peer, next.HostMacs);
        if (next.PrintJobs.Count > previous.PrintJobs.Count && next.Viewing) PrintJobArrived?.Invoke(this, EventArgs.Empty);
        if (item is { } mine) ContinueReconnect(app.Remote, mine, mine.Peer);
    }

    /// <summary>Stops the picture and forgets it.</summary>
    public void Clear()
    {
        video?.Cancel();
        video = null;
        viewingSession = null;
        var had = Frame is not null;
        Frame = null;
        Fps = 0;
        shownTimes.Clear();
        inputQueue.Clear();
        if (had) app.Workspace.Refresh();
    }

    private void StartVideo(string session)
    {
        Clear();
        viewingSession = session;
        video = new CancellationTokenSource();
        _ = ReadVideo(session, video.Token);
    }

    private async Task ReadVideo(string session, CancellationToken token)
    {
        ulong sequence = 0;
        while (!token.IsCancellationRequested)
        {
            try
            {
                // A session in a background tab keeps its last picture and stops fetching; its first picture is always fetched.
                if (Frame is not null && item is { } mine && !app.Workspace.IsVisible(mine.Peer))
                {
                    if (Fps != 0) Fps = 0;
                    await Task.Delay(100, token);
                    continue;
                }
                var data = await app.Engine.NextVideoAsync(session, compressed: false);
                if (token.IsCancellationRequested || viewingSession != session) return;
                if (data.Length == 0)
                {
                    await Task.Delay(IdleDelay, token);
                    continue;
                }
                if (VideoFrame.IsCompressed(data)) continue;
                var decoded = VideoFrame.Decode(data, sequence);
                sequence = decoded.Sequence;
                Show(decoded);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (Exception failure)
            {
                if (viewingSession == session)
                {
                    Error = failure is VideoFrame.InvalidFrame ? L("Invalid native video frame") : AppModel.Message(failure);
                    try { await app.Engine.PerformAsync("remote_close", ("session", session)); } catch (EngineFailure) { }
                    Clear();
                }
                return;
            }
        }
    }

    private void Show(VideoFrame decoded)
    {
        var first = Frame is null;
        frame = decoded;
        if (first)
        {
            Raise(nameof(Frame));
            app.Workspace.Refresh();
        }
        FrameArrived?.Invoke(this, decoded);
        var now = DateTimeOffset.UtcNow;
        shownTimes.Enqueue(now);
        while (shownTimes.Count > 0 && now - shownTimes.Peek() > TimeSpan.FromSeconds(1)) shownTimes.Dequeue();
        Fps = shownTimes.Count;
    }

    /// <summary>For screenshots and tests: show this picture without a running engine.</summary>
    public void ShowSample(byte[] data) => Show(VideoFrame.Decode(data));

    /// <summary>
    /// A session started with a support password ends after 2 hours: "ends in 5 minutes" and "ends in 1 minute"
    /// (the broker's session_ending, as remote_status.password_session). Empty when there is nothing to say.
    /// </summary>
    /// <summary>Unattended access fell back to asking the person (engine: <c>sessions[].unattended_refused</c>).</summary>
    public string ModeNotice() => Remote.UnattendedRefused && !Remote.UnattendedSession ? L("This computer is not set up for unattended access; asking the person there") : "";

    public string ReconnectingNotice() => Remote.Reconnecting ? L("Reconnecting… the session stays open") : "";

    /// <summary>The remote computer went away and this session asks for it again (engine: <c>sessions[].reconnecting</c>).</summary>
    public bool PeerReconnecting => item is not null && Remote.PeerReconnecting;
    /// <summary>The minute of asking again is over (<c>sessions[].reconnect_failed</c>): Retry or End.</summary>
    public bool ReconnectFailed => item is not null && Remote.ReconnectFailed;

    /// <summary>
    /// What the empty screen says while there is no picture: "Reconnecting...", "Could not reconnect", the wait for the person
    /// to accept, or "Starting video".
    /// </summary>
    public string WaitingText() =>
        PeerReconnecting ? L("Reconnecting...")
        : ReconnectFailed ? L("Could not reconnect")
        : Remote.Phase == "outgoing" ? L("Waiting for the other person to accept your request.")
        : L("Starting video");

    /// <summary>Asks the remote computer again for another minute (remote_reconnect with the stable id); only after "Could not reconnect".</summary>
    public async Task Retry()
    {
        if (!ReconnectFailed || SessionId is not { } id) return;
        Error = "";
        try { await app.Engine.PerformAsync("remote_reconnect", ("session", id)); }
        catch (EngineFailure failure) { Error = failure.Translated; }
        await app.RefreshRemote();
    }

    public string PasswordWarning(DateTimeOffset? now = null)
    {
        // Remote is this session's projection: its own password_session, never another session's.
        double seconds;
        if (Format.ParseDate(Remote.PasswordSession?.EndsAt) is { } ends) seconds = (ends - (now ?? DateTimeOffset.UtcNow)).TotalSeconds;
        else if (Remote.PasswordSession?.SecondsLeft is { } left) seconds = left;
        else return "";
        if (seconds <= 0 || seconds > 300) return "";
        return seconds <= 60
            ? L("This support session ends in 1 minute (support password limit). Ask for a new password to continue.")
            : L("This support session ends in %d minutes (support password limit). Ask for a new password to continue.", (int)Math.Ceiling(seconds / 60));
    }

    // ---- Input ----

    /// <summary>Queues an input event; pointer moves and wheel scrolls coalesce with the previous unsent one.</summary>
    public void Send(InputEvent input)
    {
        if (SessionId is not { } session) return;
        // Only the focused session gets input. Letting go of keys and buttons is the one thing a session that lost focus still
        // needs, and only if it was sent something before.
        if (input is InputEvent.ReleaseAll)
        {
            if (!inputSent) return;
            inputSent = false;
        }
        else if (!Controllable || !MayAct) return;
        else inputSent = true;
        if (input is InputEvent.Pointer && inputQueue.Count > 0 && inputQueue[^1] is InputEvent.Pointer)
        {
            inputQueue[^1] = input;
        }
        else if (input is InputEvent.Wheel w1 && inputQueue.Count > 0 && inputQueue[^1] is InputEvent.Wheel w0)
        {
            var h = (short)Math.Clamp(w0.Horizontal + w1.Horizontal, -120, 120);
            var v = (short)Math.Clamp(w0.Vertical + w1.Vertical, -120, 120);
            inputQueue[^1] = new InputEvent.Wheel(h, v);
        }
        else if (inputQueue.Count >= 64)
        {
            var index = inputQueue.FindIndex(item => item is InputEvent.Pointer or InputEvent.Wheel);
            if (index >= 0)
            {
                inputQueue.RemoveAt(index);
                inputQueue.Add(input);
            }
            else
            {
                // Drop oldest queued event rather than terminating the session on key-repeat bursts.
                inputQueue.RemoveAt(0);
                inputQueue.Add(input);
            }
        }
        else
        {
            inputQueue.Add(input);
        }
        _ = Flush(session);
    }

    /// <summary>Completes when the queued input was handed to the engine (tests).</summary>
    internal Task Flushing { get; private set; } = Task.CompletedTask;

    private Task Flush(string session)
    {
        if (sending) return Flushing;
        sending = true;
        Flushing = Run();
        return Flushing;

        async Task Run()
        {
            try
            {
                while (inputQueue.Count > 0)
                {
                    var next = inputQueue[0];
                    inputQueue.RemoveAt(0);
                    if (SessionId != session)
                    {
                        inputQueue.Clear();
                        return;
                    }
                    try
                    {
                        var posted = await app.Engine.SendAsync(next, session);
                        if (!posted && next is InputEvent.Text) Error = L("The remote computer could not type this text.");
                    }
                    catch (EngineFailure failure)
                    {
                        if (next is InputEvent.Pointer or InputEvent.Wheel) continue;
                        inputQueue.Clear();
                        Error = failure.Translated;
                        return;
                    }
                }
            }
            finally
            {
                sending = false;
            }
        }
    }

    /// <summary>A position in the picture (0…1 in both directions) as the engine expects (0…65535).</summary>
    public static ushort Normalized(double value) => (ushort)Math.Round(Math.Clamp(value, 0, 1) * 65535);

    public void MoveTo(double x, double y)
    {
        var (nx, ny) = (Normalized(x), Normalized(y));
        if (PointerMode) Action($"pointer:{nx},{ny}", quiet: true);
        else Send(new InputEvent.Pointer(nx, ny));
    }

    public void TypeText()
    {
        var text = TextToSend;
        TextToSend = "";
        if (text.Length > 0) Send(new InputEvent.Text(text));
    }

    /// <summary>Presses and releases a key, with modifiers held around it (Ctrl+C and similar).</summary>
    public void Press(ushort usage, params ushort[] modifiers)
    {
        foreach (var modifier in modifiers) Send(new InputEvent.Key(modifier, true));
        Send(new InputEvent.Key(usage, true));
        Send(new InputEvent.Key(usage, false));
        foreach (var modifier in modifiers.Reverse()) Send(new InputEvent.Key(modifier, false));
    }

    // ---- Drawing on the remote screen ----

    /// <summary>The drawing colours (same order as the engine's palette).</summary>
    public static readonly (byte R, byte G, byte B)[] Palette = [(0xE5, 0x32, 0x2D), (0xFF, 0xD4, 0x00), (0x22, 0xB1, 0x4C), (0x1E, 0x7B, 0xFF), (0xFF, 0xFF, 0xFF)];
    public static readonly string[] DrawTools = ["pen", "highlighter", "arrow"];

    /// <summary>A stroke drawn here (points 0…65535), kept to show it locally and to undo it.</summary>
    public sealed class Stroke
    {
        public ushort Id { get; init; }
        public string Tool { get; init; } = "pen";
        public byte Color { get; init; }
        public byte Width { get; init; }
        public List<(ushort X, ushort Y)> Points { get; } = [];
        public int Sent { get; set; }
    }

    private bool drawing;
    private string drawTool = "pen";
    private byte drawColor;
    private byte drawWidth = 2;
    private ushort nextStroke = 1;
    private Stroke? current;
    private readonly List<Stroke> strokes = [];

    /// <summary>The other computer shows drawings (feature "draw").</summary>
    public bool CanDraw => Remote.Viewing && Remote.PeerFeatures.Contains("draw");
    /// <summary>Pointer drags draw instead of moving the remote mouse.</summary>
    public bool Drawing { get => drawing && CanDraw; set => Set(ref drawing, value); }
    public string DrawTool { get => drawTool; set => Set(ref drawTool, DrawTools.Contains(value) ? value : "pen"); }
    public byte DrawColor { get => drawColor; set => Set(ref drawColor, (byte)Math.Min(value, Palette.Length - 1)); }
    public byte DrawWidth { get => drawWidth; set => Set(ref drawWidth, (byte)Math.Clamp((int)value, 1, 4)); }
    public IReadOnlyList<Stroke> Strokes => strokes;
    /// <summary>Raised when the local drawing changed (the screen redraws).</summary>
    public event EventHandler? DrawingChanged;

    public void BeginStroke(double x, double y)
    {
        if (!Drawing) return;
        current = new Stroke { Id = nextStroke++, Tool = DrawTool, Color = DrawColor, Width = DrawWidth };
        if (nextStroke == 0) nextStroke = 1;
        strokes.Add(current);
        current.Points.Add((Normalized(x), Normalized(y)));
        SendStroke(done: false);
    }

    public void ContinueStroke(double x, double y)
    {
        if (current is null || current.Points.Count >= 4096) return;
        var point = (Normalized(x), Normalized(y));
        if (current.Points[^1] == point) return;
        // An arrow is a straight line: its end follows the pointer.
        if (current.Tool == "arrow" && current.Points.Count > 1) current.Points[^1] = point;
        else current.Points.Add(point);
        if (current.Points.Count - current.Sent >= 8 || current.Tool == "arrow") SendStroke(done: false);
        DrawingChanged?.Invoke(this, EventArgs.Empty);
    }

    public void EndStroke()
    {
        if (current is null) return;
        SendStroke(done: true);
        current = null;
        DrawingChanged?.Invoke(this, EventArgs.Empty);
    }

    /// <summary>The points not sent yet, in messages of at most 64 points (an arrow: always its two ends).</summary>
    private void SendStroke(bool done)
    {
        if (current is not { } stroke || SessionId is not { } session || !MayAct) return;
        var points = stroke.Tool == "arrow"
            ? stroke.Points.Count > 1 ? [stroke.Points[0], stroke.Points[^1]] : [stroke.Points[0]]
            : stroke.Points.Skip(stroke.Sent).ToList();
        if (stroke.Tool == "arrow") stroke.Sent = 0;
        do
        {
            var part = points.Take(64).ToList();
            points = [.. points.Skip(64)];
            var last = points.Count == 0;
            if (stroke.Tool != "arrow") stroke.Sent += part.Count;
            var message = new System.Text.Json.Nodes.JsonObject
            {
                ["kind"] = "stroke", ["id"] = stroke.Id, ["tool"] = stroke.Tool, ["color"] = stroke.Color, ["width"] = stroke.Width,
                ["points"] = new System.Text.Json.Nodes.JsonArray([.. part.Select(item => (System.Text.Json.Nodes.JsonNode)new System.Text.Json.Nodes.JsonArray(item.Item1, item.Item2))]),
                ["done"] = done && last,
            };
            _ = SendDraw(session, message);
        }
        while (points.Count > 0);
    }

    private async Task SendDraw(string session, System.Text.Json.Nodes.JsonNode message)
    {
        try
        {
            var reply = await app.Engine.CallAsync("remote_draw", new System.Text.Json.Nodes.JsonObject { ["session"] = session, ["message"] = message.DeepClone() }.ToJsonString());
            if (reply == "false") Notice = L("The other computer cannot show drawings right now.");
        }
        catch (EngineFailure failure) { Error = failure.Translated; }
    }

    public void UndoDrawing()
    {
        if (SessionId is not { } session || strokes.Count == 0 || !MayAct) return;
        strokes.RemoveAt(strokes.Count - 1);
        _ = SendDraw(session, new System.Text.Json.Nodes.JsonObject { ["kind"] = "undo" });
        DrawingChanged?.Invoke(this, EventArgs.Empty);
    }

    public void ClearDrawing()
    {
        if (SessionId is not { } session || !MayAct) return;
        strokes.Clear();
        current = null;
        _ = SendDraw(session, new System.Text.Json.Nodes.JsonObject { ["kind"] = "clear" });
        DrawingChanged?.Invoke(this, EventArgs.Empty);
    }

    // ---- Toolbar ----

    internal void Action(string name, bool quiet = false)
    {
        if (SessionId is not { } session || !MayAct) return;
        _ = Run();

        async Task Run()
        {
            try { await app.Engine.SendAsync(new InputEvent.Action(name), session); }
            catch (EngineFailure failure) { if (!quiet) Error = failure.Translated; }
        }
    }

    public void TogglePrivacy()
    {
        if (!MayAct) return;
        Privacy = !Privacy;
        Action($"privacy:{(Privacy ? "on" : "off")}");
    }

    public void ToggleBlockInput()
    {
        if (!MayAct) return;
        BlockInput = !BlockInput;
        Action($"block:{(BlockInput ? "on" : "off")}");
    }

    public void ToggleLockWhenDone()
    {
        if (!MayAct) return;
        LockWhenDone = !LockWhenDone;
        Action($"lockend:{(LockWhenDone ? "on" : "off")}");
    }

    public void TogglePointer()
    {
        if (!MayAct) return;
        PointerMode = !PointerMode;
        if (!PointerMode) Action("pointer:off", quiet: true);
    }

    public void ToggleSound()
    {
        if (!MayAct) return;
        Sound = !Sound;
        Action(Sound ? "sound:on" : "sound:off", quiet: true);
    }

    public void ToggleVoice()
    {
        if (!MayAct) return;
        Voice = !Voice;
        Action(Voice ? "voice:on" : "voice:off", quiet: true);
    }

    private string qualityMode = "auto";
    public string QualityMode { get => qualityMode; private set => Set(ref qualityMode, value); }

    private string cursorShape = "arrow";
    public string CursorShape { get => cursorShape; private set => Set(ref cursorShape, value); }

    public void SetQualityMode(string mode)
    {
        if (mode is not ("auto" or "speed" or "quality")) return;
        QualityMode = mode;
        Action($"quality:{mode}", quiet: true);
    }

    public void LockNow() => Action("lock");
    public void CtrlAltDel() => Action("sas");

    public void SelectDisplay(ushort index)
    {
        if (SessionId is not { } session || !MayAct) return;
        _ = app.Engine.SendAsync(new InputEvent.SelectDisplay(index), session);
    }

    /// <summary>
    /// The first click arms, the second (within 5 s) restarts the remote computer; the app reconnects when
    /// it is back (up to 10 minutes).
    /// </summary>
    public void RestartComputer()
    {
        if (SessionId is not { } session || Remote.Peer is not { } peer || !MayAct) return;
        if (!RestartArmed)
        {
            RestartArmed = true;
            _ = Disarm();
            return;
        }
        RestartArmed = false;
        var now = DateTimeOffset.UtcNow;
        reconnect = new Reconnect
        {
            Host = peer,
            Signin = Remote.SigninId,
            Unattended = LastRequestUnattended || Remote.UnattendedSession,
            Until = now.AddMinutes(10),
            Since = now,
            Session = session,
        };
        Action("restart");

        async Task Disarm()
        {
            await Task.Delay(5000);
            RestartArmed = false;
        }
    }

    /// <summary>Called at every status while a restart of the remote computer is waited for (also after its session ended).</summary>
    internal void ContinueReconnect(RemoteStatus status, SessionStatus? mine, uint peer)
    {
        if (reconnect is not { } plan) return;
        if (mine is not null)
        {
            // The old session is still listed: wait. A different one (stable id, or the transport id of an older engine): the computer is back.
            if (mine.Id != plan.Session)
            {
                reconnect = null;
                Notice = "";
            }
            return;
        }
        var now = DateTimeOffset.UtcNow;
        if (now > plan.Until)
        {
            reconnect = null;
            Notice = "";
            Error = L("The remote computer did not come back within 10 minutes.");
            return;
        }
        if (now - plan.LastPresence > TimeSpan.FromSeconds(5))
        {
            plan.LastPresence = now;
            uint[] hosts = plan.Signin is { } signin ? [plan.Host, signin] : [plan.Host];
            _ = app.Engine.PerformAsync("remote_presence", ("hosts", hosts));
        }
        if (!status.Online.Contains(plan.Host)) plan.WentOffline = true;
        var ready = plan.WentOffline || now - plan.Since > TimeSpan.FromSeconds(120);
        uint? target = null;
        if (ready)
        {
            if (status.Online.Contains(plan.Host)) target = plan.Host;
            else if (plan.Signin is { } signin && status.Online.Contains(signin) && now - plan.Since > TimeSpan.FromSeconds(45)) target = signin;
            else if (plan.Unattended && now - plan.Since > TimeSpan.FromSeconds(60)) target = plan.Host;
        }
        Notice = L("The remote computer is restarting. Mixel Remote reconnects when it is back…");
        if (target is { } host && app.CanRequestSession(status) && !app.Busy && now - plan.LastTry > TimeSpan.FromSeconds(15))
        {
            plan.LastTry = now;
            var unattended = plan.Unattended || host == plan.Signin;
            _ = app.Act(() => app.Engine.PerformAsync("remote_request", ("host", host), ("password", null), ("unattended", unattended)));
        }
    }

    /// <summary>Renew the connection (new keys, same session).</summary>
    public Task Renew() => SessionId is { } session
        ? app.Act(() => app.Engine.PerformAsync("remote_restart", ("session", session)))
        : Task.CompletedTask;

    /// <summary>
    /// Ends this session for both sides (remote_close with its stable id; a session that is still being asked for is
    /// cancelled the same way). Other sessions are not touched. The hosted side ends by its consent.
    /// </summary>
    public async Task End()
    {
        if (Stopping) return;
        var consent = Remote.Consent;
        var id = item is null ? null : SessionId;
        if (id is null && consent is null) return;
        stoppedConsent = consent;
        Stopping = true;
        Clear();
        Error = "";
        try
        {
            if (id is not null) await app.Engine.PerformAsync("remote_close", ("session", id));
            else await app.Engine.PerformAsync("remote_stop_session", ("consent", consent));
        }
        catch (EngineFailure failure) { Error = L("Could not confirm that the session ended. Try End session again.") + " " + failure.Translated; }
        Stopping = false;
        await app.RefreshRemote();
    }

    // ---- Clipboard, chat, files ----

    public async Task Clipboard(bool push)
    {
        if (SessionId is not { } session || !MayAct) return;
        ClipboardResult = L("Transferring…");
        try
        {
            await app.Engine.PerformAsync("remote_clipboard", ("session", session), ("push", push));
            ClipboardResult = push ? L("Remote clipboard updated.") : L("Your clipboard was updated.");
        }
        catch (EngineFailure failure)
        {
            ClipboardResult = failure.Translated;
        }
    }

    public async Task PasteToRemote(ushort usage, ushort[] modifiers)
    {
        if (SessionId is not { } session || !Controllable || !MayAct) return;
        if (Remote is { Clipboard: true, Phase: "active", ControlPending: false })
        {
            try { await app.Engine.PerformAsync("remote_clipboard", ("session", session), ("push", true)); }
            catch (EngineFailure) { }
        }
        Press(usage, modifiers);
    }

    public void PullClipboardSoon()
    {
        if (SessionId is not { } session || !MayAct || Remote is not { Clipboard: true, Phase: "active" }) return;
        _ = PullAsync(session);

        async Task PullAsync(string id)
        {
            foreach (var delay in new[] { 150, 350 })
            {
                await Task.Delay(delay);
                if (SessionId != id || !MayAct || Remote is not { Clipboard: true, Phase: "active" }) return;
                try { await app.Engine.PerformAsync("remote_clipboard", ("session", id), ("push", false)); }
                catch (EngineFailure) { }
            }
        }
    }

    private void StartTools()
    {
        if (tools is not null) return;
        tools = new CancellationTokenSource();
        var token = tools.Token;
        _ = Loop();

        async Task Loop()
        {
            while (!token.IsCancellationRequested)
            {
                await ReadTools();
                try { await Task.Delay(700, token); }
                catch (TaskCanceledException) { return; }
            }
        }
    }

    private void StopTools()
    {
        tools?.Cancel();
        tools = null;
    }

    public async Task ReadTools()
    {
        var status = Remote;
        if (status is { Session: { } session, Phase: "active" })
        {
            try { Chat = await app.Engine.RunAsync<List<ChatMessage>?>("remote_chat_messages", ("session", session)) ?? []; }
            catch (EngineFailure) { }
        }
        if (status is { Consent: { } consent, Files: true, Phase: "active" })
        {
            try { Files = await app.Engine.RunAsync<FileView?>("remote_file_status", ("consent", consent)); }
            catch (EngineFailure) { Files = null; }
        }
        else if (Files is not null)
        {
            Files = null;
        }
    }

    public async Task SendChat()
    {
        var text = ChatDraft.Trim();
        if (SessionId is not { } session || text.Length == 0 || !MayAct) return;
        ChatDraft = "";
        try
        {
            await app.Engine.PerformAsync("remote_chat_send", ("session", session), ("text", text));
            await ReadTools();
        }
        catch (EngineFailure failure) { Error = failure.Translated; }
    }

    /// <summary>Sends the file at <paramref name="path"/> (chosen in the Open dialog).</summary>
    public async Task SendFile(string path)
    {
        if (Remote.Consent is not { } consent || !MayAct) return;
        try
        {
            await app.Engine.PerformAsync("remote_file_send", ("consent", consent), ("path", path));
            await ReadTools();
        }
        catch (EngineFailure failure) { Error = failure.Translated; }
    }

    /// <summary>The suggested name of a file the other side offers (for the Save dialog).</summary>
    public async Task<string?> OfferedName(string id)
    {
        if (Remote.Consent is not { } consent) return null;
        try { return await app.Engine.RunAsync<string?>("remote_file_pending", ("consent", consent), ("id", id)); }
        catch (EngineFailure) { return null; }
    }

    public async Task AcceptFile(string id, string path)
    {
        if (Remote.Consent is not { } consent || !MayAct) return;
        try
        {
            await app.Engine.PerformAsync("remote_file_accept", ("consent", consent), ("id", id), ("path", path));
            await ReadTools();
        }
        catch (EngineFailure failure) { Error = failure.Translated; }
    }

    public async Task CancelFile(string id)
    {
        if (Remote.Consent is not { } consent) return;
        try { await app.Engine.PerformAsync("remote_file_cancel", ("consent", consent), ("id", id)); } catch (EngineFailure) { }
        await ReadTools();
    }

    // ---- Recording ----

    /// <summary>The recording notice, plus (when a picture size change split it) where the files are: name, then name-part2, ...</summary>
    public string RecordingText => RecordingPhaseText + (Remote.RecordingParts.Count > 1 && RecordingPhaseText.Length > 0
        ? "\n" + string.Format(System.Globalization.CultureInfo.InvariantCulture, L("The recording is in {0} files: name, then name-part2, ..."), Remote.RecordingParts.Count) : "");

    private string RecordingPhaseText => Remote.Recording?.Phase switch
    {
        "waiting_for_video" => L("Waiting for a complete video frame…"),
        "recording" => L("Recording video"),
        "saving" => L("Saving recording…"),
        "saved" => L("Recording saved"),
        "saved_partial" => L("The recording stopped early. The playable portion was saved."),
        "error" => Translator.Shared.Translate(Remote.Recording?.Message ?? ""),
        _ => "",
    };

    public async Task<bool> MayRecord()
    {
        if (SessionId is not { } session) return false;
        try { return await app.Engine.RunAsync<bool>("remote_record_may_start", ("session", session)); }
        catch (EngineFailure) { return false; }
    }

    public async Task StartRecording(string path)
    {
        if (SessionId is not { } session || !MayAct) return;
        RecordingPending = true;
        try { await app.Engine.PerformAsync("remote_record_start", ("session", session), ("path", path)); }
        catch (EngineFailure failure) { Error = failure.Translated; }
        RecordingPending = false;
        await app.RefreshRemote();
    }

    public async Task StopRecording()
    {
        if (SessionId is not { } session || !MayAct) return;
        RecordingPending = true;
        try { await app.Engine.PerformAsync("remote_record_stop", ("session", session)); }
        catch (EngineFailure failure) { Error = failure.Translated; }
        RecordingPending = false;
        await app.RefreshRemote();
    }

    /// <summary>The Record button: asks whether recording is allowed, then where to save it.</summary>
    public async Task Record()
    {
        if (!await MayRecord())
        {
            Error = L("Start a permitted recording after the remote screen appears");
            return;
        }
        if (await app.ChooseSaveLocation("Mixel Remote recording.mp4") is { } path) await StartRecording(path);
    }

    // ---- Colleagues, switching sides, meetings ----

    public Task Invite(string? colleague, uint? device, bool control) => SessionId is { } session && MayAct
        ? app.Act(() => app.Engine.PerformAsync("remote_invite", ("session", session), ("user", colleague), ("device", device), ("control", control)))
        : Task.CompletedTask;

    public Task Join(Invitation invitation) => app.Act(() => app.Engine.PerformAsync("remote_join", ("invitation", invitation.Id)));

    public Task Dismiss(Invitation invitation) => app.Act(() => app.Engine.PerformAsync("remote_dismiss_invitation", ("invitation", invitation.Id)));

    public Task SwitchSides() => SessionId is { } session && MayAct
        ? app.Act(() => app.Engine.PerformAsync("remote_switch_sides", ("session", session)))
        : Task.CompletedTask;

    /// <summary>Show my screen (older computers without switching sides): a Mixel Meet room the other side is asked to watch.</summary>
    public Task ShowMyScreen() => SessionId is { } session && MayAct
        ? app.Act(async () =>
        {
            var room = await app.Engine.RunAsync<MeetingRoom>("meeting_create");
            app.OpenUrl(room.Host);
            await app.Engine.SendAsync(new InputEvent.Action($"meet:{room.Room}"), session);
            MeetingNotice = L("Your meeting opens in the browser. Choose “Share screen” there; the other person is asked to watch.");
        })
        : Task.CompletedTask;

    /// <summary>The meeting link for a colleague (when the other computer cannot invite natively).</summary>
    public async Task<MeetingRoom?> MeetingLink()
    {
        try
        {
            var room = await app.Engine.RunAsync<MeetingRoom>("meeting_create");
            app.OpenUrl(room.Host);
            return room;
        }
        catch (EngineFailure failure)
        {
            Error = failure.Translated;
            return null;
        }
    }

    public Task PrintJob(string path, bool open) => app.Act(() => app.Engine.RunAsync<string>("print_job", ("path", path), ("open", open)));
}
