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
}
