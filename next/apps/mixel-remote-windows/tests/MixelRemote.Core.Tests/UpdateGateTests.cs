using System.Text.Json;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;

namespace MixelRemote.Tests;

[TestClass]
public sealed class UpdateGateTests
{
    [TestMethod]
    public void AnUpdateHeldBackBySessionIsReadFromTheEngineAnswer()
    {
        var waiting = JsonSerializer.Deserialize<UpdateInfo>(
            """{"version":"2.1.1","installable":true,"silent":false,"needs_password":false,"waits_for_session":true,"idle_seconds_left":42}""", Json.Options)!;
        Assert.IsTrue(waiting.WaitsForSession);
        Assert.AreEqual(42, waiting.IdleSecondsLeft);
        Assert.IsFalse(waiting.Silent, "nothing installs by itself while it waits");

        var older = JsonSerializer.Deserialize<UpdateInfo>("""{"version":"2.1.1","installable":true,"silent":true,"needs_password":false}""", Json.Options)!;
        Assert.IsFalse(older.WaitsForSession, "an engine that does not report it is not waiting");
        Assert.IsNull(older.IdleSecondsLeft);
    }
}

[TestClass]
public sealed class ReconnectingTests
{
    [TestMethod]
    public void TheSessionStaysOpenWhileTheRegistrationIsRestored()
    {
        Translator.Shared.Choice = "en";
        var status = JsonSerializer.Deserialize<RemoteStatus>("""{"phase":"active","reconnecting":true}""", Json.Options)!;
        Assert.IsTrue(status.Reconnecting);
        Assert.AreEqual("active", status.Phase);
        Assert.IsFalse(JsonSerializer.Deserialize<RemoteStatus>("""{"phase":"active"}""", Json.Options)!.Reconnecting);
    }

    [TestMethod]
    public void UnattendedRefusedIsReadFromTheEngineStatus()
    {
        var status = JsonSerializer.Deserialize<RemoteStatus>("""{"phase":"outgoing","unattended_refused":true,"unattended_session":false}""", Json.Options)!;
        Assert.IsTrue(status.UnattendedRefused);
        Assert.IsFalse(JsonSerializer.Deserialize<RemoteStatus>("""{"phase":"active"}""", Json.Options)!.UnattendedRefused);
    }

    [TestMethod]
    public void TheLastRefusalIsReadFromTheEngineStatus()
    {
        var status = JsonSerializer.Deserialize<RemoteStatus>("""{"phase":"active","last_refusal":{"peer":731204118,"message":"plan limit","at":1763380000}}""", Json.Options)!;
        Assert.AreEqual(731204118u, status.LastRefusal!.Peer);
        Assert.AreEqual("plan limit", status.LastRefusal.Message);
        Assert.IsNull(JsonSerializer.Deserialize<RemoteStatus>("""{"phase":"active"}""", Json.Options)!.LastRefusal);
    }
}
