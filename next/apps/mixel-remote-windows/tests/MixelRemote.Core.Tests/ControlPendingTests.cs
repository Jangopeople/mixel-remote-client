using MixelRemote.Demo;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>Fast first picture: the picture is up before the control channel is open. The label says "Connecting control…" then, not "View only".</summary>
[TestClass]
public sealed class ControlPendingTests
{
    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    private static async Task<AppModel> With(bool control, bool pending)
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        engine.ReplyValue("remote_status", new RemoteStatus
        {
            Phase = "active", Host = DemoFixtures.ThisComputer, Session = DemoFixtures.SessionId, Peer = 731_204_118, Control = control, ControlPending = pending,
        });
        var app = new AppModel(engine);
        await app.RefreshAccount();
        await app.RefreshRemote();
        return app;
    }

    [TestMethod]
    public async Task The_label_follows_the_control_state()
    {
        Assert.AreEqual("Connected in view-only mode.", (await With(false, false)).ConnectionMessage, "a session that really is view only");
        Assert.AreEqual("Connecting control…", (await With(false, true)).ConnectionMessage, "picture up, control channel still opening");
        Assert.AreEqual("Connected. Keyboard and mouse control is enabled.", (await With(true, false)).ConnectionMessage, "channel open");
    }

    [TestMethod]
    public void The_engine_status_carries_the_pending_state_and_old_statuses_stay_valid()
    {
        var pending = Json.Decode<RemoteStatus>("""{"phase":"active","control":false,"control_pending":true}""")!;
        Assert.IsTrue(pending.ControlPending);
        var old = Json.Decode<RemoteStatus>("""{"phase":"active","control":true}""")!;
        Assert.IsFalse(old.ControlPending);
    }

    [TestMethod]
    public void The_new_text_is_translated_in_every_language()
    {
        foreach (var language in new[] { "de", "fr", "it" })
        {
            Translator.Shared.Choice = language;
            Assert.AreNotEqual("Connecting control…", T.L("Connecting control…"), language);
        }
    }
}
