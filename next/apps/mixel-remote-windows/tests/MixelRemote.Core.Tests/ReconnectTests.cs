using MixelRemote.Demo;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>
/// Engine 2.1.0 per session: the support-password warning and unattended flag of each session, and the session that asks its
/// remote computer again ("Reconnecting...", "Could not reconnect", Retry, End) with the mock engine.
/// </summary>
[TestClass]
public sealed class ReconnectTests
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

    private static async Task<(AppModel App, MockEngine Engine)> Running(int count, Func<RemoteStatus, RemoteStatus>? change = null)
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        var status = DemoFixtures.Multi(count);
        engine.ReplyValue("remote_status", change is null ? status : change(status));
        engine.Reply("remote_chat_messages", "[]");
        engine.Reply("remote_file_status", "null");
        for (var index = 0; index < count; index++) engine.QueueFrame(Id(index), VideoFrame.Sample(64, 40, (ulong)(index + 1)));
        var app = new AppModel(engine) { PictureIdleDelay = 1 };
        await app.RefreshAccount();
        await app.Company.Load();
        await app.RefreshRemote();
        await Until(() => app.Sessions.Count == count);
        return (app, engine);
    }

    /// <summary>The engine's status while session <paramref name="index"/> asks its computer again; the top-level session is empty then.</summary>
    private static RemoteStatus Retrying(RemoteStatus status, int index, bool failed = false)
    {
        var sessions = status.Sessions!.Select((item, at) => at != index ? item : item with
        {
            Phase = "outgoing", Session = null, Control = false, Reconnecting = !failed, ReconnectFailed = failed,
            Message = failed ? "Could not reconnect" : "Reconnecting...",
        }).ToList();
        var top = index == 0 ? new RemoteStatus { Phase = "outgoing", Host = status.Host, Online = status.Online, MaxSessions = status.MaxSessions } : status;
        return top with { Sessions = sessions };
    }

    private static async Task Show(AppModel app, MockEngine engine, RemoteStatus status)
    {
        engine.ReplyValue("remote_status", status);
        await app.RefreshRemote();
    }

    // ---- A: password and unattended per session ----

    [TestMethod]
    public void The_engine_status_reads_the_per_session_fields()
    {
        const string json = """
            {"phase":"active","max_sessions":4,"password_session":{"ends_at":"2026-11-17T12:00:00Z","seconds_left":60},
             "sessions":[
              {"id":"s-1","peer":731204118,"phase":"active","password_session":{"ends_at":"2026-11-17T12:00:00Z","seconds_left":299.5},"unattended_session":true,"reconnecting":false,"reconnect_failed":false},
              {"id":"s-2","peer":268455901,"phase":"outgoing","password_session":null,"unattended_session":false,"reconnecting":true,"reconnect_failed":false,"message":"Reconnecting..."},
              {"id":"s-3","peer":519003442,"phase":"outgoing","reconnecting":false,"reconnect_failed":true,"message":"Could not reconnect"}]}
            """;
        var status = Json.Decode<RemoteStatus>(json)!;
        var (one, two, three) = (status.Sessions![0], status.Sessions[1], status.Sessions[2]);
        Assert.AreEqual(299.5, one.PasswordSession!.SecondsLeft);
        Assert.AreEqual("2026-11-17T12:00:00Z", one.PasswordSession.EndsAt);
        Assert.IsTrue(one.UnattendedSession);
        Assert.IsNull(two.PasswordSession);
        Assert.IsTrue(two.Reconnecting && !two.ReconnectFailed);
        Assert.IsTrue(three.ReconnectFailed && !three.Reconnecting);
        Assert.IsTrue(status.PasswordSession is not null, "the top-level value is still read");
        Assert.IsTrue(two.Project(status).PeerReconnecting);
        Assert.IsFalse(one.Asking);
        Assert.IsFalse(two.Asking, "asking its computer again is not waiting for Accept");
        Assert.IsFalse(three.Asking);
    }

    [TestMethod]
    public async Task The_password_warning_shows_only_in_the_session_it_belongs_to()
    {
        var now = DateTimeOffset.UtcNow;
        var (app, _) = await Running(3, status => status with
        {
            // The top-level value (an older engine, or the oldest session) must not decide for the others.
            PasswordSession = new PasswordSession { EndsAt = now.AddMinutes(4).ToString("o") },
            Sessions = [status.Sessions![0], status.Sessions[1] with { PasswordSession = new PasswordSession { EndsAt = now.AddSeconds(50).ToString("o") } }, status.Sessions[2]],
        });
        Assert.AreEqual("", app.SessionFor(Peer(0))!.PasswordWarning(now), "the oldest session has no password session of its own");
        StringAssert.Contains(app.SessionFor(Peer(1))!.PasswordWarning(now), "ends in 1 minute");
        Assert.AreEqual("", app.SessionFor(Peer(2))!.PasswordWarning(now));
        app.ClearSessions();
    }

    [TestMethod]
    public async Task Two_sessions_with_a_password_each_warn_on_their_own_clock()
    {
        var now = DateTimeOffset.UtcNow;
        var (app, _) = await Running(2, status => status with
        {
            Sessions =
            [
                status.Sessions![0] with { PasswordSession = new PasswordSession { EndsAt = now.AddMinutes(4).ToString("o") } },
                status.Sessions[1] with { PasswordSession = new PasswordSession { EndsAt = now.AddMinutes(90).ToString("o") } },
            ],
        });
        StringAssert.Contains(app.SessionFor(Peer(0))!.PasswordWarning(now), "ends in 4 minutes");
        Assert.AreEqual("", app.SessionFor(Peer(1))!.PasswordWarning(now), "two hours away: nothing yet");
        app.ClearSessions();
    }

    [TestMethod]
    public async Task Unattended_is_read_per_session()
    {
        var (app, _) = await Running(2, status => status with
        {
            UnattendedSession = true,
            Sessions = [status.Sessions![0] with { UnattendedSession = true }, status.Sessions[1]],
        });
        Assert.IsTrue(app.SessionFor(Peer(0))!.Remote.UnattendedSession);
        Assert.IsFalse(app.SessionFor(Peer(1))!.Remote.UnattendedSession, "the top-level value is not this session's");
        app.ClearSessions();
    }

    [TestMethod]
    public async Task An_older_engine_without_sessions_still_uses_the_top_level_values()
    {
        var now = DateTimeOffset.UtcNow;
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        engine.ReplyValue("remote_status", new RemoteStatus
        {
            Phase = "active", Host = DemoFixtures.ThisComputer, Session = DemoFixtures.SessionId, Peer = Peer(0), Control = true, UnattendedSession = true,
            PasswordSession = new PasswordSession { EndsAt = now.AddMinutes(2).ToString("o") },
        });
        var app = new AppModel(engine);
        await app.RefreshAccount();
        await app.RefreshRemote();
        var session = app.SessionFor(Peer(0))!;
        StringAssert.Contains(session.PasswordWarning(now), "ends in 2 minutes");
        Assert.IsTrue(session.Remote.UnattendedSession);
        app.ClearSessions();
    }

    // ---- B: reconnecting ----

    [TestMethod]
    public async Task A_session_that_asks_its_computer_again_keeps_its_tab_and_says_reconnecting()
    {
        var (app, engine) = await Running(2);
        Assert.AreEqual(2, app.Workspace.Tabs.Count);
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0));
        var tabs = app.Workspace.Tabs;
        Assert.AreEqual(2, tabs.Count, "both tabs stay");
        Assert.AreEqual("reconnecting", tabs.Single(tab => tab.Peer == Peer(0)).State);
        Assert.AreEqual(Id(0), tabs.Single(tab => tab.Peer == Peer(0)).Id, "the stable id");
        Assert.AreEqual("ready", tabs.Single(tab => tab.Peer == Peer(1)).State, "the other session is not touched");
        var session = app.SessionFor(Peer(0))!;
        Assert.IsTrue(session.PeerReconnecting);
        Assert.IsFalse(session.ReconnectFailed);
        Assert.AreEqual("Reconnecting...", session.WaitingText());
        Assert.IsNull(session.Frame, "no picture while it asks again");
        Assert.AreEqual("", app.SessionFor(Peer(1))!.PasswordWarning());
        Assert.IsFalse(app.SessionFor(Peer(1))!.PeerReconnecting);
        app.ClearSessions();
    }

    [TestMethod]
    public async Task Reconnecting_text_is_translated()
    {
        var (app, engine) = await Running(1);
        await Show(app, engine, Retrying(DemoFixtures.Multi(1), 0));
        Translator.Shared.Choice = "de";
        Assert.AreNotEqual("Reconnecting...", app.SessionFor(Peer(0))!.WaitingText());
        await Show(app, engine, Retrying(DemoFixtures.Multi(1), 0, failed: true));
        Assert.AreNotEqual("Could not reconnect", app.SessionFor(Peer(0))!.WaitingText());
        Translator.Shared.Choice = "en";
        app.ClearSessions();
    }

    [TestMethod]
    public async Task End_while_retrying_closes_that_session_by_its_stable_id_with_no_top_level_session()
    {
        var (app, engine) = await Running(2);
        var status = Retrying(DemoFixtures.Multi(2), 0);
        Assert.IsNull(status.Session, "the engine's top-level session is empty while retrying");
        await Show(app, engine, status);
        await app.SessionFor(Peer(0))!.End();
        var call = engine.CallsOf("remote_close").Single().Object;
        Assert.AreEqual(Id(0), call["session"]!.GetValue<string>());
        app.ClearSessions();
    }

    [TestMethod]
    public async Task After_a_minute_it_says_could_not_reconnect_and_retry_asks_again()
    {
        var (app, engine) = await Running(2);
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0, failed: true));
        var session = app.SessionFor(Peer(0))!;
        Assert.AreEqual("failed", app.Workspace.Tab(Peer(0))!.State, "the tab stays listed");
        Assert.IsTrue(session.ReconnectFailed);
        Assert.IsFalse(session.PeerReconnecting);
        Assert.AreEqual("Could not reconnect", session.WaitingText());
        await session.Retry();
        var call = engine.CallsOf("remote_reconnect").Single().Object;
        Assert.AreEqual(Id(0), call["session"]!.GetValue<string>());
        Assert.AreEqual(0, engine.CallsOf("remote_close").Count, "Retry does not end it");
        // The engine asks again: the tab says reconnecting again.
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0));
        Assert.AreEqual("reconnecting", app.Workspace.Tab(Peer(0))!.State);
        app.ClearSessions();
    }

    [TestMethod]
    public async Task Retry_is_only_for_a_session_that_could_not_reconnect()
    {
        var (app, engine) = await Running(1);
        await app.SessionFor(Peer(0))!.Retry();
        await Show(app, engine, Retrying(DemoFixtures.Multi(1), 0));
        await app.SessionFor(Peer(0))!.Retry();
        Assert.AreEqual(0, engine.CallsOf("remote_reconnect").Count);
        app.ClearSessions();
    }

    [TestMethod]
    public async Task A_failed_retry_shows_the_engines_text_and_keeps_the_session()
    {
        var (app, engine) = await Running(1);
        await Show(app, engine, Retrying(DemoFixtures.Multi(1), 0, failed: true));
        engine.Fail("remote_reconnect", "This session is not reconnecting.");
        var session = app.SessionFor(Peer(0))!;
        await session.Retry();
        Assert.IsTrue(session.Error.Length > 0);
        Assert.AreEqual("failed", app.Workspace.Tab(Peer(0))!.State);
        app.ClearSessions();
    }

    [TestMethod]
    public async Task End_works_after_could_not_reconnect()
    {
        var (app, engine) = await Running(1);
        await Show(app, engine, Retrying(DemoFixtures.Multi(1), 0, failed: true));
        await app.Workspace.End(Peer(0));
        Assert.AreEqual(Id(0), engine.CallsOf("remote_close").Single().Object["session"]!.GetValue<string>());
        app.ClearSessions();
    }

    [TestMethod]
    public async Task The_window_stays_while_retrying_and_a_reconnected_session_is_not_announced_again()
    {
        var (app, engine) = await Running(2);
        var ws = app.Workspace;
        ws.OpenWindow = _ => { };
        List<uint> closed = [];
        ws.CloseWindow = closed.Add;
        ws.PopOut(Peer(0));
        ws.DismissNotice(Peer(0));
        ws.DismissNotice(Peer(1));
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0));
        Assert.IsTrue(ws.IsInWindow(Peer(0)), "its window stays");
        Assert.AreEqual(0, closed.Count);
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0, failed: true));
        Assert.IsTrue(ws.IsInWindow(Peer(0)));
        // The computer is back: the same session (stable id) is active again.
        await Show(app, engine, DemoFixtures.Multi(2));
        Assert.IsTrue(ws.IsInWindow(Peer(0)));
        Assert.AreEqual(0, ws.ReadyNotices.Count, "no \"is ready\" notice for a session that only came back");
        app.ClearSessions();
    }

    [TestMethod]
    public async Task Cancel_does_not_end_a_session_that_is_asking_its_computer_again()
    {
        var (app, engine) = await Running(2);
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0));
        await app.CancelRequests();
        Assert.AreEqual(0, engine.CallsOf("remote_close").Count);
        app.ClearSessions();
    }

    [TestMethod]
    public async Task A_session_that_ends_while_retrying_loses_its_tab_and_window()
    {
        var (app, engine) = await Running(2);
        var ws = app.Workspace;
        ws.OpenWindow = _ => { };
        List<uint> closed = [];
        ws.CloseWindow = closed.Add;
        ws.PopOut(Peer(0));
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0));
        var rest = DemoFixtures.Multi(2);
        await Show(app, engine, rest with { Sessions = [rest.Sessions![1]], Session = rest.Sessions[1].Session, Peer = Peer(1) });
        Assert.AreEqual(1, ws.Tabs.Count);
        Assert.IsFalse(ws.IsInWindow(Peer(0)));
        CollectionAssert.AreEqual(new[] { Peer(0) }, closed);
        app.ClearSessions();
    }

    [TestMethod]
    public async Task Closing_the_window_of_a_retrying_session_asks_then_ends_that_session_only()
    {
        var (app, engine) = await Running(2);
        var ws = app.Workspace;
        ws.OpenWindow = _ => { };
        ws.PopOut(Peer(0));
        await Show(app, engine, Retrying(DemoFixtures.Multi(2), 0));
        var decision = ws.WindowCloseRequested(Peer(0));
        Assert.IsTrue(decision.Confirm);
        StringAssert.StartsWith(decision.Title, "End session with ");
        await ws.ConfirmWindowClose(Peer(0));
        var calls = engine.CallsOf("remote_close");
        Assert.AreEqual(1, calls.Count);
        Assert.AreEqual(Id(0), calls[0].Object["session"]!.GetValue<string>());
        app.ClearSessions();
    }
}
