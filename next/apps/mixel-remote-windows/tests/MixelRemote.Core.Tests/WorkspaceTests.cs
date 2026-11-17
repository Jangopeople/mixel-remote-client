using MixelRemote.Demo;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>The workspace rules of the Pro Console (ported from the Mac app's WorkspaceTests).</summary>
[TestClass]
public sealed class WorkspaceTests
{
    private const uint Peer = 731_204_118;

    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    private static async Task Until(Func<bool> condition)
    {
        for (var round = 0; round < 300 && !condition(); round++) await Task.Delay(10);
        Assert.IsTrue(condition(), "timed out");
    }

    private static async Task<(AppModel App, MockEngine Engine)> Viewing(OpenIn openIn = OpenIn.Tab, WhenReady whenReady = WhenReady.Stay)
    {
        var preferences = new MemoryPreferences();
        preferences.Set(WorkspaceModel.OpenInKey, openIn == OpenIn.Window ? "window" : "tab");
        preferences.Set(WorkspaceModel.WhenReadyKey, whenReady == WhenReady.Open ? "open" : "stay");
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        engine.QueueFrame(VideoFrame.Sample(320, 200, 1));
        var app = new AppModel(engine, preferences);
        app.PictureIdleDelay = 1;
        await app.RefreshAccount();
        await app.Company.Load();
        await app.RefreshRemote();
        await Until(() => app.Session.Frame is not null);
        app.Workspace.Refresh();
        return (app, engine);
    }

    [TestMethod]
    public async Task A_ready_session_becomes_a_tab_and_stays_in_devices_with_a_notice()
    {
        var (app, _) = await Viewing();
        var ws = app.Workspace;
        CollectionAssert.AreEqual(new[] { Peer }, ws.Tabs.Select(tab => tab.Peer).ToArray());
        Assert.AreEqual("ready", ws.Tabs[0].State);
        Assert.IsTrue(ws.Selected.IsConsole, "Connect must not jump away from Devices");
        CollectionAssert.AreEqual(new[] { Peer }, ws.ReadyNotices.ToArray());
        app.Session.Clear();
    }

    [TestMethod]
    public async Task The_switch_setting_opens_the_tab_at_once()
    {
        var (app, _) = await Viewing(whenReady: WhenReady.Open);
        Assert.AreEqual(WorkspaceTab.Session(Peer), app.Workspace.Selected);
        Assert.AreEqual(0, app.Workspace.ReadyNotices.Count);
        app.Session.Clear();
    }

    [TestMethod]
    public async Task Pop_out_and_dock()
    {
        var (app, _) = await Viewing(whenReady: WhenReady.Open);
        var ws = app.Workspace;
        List<uint> opened = [], closed = [];
        ws.OpenWindow = opened.Add;
        ws.CloseWindow = closed.Add;
        ws.PopOut(Peer);
        CollectionAssert.AreEqual(new[] { Peer }, opened);
        Assert.IsTrue(ws.IsInWindow(Peer));
        Assert.IsTrue(ws.Selected.IsConsole);
        ws.Dock(Peer);
        CollectionAssert.AreEqual(new[] { Peer }, closed);
        Assert.IsFalse(ws.IsInWindow(Peer));
        Assert.AreEqual(WorkspaceTab.Session(Peer), ws.Selected);
        app.Session.Clear();
    }

    [TestMethod]
    public async Task Closing_the_window_asks_and_ends_the_session_instead_of_docking_it()
    {
        var (app, engine) = await Viewing(openIn: OpenIn.Window);
        var ws = app.Workspace;
        ws.OpenWindow = _ => { };
        ws.Show(Peer);
        Assert.IsTrue(ws.IsInWindow(Peer), "default own window");
        var decision = ws.WindowCloseRequested(Peer);
        Assert.IsTrue(decision.Confirm, "a running session asks first");
        Assert.AreEqual($"End session with {ws.Tab(Peer)!.Name}?", decision.Title);
        Assert.IsTrue(ws.IsInWindow(Peer), "asking changes nothing");
        Assert.AreEqual(0, engine.CallsOf("remote_close").Count, "nothing ended before the answer");
        await ws.ConfirmWindowClose(Peer);
        Assert.AreEqual(1, engine.CallsOf("remote_close").Count, "confirmed: the session ends");
        Assert.AreNotEqual(WorkspaceTab.Session(Peer), ws.Selected, "never docked into a tab");
        app.Session.Clear();
    }

    [TestMethod]
    public async Task A_window_without_a_running_session_closes_without_asking()
    {
        var (app, engine) = await Viewing(openIn: OpenIn.Window);
        var ws = app.Workspace;
        ws.OpenWindow = _ => { };
        Assert.IsFalse(ws.WindowCloseRequested(Peer + 1).Confirm, "not a window of this workspace");
        ws.Show(Peer);
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "ready", Host = DemoFixtures.ThisComputer });
        await app.RefreshRemote();
        Assert.IsFalse(ws.WindowCloseRequested(Peer).Confirm, "its session is gone");
        ws.WindowGone(Peer);
        Assert.IsFalse(ws.IsInWindow(Peer));
        Assert.IsTrue(ws.Selected.IsConsole);
        app.Session.Clear();
    }

    [TestMethod]
    public async Task A_session_that_ends_removes_tab_window_and_notice()
    {
        var (app, engine) = await Viewing(whenReady: WhenReady.Open);
        var ws = app.Workspace;
        ws.OpenWindow = _ => { };
        List<uint> closed = [];
        ws.CloseWindow = closed.Add;
        ws.PopOut(Peer);
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "ready", Host = DemoFixtures.ThisComputer });
        await app.RefreshRemote();
        Assert.AreEqual(0, ws.Tabs.Count);
        Assert.IsFalse(ws.IsInWindow(Peer));
        CollectionAssert.AreEqual(new[] { Peer }, closed);
        Assert.IsTrue(ws.Selected.IsConsole);
    }

    [TestMethod]
    public async Task Open_in_window_instead_pops_the_new_session_out_when_it_is_ready()
    {
        var (app, engine) = await Viewing();
        var ws = app.Workspace;
        // Start over: the session ends, then a new connection is prepared with "Open in window instead".
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "ready", Host = DemoFixtures.ThisComputer });
        await app.RefreshRemote();
        List<uint> opened = [];
        ws.OpenWindow = opened.Add;
        engine.Reply("quick_phone_claim", "false");
        _ = ws.Connect(Peer, "SRV", "windows", unattended: true);
        ws.OpenAttemptInWindow();
        Assert.IsTrue(ws.Attempt!.InWindow);
        DemoFixtures.Viewing(engine);
        engine.QueueFrame(VideoFrame.Sample(320, 200, 1));
        await app.RefreshRemote();
        await Until(() => app.Session.Frame is not null);
        CollectionAssert.AreEqual(new[] { Peer }, opened);
        Assert.IsNull(ws.Attempt, "the panel gives way once the session is ready");
        app.Session.Clear();
    }

    [TestMethod]
    public async Task The_attempt_walks_through_the_four_steps()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        var app = await DemoFixtures.App(engine);
        var ws = app.Workspace;
        app.WaitStepMilliseconds = 0;
        _ = ws.Connect(Peer, "SRV-DC-02", "windows", unattended: true);
        Assert.AreEqual(0, ws.Attempt!.Step);
        Assert.AreEqual("SRV-DC-02", ws.Attempt.Name);
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "outgoing", Host = DemoFixtures.ThisComputer, Peer = Peer });
        await app.RefreshRemote();
        Assert.AreEqual(1, ws.Attempt!.Step);
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "negotiating", Host = DemoFixtures.ThisComputer, Peer = Peer, Session = DemoFixtures.SessionId });
        await app.RefreshRemote();
        Assert.AreEqual(2, ws.Attempt!.Step);
        Assert.AreEqual("connecting", ws.StateOf(Peer));
        await ws.CancelAttempt();
        Assert.IsNull(ws.Attempt);
    }

    [TestMethod]
    public async Task A_declined_request_shows_why_and_can_be_closed()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        var app = await DemoFixtures.App(engine);
        var ws = app.Workspace;
        app.WaitStepMilliseconds = 0;
        await ws.Connect(Peer, "SRV-DC-02", "windows", unattended: true);
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "outgoing", Host = DemoFixtures.ThisComputer, Peer = Peer });
        await app.RefreshRemote();
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "ready", Host = DemoFixtures.ThisComputer });
        await app.RefreshRemote();
        StringAssert.StartsWith(ws.Attempt!.Failure, "The connection was not established");
        await ws.CancelAttempt();
        Assert.IsNull(ws.Attempt);
    }

    [TestMethod]
    public void The_two_choices_are_remembered()
    {
        var preferences = new MemoryPreferences();
        var a = new AppModel(new MockEngine(), preferences).Workspace;
        Assert.AreEqual(WhenReady.Stay, a.WhenReady);
        Assert.AreEqual(OpenIn.Tab, a.OpenIn);
        a.WhenReady = WhenReady.Open;
        a.OpenIn = OpenIn.Window;
        var b = new AppModel(new MockEngine(), preferences).Workspace;
        Assert.AreEqual(WhenReady.Open, b.WhenReady);
        Assert.AreEqual(OpenIn.Window, b.OpenIn);
    }
}
