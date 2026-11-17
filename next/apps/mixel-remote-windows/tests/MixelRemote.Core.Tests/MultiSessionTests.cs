using MixelRemote.Demo;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>Several technician sessions at once (engine 2.1: remote_status.sessions, up to four, each with a stable id).</summary>
[TestClass]
public sealed class MultiSessionTests
{
    private static uint Peer(int index) => DemoFixtures.Peers[index];
    private static string Id(int index) => DemoFixtures.StableId(index);

    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    [TestCleanup]
    public void Language() => Translator.Shared.Choice = "en";

    private static async Task Until(Func<bool> condition)
    {
        for (var round = 0; round < 300 && !condition(); round++) await Task.Delay(10);
        Assert.IsTrue(condition(), "timed out");
    }

    /// <summary>An app model whose engine runs <paramref name="count"/> sessions, each with its own pictures.</summary>
    private static async Task<(AppModel App, MockEngine Engine)> Running(int count, int limit = 4)
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        engine.ReplyValue("remote_status", DemoFixtures.Multi(count, limit: limit));
        engine.Reply("remote_chat_messages", "[]");
        engine.Reply("remote_file_status", "null");
        for (var index = 0; index < count; index++)
        {
            engine.QueueFrame(Id(index), VideoFrame.Sample(64, 40, (ulong)(index + 1)));
        }
        var app = new AppModel(engine) { PictureIdleDelay = 1 };
        await app.RefreshAccount();
        await app.Company.Load();
        await app.RefreshRemote();
        await Until(() => app.Sessions.Count == count && app.Sessions.All(session => session.Frame is not null));
        app.Workspace.Refresh();
        return (app, engine);
    }

    private static void ClearAll(AppModel app) => app.ClearSessions();

    private static IReadOnlyList<(string Session, string Json)> InputOf(MockEngine engine, string session) =>
        [.. engine.SentInputs.Where(item => item.Session == session)];

    [TestMethod]
    public void The_engine_status_with_sessions_is_read_in_full()
    {
        const string json = """
            {"phase":"active","message":"Connected","host":482913557,"session":"7a000000","peer":731204118,"consent":"c1","max_sessions":4,
             "sessions":[
              {"id":"s-1","session":"t-1","peer":731204118,"device":731204118,"phase":"active","message":"Connected","consent":"c1","received":4096,
               "control":true,"clipboard":true,"files":false,"recording_permitted":true,"recording":{"phase":"ready"},"displays":2,"display":1,"display_all":false,
               "print_jobs":["a.pdf"],"host_actions":["lock","sas"],"signin_id":null,"host_macs":["aa:bb"],"meeting_offer":null,"peer_features":["draw","switch"],
               "joined_session":false,"switched":false,"quality":{"pictures":99,"pictures_per_second":24.5,"picture_age_ms":31},"started_at":1800000000},
              {"id":"s-2","session":null,"peer":268455901,"device":268455901,"phase":"outgoing","message":"Waiting for the other computer to accept","consent":null,"received":0,
               "control":false,"clipboard":false,"files":false,"recording_permitted":false,"recording":null,"displays":0,"display":0,"display_all":false,
               "print_jobs":[],"host_actions":[],"signin_id":123456789,"host_macs":[],"meeting_offer":["a","b"],"peer_features":[],
               "joined_session":true,"switched":true,"quality":{"pictures":0,"pictures_per_second":0,"picture_age_ms":null},"started_at":1800000100}]}
            """;
        var status = Json.Decode<RemoteStatus>(json)!;
        Assert.AreEqual(4, status.SessionLimit);
        Assert.AreEqual(2, status.Sessions!.Count);
        var first = status.Sessions[0];
        Assert.AreEqual("s-1", first.Id);
        Assert.AreEqual("t-1", first.Session);
        Assert.AreEqual(731_204_118u, first.Peer);
        Assert.AreEqual("active", first.Phase);
        Assert.IsTrue(first.Control && first.Clipboard && first.RecordingPermitted && !first.Files);
        Assert.AreEqual(2, first.Displays);
        Assert.AreEqual(4096ul, first.Received);
        CollectionAssert.AreEqual(new[] { "lock", "sas" }, first.HostActions);
        CollectionAssert.AreEqual(new[] { "draw", "switch" }, first.PeerFeatures);
        Assert.AreEqual(24.5, first.Quality.PicturesPerSecond);
        Assert.AreEqual(31ul, first.Quality.PictureAgeMs);
        Assert.AreEqual(1_800_000_000ul, first.StartedAt);
        var second = status.Sessions[1];
        Assert.IsNull(second.Session);
        Assert.AreEqual("outgoing", second.Phase);
        Assert.AreEqual(123_456_789u, second.SigninId);
        Assert.IsNotNull(second.MeetingOffer);
        Assert.IsNull(second.Quality.PictureAgeMs);
        // The projection is the single-session shape the screens read, bound to the stable id.
        var projected = first.Project(status);
        Assert.AreEqual("s-1", projected.Session);
        Assert.IsTrue(projected.Viewing);
        Assert.AreEqual(731_204_118u, projected.Peer);
    }

    [TestMethod]
    public void An_older_engine_without_sessions_reads_as_one_session_with_a_limit_of_one()
    {
        var old = Json.Decode<RemoteStatus>("""{"phase":"active","session":"abc","consent":"c","peer":731204118,"control":true}""")!;
        Assert.IsNull(old.Sessions);
        Assert.AreEqual(1, old.SessionLimit);
        var only = old.TechnicianSessions.Single();
        Assert.AreEqual("abc", only.Id);
        Assert.AreEqual(731_204_118u, only.Peer);
        Assert.IsTrue(only.Control);
        Assert.AreEqual(0, Json.Decode<RemoteStatus>("""{"phase":"ready"}""")!.TechnicianSessions.Count);
        Assert.AreEqual(0, Json.Decode<RemoteStatus>("""{"phase":"active","session":"abc","peer":1,"sharing":true}""")!.TechnicianSessions.Count, "a shared computer has no technician session");
    }

    [TestMethod]
    public async Task Without_sessions_the_single_session_fields_make_one_tab()
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        engine.QueueFrame(VideoFrame.Sample(64, 40, 1));
        var app = new AppModel(engine) { PictureIdleDelay = 1 };
        await app.RefreshAccount();
        await app.RefreshRemote();
        await Until(() => app.Session.Frame is not null);
        app.Workspace.Refresh();
        Assert.IsNull(app.Remote.Sessions);
        Assert.AreEqual(1, app.Workspace.Tabs.Count);
        Assert.AreEqual(DemoFixtures.SessionId, app.Workspace.Tabs[0].Id);
        Assert.AreEqual(731_204_118u, app.Workspace.Tabs[0].Peer);
        Assert.IsFalse(app.CanStartConnection, "an older engine runs one session: Connect waits for its end");
        ClearAll(app);
    }

    [TestMethod]
    [DataRow(2)]
    [DataRow(3)]
    [DataRow(4)]
    public async Task Two_to_four_sessions_produce_that_many_tabs(int count)
    {
        var (app, _) = await Running(count);
        Assert.AreEqual(count, app.Workspace.Tabs.Count);
        CollectionAssert.AreEqual(Enumerable.Range(0, count).Select(Peer).ToArray(), app.Workspace.Tabs.Select(tab => tab.Peer).ToArray());
        CollectionAssert.AreEqual(Enumerable.Range(0, count).Select(Id).ToArray(), app.Workspace.Tabs.Select(tab => tab.Id).ToArray());
        Assert.IsTrue(app.Workspace.Tabs.All(tab => tab.State == "ready"));
        Assert.AreEqual(count, app.Sessions.Count);
        Assert.AreEqual(count, app.Workspace.ReadyNotices.Count, "each ready session offers its notice");
        for (var index = 0; index < count; index++) Assert.AreEqual(Id(index), app.SessionFor(Peer(index))!.SessionId);
        ClearAll(app);
    }

    [TestMethod]
    public async Task Each_session_has_its_own_picture()
    {
        var (app, engine) = await Running(3);
        for (var index = 0; index < 3; index++) Assert.AreEqual((ulong)(index + 1), app.SessionFor(Peer(index))!.Frame!.Sequence, $"session {index}");
        CollectionAssert.IsSubsetOf(engine.VideoRequests.Distinct().ToList(), new[] { Id(0), Id(1), Id(2) });
        Assert.AreEqual(3, engine.VideoRequests.Distinct().Count(), "every session fetches next_video with its own id");
        ClearAll(app);
    }

    [TestMethod]
    public async Task Focus_switching_routes_input_clipboard_and_toolbar_to_the_focused_session_only()
    {
        var (app, engine) = await Running(2);
        var (a, b) = (app.SessionFor(Peer(0))!, app.SessionFor(Peer(1))!);
        app.Workspace.Selected = WorkspaceTab.Session(Peer(0));
        Assert.AreEqual(Peer(0), app.Workspace.Focused);
        Assert.IsTrue(a.Focused && !b.Focused);
        Assert.AreSame(a, app.Session, "the focused session is the one the single-session screens read");

        a.MoveTo(0.5, 0.5);
        b.MoveTo(0.1, 0.1);
        a.Press(KeyMap.Shift);
        b.Press(KeyMap.Shift);
        await a.Flushing;
        await b.Flushing;
        await a.Clipboard(push: true);
        await b.Clipboard(push: true);
        a.TogglePrivacy();
        b.TogglePrivacy();
        b.CtrlAltDel();
        a.SelectDisplay(0);
        b.SelectDisplay(0);
        await Task.Delay(30);
        Assert.IsTrue(InputOf(engine, Id(0)).Count >= 4, "the focused session gets pointer, keys, privacy and display");
        Assert.AreEqual(0, InputOf(engine, Id(1)).Count, "the other session gets nothing");
        var clipboard = engine.CallsOf("remote_clipboard");
        Assert.AreEqual(1, clipboard.Count);
        Assert.AreEqual(Id(0), clipboard[0].Object["session"]!.GetValue<string>());
        Assert.IsTrue(a.Privacy);
        Assert.IsFalse(b.Privacy, "an ignored toolbar action does not even toggle");

        // The second tab comes to the front: now it is the only one that receives anything.
        var before = engine.SentInputs.Count;
        app.Workspace.Selected = WorkspaceTab.Session(Peer(1));
        Assert.AreSame(b, app.Session);
        a.MoveTo(0.9, 0.9);
        b.MoveTo(0.2, 0.3);
        await b.Clipboard(push: false);
        await a.Clipboard(push: false);
        b.LockNow();
        a.LockNow();
        await Task.Delay(30);
        var after = engine.SentInputs.Skip(before).ToList();
        Assert.IsTrue(after.All(item => item.Session == Id(1)), "after the switch only the second session receives input: " + string.Join(",", after.Select(item => item.Session)));
        Assert.AreEqual(2, after.Count);
        Assert.AreEqual(2, engine.CallsOf("remote_clipboard").Count);
        Assert.AreEqual(Id(1), engine.CallsOf("remote_clipboard")[1].Object["session"]!.GetValue<string>());
        ClearAll(app);
    }

    [TestMethod]
    public async Task A_pop_out_window_is_focused_while_it_is_active()
    {
        var (app, engine) = await Running(2);
        var ws = app.Workspace;
        ws.OpenWindow = _ => { };
        ws.CloseWindow = _ => { };
        ws.Selected = WorkspaceTab.Session(Peer(0));
        ws.PopOut(Peer(1));
        Assert.AreEqual(Peer(0), ws.Focused, "the main window is active: its tab in front is focused");
        ws.WindowActivated(Peer(1));
        Assert.AreEqual(Peer(1), ws.Focused);
        app.SessionFor(Peer(1))!.MoveTo(0.4, 0.4);
        app.SessionFor(Peer(0))!.MoveTo(0.4, 0.4);
        await Task.Delay(30);
        Assert.AreEqual(1, InputOf(engine, Id(1)).Count);
        Assert.AreEqual(0, InputOf(engine, Id(0)).Count);
        // The main window comes back with the first session in front: the window's session gets nothing more.
        ws.MainActivated();
        ws.Selected = WorkspaceTab.Session(Peer(0));
        Assert.AreEqual(Peer(0), ws.Focused);
        app.SessionFor(Peer(1))!.MoveTo(0.6, 0.6);
        app.SessionFor(Peer(0))!.MoveTo(0.6, 0.6);
        await Task.Delay(30);
        Assert.AreEqual(1, InputOf(engine, Id(1)).Count, "the unfocused window sends nothing");
        Assert.AreEqual(1, InputOf(engine, Id(0)).Count);
        ws.WindowDeactivated(Peer(1));
        Assert.AreEqual(Peer(0), ws.Focused);
        ClearAll(app);
    }

    [TestMethod]
    public async Task Unfocused_sessions_send_nothing_at_all()
    {
        var (app, engine) = await Running(4);
        Assert.IsTrue(app.Workspace.Selected.IsConsole);
        Assert.IsNull(app.Workspace.Focused);
        foreach (var session in app.Sessions)
        {
            session.MoveTo(0.5, 0.5);
            session.Press(KeyMap.Shift);
            session.Send(new InputEvent.ButtonClick(1, true, 1));
            session.Send(new InputEvent.ReleaseAll());
            session.TextToSend = "typed";
            session.TypeText();
            session.TogglePrivacy();
            session.RestartComputer();
            session.CtrlAltDel();
            await session.Clipboard(push: true);
            session.ChatDraft = "hello";
            await session.SendChat();
            await session.SendFile(@"C:\Temp\a.pdf");
        }
        await Task.Delay(30);
        Assert.AreEqual(0, engine.SentInputs.Count);
        foreach (var command in new[] { "remote_clipboard", "remote_chat_send", "remote_file_send", "remote_draw", "remote_record_start" })
        {
            Assert.AreEqual(0, engine.CallsOf(command).Count, command);
        }
        ClearAll(app);
    }

    [TestMethod]
    public async Task Closing_one_session_ends_only_that_session()
    {
        var (app, engine) = await Running(3);
        await app.Workspace.End(Peer(1));
        var closes = engine.CallsOf("remote_close");
        Assert.AreEqual(1, closes.Count);
        Assert.AreEqual(Id(1), closes[0].Object["session"]!.GetValue<string>(), "the stable id, not the transport id");
        Assert.AreEqual(0, engine.CallsOf("remote_stop_session").Count);
        Assert.IsNull(app.SessionFor(Peer(1))!.Frame, "its picture stops");
        Assert.IsNotNull(app.SessionFor(Peer(0))!.Frame, "the others keep theirs");
        Assert.IsNotNull(app.SessionFor(Peer(2))!.Frame);
        Assert.IsFalse(app.SessionFor(Peer(0))!.Stopping);
        Assert.IsFalse(app.SessionFor(Peer(2))!.Stopping);
        ClearAll(app);
    }

    [TestMethod]
    public async Task Cancelling_a_request_closes_the_session_that_is_still_outgoing()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        var status = DemoFixtures.Multi(2, phase: "active");
        status = status with { Sessions = [status.Sessions![0], status.Sessions[1] with { Phase = "outgoing", Session = null, Consent = null }] };
        engine.ReplyValue("remote_status", status);
        engine.Reply("remote_chat_messages", "[]");
        engine.Reply("remote_file_status", "null");
        var app = await DemoFixtures.App(engine);
        await app.CancelRequests();
        var closes = engine.CallsOf("remote_close");
        Assert.AreEqual(1, closes.Count);
        Assert.AreEqual(Id(1), closes[0].Object["session"]!.GetValue<string>());
        ClearAll(app);
    }

    [TestMethod]
    public async Task A_session_that_ends_removes_only_its_tab()
    {
        var (app, engine) = await Running(3);
        var ws = app.Workspace;
        List<uint> closedWindows = [];
        ws.OpenWindow = _ => { };
        ws.CloseWindow = closedWindows.Add;
        ws.PopOut(Peer(2));
        ws.Selected = WorkspaceTab.Session(Peer(1));
        // The engine drops the second session; the others go on.
        var left = DemoFixtures.Multi(3);
        engine.ReplyValue("remote_status", left with { Sessions = [left.Sessions![0], left.Sessions[2]] });
        await app.RefreshRemote();
        CollectionAssert.AreEqual(new[] { Peer(0), Peer(2) }, ws.Tabs.Select(tab => tab.Peer).ToArray());
        Assert.IsTrue(ws.Selected.IsConsole, "its tab was in front: Devices comes back");
        Assert.IsTrue(ws.IsInWindow(Peer(2)), "another session's window stays");
        Assert.AreEqual(0, closedWindows.Count);
        Assert.IsNull(app.SessionFor(Peer(1)));
        Assert.AreEqual(2, app.Sessions.Count);
        Assert.IsNotNull(app.SessionFor(Peer(0))!.Frame);
        // The window's own session ends too: its window closes, the first tab stays.
        engine.ReplyValue("remote_status", left with { Sessions = [left.Sessions[0]] });
        await app.RefreshRemote();
        CollectionAssert.AreEqual(new[] { Peer(2) }, closedWindows);
        CollectionAssert.AreEqual(new[] { Peer(0) }, ws.Tabs.Select(tab => tab.Peer).ToArray());
        ClearAll(app);
    }

    [TestMethod]
    public async Task The_status_phase_ready_does_not_gate_Connect_while_fewer_than_the_limit_are_open()
    {
        foreach (var open in new[] { 1, 2, 3 })
        {
            var engine = new MockEngine();
            DemoFixtures.Company(engine);
            engine.ReplyValue("remote_status", DemoFixtures.Multi(open));
            engine.Reply("remote_chat_messages", "[]");
            engine.Reply("remote_file_status", "null");
            engine.Reply("quick_phone_claim", "false");
            var app = await DemoFixtures.App(engine);
            Assert.AreNotEqual("ready", app.Remote.Phase);
            Assert.IsTrue(app.CanStartConnection, $"{open} open");
            Assert.IsTrue(app.CanRequestSession(app.Remote));
            await app.Connect(Peers(open), unattended: true, checkPhone: false);
            Assert.AreEqual(1, engine.CallsOf("remote_request").Count, $"{open} open: the request is sent");
            Assert.AreEqual(Peers(open), engine.CallsOf("remote_request")[0].Object["host"]!.GetValue<uint>());
            Assert.AreEqual("", app.Error);
            app.ClearSessions();
        }
        var full = new MockEngine();
        DemoFixtures.Company(full);
        full.ReplyValue("remote_status", DemoFixtures.Multi(4));
        var fullApp = await DemoFixtures.App(full);
        Assert.IsFalse(fullApp.CanStartConnection, "the limit is reached");
        fullApp.ClearSessions();

        static uint Peers(int open) => DemoFixtures.Peers[open];
    }

    [TestMethod]
    public async Task A_fifth_request_shows_the_typed_error_as_a_friendly_sentence()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        engine.ReplyValue("remote_status", DemoFixtures.Multi(4));
        engine.Reply("remote_chat_messages", "[]");
        engine.Reply("remote_file_status", "null");
        engine.Reply("quick_phone_claim", "false");
        engine.Fail("remote_request", """{"hint":"too_many_sessions","max":4,"message":"At most 4 sessions at once"}""");
        var app = await DemoFixtures.App(engine);
        await app.Workspace.Connect(DemoFixtures.Peers[4], "Fifth", "windows", unattended: true, checkPhone: false);
        Assert.AreEqual(1, engine.CallsOf("remote_request").Count, "the engine decides, the app does not drop the request");
        const string sentence = "You already have 4 sessions open, the most Mixel Remote allows. Close one to start another.";
        Assert.AreEqual(sentence, app.Error);
        Assert.AreEqual(sentence, app.Workspace.Attempt!.Failure);
        Assert.AreEqual(4, app.Sessions.Count, "the four sessions go on");
        Assert.IsFalse(app.Error.Contains('{', StringComparison.Ordinal), "never the raw JSON");
        app.ClearSessions();
    }

    [TestMethod]
    public void Typed_errors_are_read_and_translated()
    {
        var many = new EngineFailure("""{"hint":"too_many_sessions","max":4,"message":"x"}""");
        Assert.AreEqual(EngineFailure.TooManySessions, many.Hint);
        Assert.AreEqual(4, many.Max);
        var again = new EngineFailure("""{"hint":"already_connected","message":"Already connected to this computer"}""");
        Assert.AreEqual("You are already connected to this computer.", again.Translated);
        var plain = new EngineFailure("The other computer declined");
        Assert.IsNull(plain.Hint);
        Assert.AreEqual("The other computer declined", plain.Translated);
        Assert.IsNull(new EngineFailure("{not json").Hint);
        Translator.Shared.Choice = "de";
        StringAssert.Contains(many.Translated, "4", "German text carries the number");
        Assert.AreNotEqual("You already have 4 sessions open, the most Mixel Remote allows. Close one to start another.", many.Translated);
        Translator.Shared.Choice = "fr";
        Assert.AreNotEqual("You are already connected to this computer.", again.Translated);
        Translator.Shared.Choice = "it";
        Assert.AreNotEqual("You are already connected to this computer.", again.Translated);
    }

    [TestMethod]
    public async Task A_transport_renewal_keeps_the_tab_and_the_model()
    {
        var (app, engine) = await Running(2);
        var model = app.SessionFor(Peer(0))!;
        var tab = app.Workspace.Tabs[0];
        app.Workspace.Selected = WorkspaceTab.Session(Peer(0));
        Assert.AreNotEqual("renewed-transport", app.Remote.Sessions![0].Session);
        // Renewal: same stable id, a new transport session id.
        var renewed = DemoFixtures.Multi(2);
        engine.ReplyValue("remote_status", renewed with { Sessions = [renewed.Sessions![0] with { Session = "renewed-transport" }, renewed.Sessions[1]] });
        await app.RefreshRemote();
        Assert.AreEqual("renewed-transport", app.Remote.Sessions![0].Session);
        Assert.AreSame(model, app.SessionFor(Peer(0)), "the same model");
        Assert.AreEqual(2, app.Workspace.Tabs.Count);
        Assert.AreEqual(tab, app.Workspace.Tabs[0], "the same tab, same stable id");
        Assert.AreEqual(Id(0), model.SessionId);
        Assert.IsNotNull(model.Frame, "the picture stays");
        Assert.AreEqual(1, app.Workspace.Tabs.Count(item => item.Peer == Peer(0)));
        Assert.IsTrue(app.Workspace.Selected.Peer == Peer(0), "still in front");
        model.MoveTo(0.5, 0.5);
        await model.Flushing;
        Assert.AreEqual(1, InputOf(engine, Id(0)).Count, "calls keep carrying the stable id");
        Assert.AreEqual(engine.SentInputs.Count, InputOf(engine, Id(0)).Count + InputOf(engine, Id(1)).Count);
        CollectionAssert.IsSubsetOf(engine.VideoRequests.Distinct().ToList(), new[] { Id(0), Id(1) }, "no picture request ever used a transport id");
        ClearAll(app);
    }

    [TestMethod]
    public async Task A_background_session_keeps_its_last_picture_and_stops_fetching()
    {
        var (app, engine) = await Running(2);
        app.Workspace.Selected = WorkspaceTab.Session(Peer(0));
        await Task.Delay(50);
        var hidden = engine.VideoRequests.Count(session => session == Id(1));
        var shown = engine.VideoRequests.Count(session => session == Id(0));
        await Task.Delay(150);
        Assert.AreEqual(hidden, engine.VideoRequests.Count(session => session == Id(1)), "the hidden session is not fetched");
        Assert.IsTrue(engine.VideoRequests.Count(session => session == Id(0)) > shown, "the visible one is");
        Assert.IsNotNull(app.SessionFor(Peer(1))!.Frame, "its last picture stays");
        // Coming to the front resumes it.
        app.Workspace.Selected = WorkspaceTab.Session(Peer(1));
        await Until(() => engine.VideoRequests.Count(session => session == Id(1)) > hidden);
        ClearAll(app);
    }

    [TestMethod]
    public async Task The_tab_and_the_attempt_follow_a_new_session_from_asked_to_ready()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        engine.ReplyValue("remote_status", DemoFixtures.Multi(1));
        engine.Reply("remote_chat_messages", "[]");
        engine.Reply("remote_file_status", "null");
        engine.Reply("quick_phone_claim", "false");
        engine.Reply("remote_request", "\"" + Id(1) + "\"");
        engine.QueueFrame(Id(0), VideoFrame.Sample(64, 40, 1));
        engine.QueueFrame(Id(1), VideoFrame.Sample(64, 40, 1));
        var app = new AppModel(engine) { PictureIdleDelay = 1 };
        await app.RefreshAccount();
        await app.Company.Load();
        await app.RefreshRemote();
        await Until(() => app.Session.Frame is not null);
        _ = app.Workspace.Connect(Peer(1), "Second", "windows", unattended: true, checkPhone: false);
        var asked = DemoFixtures.Multi(2);
        engine.ReplyValue("remote_status", asked with { Sessions = [asked.Sessions![0], asked.Sessions[1] with { Phase = "outgoing", Session = null, Consent = null }] });
        await app.RefreshRemote();
        Assert.AreEqual(1, app.Workspace.Attempt!.Step);
        Assert.AreEqual(1, app.Workspace.Tabs.Count, "an asked session is no tab yet");
        engine.ReplyValue("remote_status", DemoFixtures.Multi(2));
        await app.RefreshRemote();
        await Until(() => app.SessionFor(Peer(1))?.Frame is not null);
        Assert.AreEqual(2, app.Workspace.Tabs.Count);
        Assert.IsNull(app.Workspace.Attempt, "the panel gives way once the session is ready");
        ClearAll(app);
    }
}
