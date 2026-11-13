using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>
/// The session on the technician's side: the remote picture, keyboard and mouse, and the toolbar
/// (displays, privacy screen, block input, pointer, lock, Ctrl+Alt+Del, restart, sound, clipboard, files,
/// chat, recording, invite a colleague, switch sides, end).
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

    internal SessionModel(AppModel app) => this.app = app;

    /// <summary>The newest picture (the screen draws it).</summary>
    public VideoFrame? Frame { get => frame; private set => Set(ref frame, value); }
    public int Fps { get => fps; private set => Set(ref fps, value); }
    public bool Stopping { get => stopping; private set => Set(ref stopping, value); }
    public string Error { get => error; set => Set(ref error, value); }
    public string Notice { get => notice; set => Set(ref notice, value); }
    public bool Privacy { get => privacy; private set => Set(ref privacy, value); }
    public bool BlockInput { get => blockInput; private set => Set(ref blockInput, value); }
    public bool LockWhenDone { get => lockWhenDone; private set => Set(ref lockWhenDone, value); }
    public bool PointerMode { get => pointerMode; set => Set(ref pointerMode, value); }
    public bool Sound { get => sound; private set => Set(ref sound, value); }
    public bool RestartArmed { get => restartArmed; private set => Set(ref restartArmed, value); }
    public List<ChatMessage> Chat { get => chat; private set => SetList(ref chat, value); }
    public string ChatDraft { get => chatDraft; set => Set(ref chatDraft, value); }
    public FileView? Files { get => files; private set => SetList(ref files, value); }
    public string ClipboardResult { get => clipboardResult; set => Set(ref clipboardResult, value); }
    public string TextToSend { get => textToSend; set => Set(ref textToSend, value); }
    public bool RecordingPending { get => recordingPending; private set => Set(ref recordingPending, value); }
    public string MeetingNotice { get => meetingNotice; private set => Set(ref meetingNotice, value); }
    public string DismissedMeetingRoom { get; set; } = "";
    /// <summary>A print job of the other computer arrived (the session window opens the print jobs).</summary>
    public event EventHandler? PrintJobArrived;
    /// <summary>Raised for every new picture (the screen redraws without going through bindings).</summary>
    public event EventHandler<VideoFrame>? FrameArrived;

    internal bool LastRequestUnattended { get; set; }
    /// <summary>Milliseconds to wait when no picture is ready yet.</summary>
    internal int IdleDelay { get; set; } = 16;

    public RemoteStatus Remote => app.Remote;
    public string? SessionId => Remote.Session;
    /// <summary>The picture is shown and keyboard and mouse go to the other computer.</summary>
    public bool Controllable => !Stopping && Remote.Viewing && Remote.Control && Frame is not null && !LocallyStopped;
    private bool LocallyStopped => Remote.Consent is not null && Remote.Consent == stoppedConsent;
    public bool CanSwitchSides => Remote.Phase == "active" && Remote.PeerFeatures.Contains("switch");

    internal void StatusChanged(RemoteStatus previous, RemoteStatus next)
    {
        if (next.Session != toggleSession)
        {
            // A session this computer viewed ended: offer a note on it (companies only).
            if (toggleSession is not null && previous.Viewing && previous.Peer is { } helped && app.Company.Tenant is not null) NoteFor = helped;
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
        }
        if (next.Viewing && !Stopping && !LocallyStopped && next.Session is { } session)
        {
            if (viewingSession != session) StartVideo(session);
        }
        else if (viewingSession is not null)
        {
            Clear();
        }
        if (next.Phase is "active" or "incoming" or "outgoing") StartTools();
        else StopTools();
        if (!next.InSession) stoppedConsent = null;
        if (next.Peer is { } peer && next.HostMacs.Count > 0) app.Devices.RememberMacs(peer, next.HostMacs);
        if (next.PrintJobs.Count > previous.PrintJobs.Count && next.Viewing) PrintJobArrived?.Invoke(this, EventArgs.Empty);
        ContinueReconnect(next);
    }

    /// <summary>Stops the picture and forgets it.</summary>
    public void Clear()
    {
        video?.Cancel();
        video = null;
        viewingSession = null;
        Frame = null;
        Fps = 0;
        shownTimes.Clear();
        inputQueue.Clear();
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
        if (first) Raise(nameof(Frame));
        FrameArrived?.Invoke(this, decoded);
        var now = DateTimeOffset.UtcNow;
        shownTimes.Enqueue(now);
        while (shownTimes.Count > 0 && now - shownTimes.Peek() > TimeSpan.FromSeconds(1)) shownTimes.Dequeue();
        Fps = shownTimes.Count;
    }

    /// <summary>For screenshots and tests: show this picture without a running engine.</summary>
    public void ShowSample(byte[] data) => Show(VideoFrame.Decode(data));

    // ---- Input ----

    /// <summary>Queues an input event; pointer moves replace the previous unsent move.</summary>
    public void Send(InputEvent input)
    {
        if (SessionId is not { } session) return;
        if (input is not InputEvent.ReleaseAll && !Controllable) return;
        if (input is InputEvent.Pointer && inputQueue.Count > 0 && inputQueue[^1] is InputEvent.Pointer)
        {
            inputQueue[^1] = input;
        }
        else if (inputQueue.Count >= 64)
        {
            inputQueue.Clear();
            Error = L("Input delivery stalled; the session has been stopped");
            _ = app.Engine.PerformAsync("remote_close", ("session", session));
            return;
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
        if (current is not { } stroke || SessionId is not { } session) return;
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
        if (SessionId is not { } session || strokes.Count == 0) return;
        strokes.RemoveAt(strokes.Count - 1);
        _ = SendDraw(session, new System.Text.Json.Nodes.JsonObject { ["kind"] = "undo" });
        DrawingChanged?.Invoke(this, EventArgs.Empty);
    }

    public void ClearDrawing()
    {
        if (SessionId is not { } session) return;
        strokes.Clear();
        current = null;
        _ = SendDraw(session, new System.Text.Json.Nodes.JsonObject { ["kind"] = "clear" });
        DrawingChanged?.Invoke(this, EventArgs.Empty);
    }

    /// <summary>After a session: the session the technician may add a note to (Remote ID of the other computer).</summary>
    public uint? NoteFor { get; private set; }

    public void NoteTaken() => NoteFor = null;

    // ---- Toolbar ----

    internal void Action(string name, bool quiet = false)
    {
        if (SessionId is not { } session) return;
        _ = Run();

        async Task Run()
        {
            try { await app.Engine.SendAsync(new InputEvent.Action(name), session); }
            catch (EngineFailure failure) { if (!quiet) Error = failure.Translated; }
        }
    }

    public void TogglePrivacy()
    {
        Privacy = !Privacy;
        Action($"privacy:{(Privacy ? "on" : "off")}");
    }

    public void ToggleBlockInput()
    {
        BlockInput = !BlockInput;
        Action($"block:{(BlockInput ? "on" : "off")}");
    }

    public void ToggleLockWhenDone()
    {
        LockWhenDone = !LockWhenDone;
        Action($"lockend:{(LockWhenDone ? "on" : "off")}");
    }

    public void TogglePointer()
    {
        PointerMode = !PointerMode;
        if (!PointerMode) Action("pointer:off", quiet: true);
    }

    public void ToggleSound()
    {
        Sound = !Sound;
        Action(Sound ? "sound:on" : "sound:off", quiet: true);
    }

    public void LockNow() => Action("lock");
    public void CtrlAltDel() => Action("sas");

    public void SelectDisplay(ushort index)
    {
        if (SessionId is not { } session) return;
        _ = app.Engine.SendAsync(new InputEvent.SelectDisplay(index), session);
    }

    /// <summary>
    /// The first click arms, the second (within 5 s) restarts the remote computer; the app reconnects when
    /// it is back (up to 10 minutes).
    /// </summary>
    public void RestartComputer()
    {
        if (SessionId is not { } session || Remote.Peer is not { } peer) return;
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

    private void ContinueReconnect(RemoteStatus status)
    {
        if (reconnect is not { } plan) return;
        if (status.Phase == "active")
        {
            if (status.Session != plan.Session)
            {
                reconnect = null;
                Notice = "";
            }
            return;
        }
        if (status.Phase is "negotiating" or "outgoing" or "deciding") return;
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
        if (target is { } host && status.Phase == "ready" && !app.Busy && now - plan.LastTry > TimeSpan.FromSeconds(15))
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

    /// <summary>Ends the session for both sides.</summary>
    public async Task End()
    {
        if (Remote.Consent is not { } consent || Stopping) return;
        stoppedConsent = consent;
        Stopping = true;
        Clear();
        Error = "";
        try { await app.Engine.PerformAsync("remote_stop_session", ("consent", consent)); }
        catch (EngineFailure failure) { Error = L("Could not confirm that the session ended. Try End session again.") + " " + failure.Translated; }
        Stopping = false;
        await app.RefreshRemote();
    }

    // ---- Clipboard, chat, files ----

    public async Task Clipboard(bool push)
    {
        if (SessionId is not { } session) return;
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
        if (SessionId is not { } session || text.Length == 0) return;
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
        if (Remote.Consent is not { } consent) return;
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
        if (Remote.Consent is not { } consent) return;
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

    public string RecordingText => Remote.Recording?.Phase switch
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
        if (SessionId is not { } session) return;
        RecordingPending = true;
        try { await app.Engine.PerformAsync("remote_record_start", ("session", session), ("path", path)); }
        catch (EngineFailure failure) { Error = failure.Translated; }
        RecordingPending = false;
        await app.RefreshRemote();
    }

    public async Task StopRecording()
    {
        if (SessionId is not { } session) return;
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

    public Task Invite(string? colleague, uint? device, bool control) => SessionId is { } session
        ? app.Act(() => app.Engine.PerformAsync("remote_invite", ("session", session), ("user", colleague), ("device", device), ("control", control)))
        : Task.CompletedTask;

    public Task Join(Invitation invitation) => app.Act(() => app.Engine.PerformAsync("remote_join", ("invitation", invitation.Id)));

    public Task Dismiss(Invitation invitation) => app.Act(() => app.Engine.PerformAsync("remote_dismiss_invitation", ("invitation", invitation.Id)));

    public Task SwitchSides() => SessionId is { } session
        ? app.Act(() => app.Engine.PerformAsync("remote_switch_sides", ("session", session)))
        : Task.CompletedTask;

    /// <summary>Show my screen (older computers without switching sides): a Mixel Meet room the other side is asked to watch.</summary>
    public Task ShowMyScreen() => SessionId is { } session
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
