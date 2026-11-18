using MixelRemote.Demo;
using MixelRemote.Engine;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>The pre-warm trigger: remote_prewarm on only while the Devices list is shown with a device online, the app is in front
/// and the plan and role allow connecting; off otherwise.</summary>
[TestClass]
public sealed class PrewarmTests
{
    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    private static bool? LastOn(MockEngine engine) => engine.CallsOf("remote_prewarm").LastOrDefault()?.Object["on"]?.GetValue<bool>();

    private static async Task<(MockEngine Engine, AppModel App)> Company(string role = "owner")
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine, "ready", role);
        return (engine, await DemoFixtures.App(engine));
    }

    [TestMethod]
    public void Gate_truth_table()
    {
        static bool W(bool shown = true, bool active = true, bool signedIn = true, bool connected = true, bool allowed = true, bool blocked = false, bool online = true)
            => DevicesModel.PrewarmWanted(shown, active, signedIn, connected, allowed, blocked, online);
        Assert.IsTrue(W());
        Assert.IsFalse(W(shown: false), "Devices list not shown");
        Assert.IsFalse(W(active: false), "app hidden or not in front");
        Assert.IsFalse(W(signedIn: false));
        Assert.IsFalse(W(connected: false));
        Assert.IsFalse(W(allowed: false), "plan or role does not allow connecting");
        Assert.IsFalse(W(blocked: true), "this computer is being helped / session limit");
        Assert.IsFalse(W(online: false), "no device online");
    }

    [TestMethod]
    public async Task Devices_list_with_an_online_device_asks_and_leaving_releases()
    {
        var (engine, app) = await Company();
        app.Section = AppSection.Devices;
        await app.Devices.UpdatePrewarm();
        Assert.AreEqual(true, LastOn(engine));
        app.Section = AppSection.Sessions;
        await app.Devices.UpdatePrewarm();
        Assert.AreEqual(false, LastOn(engine));
        var count = engine.CallsOf("remote_prewarm").Count;
        await app.Devices.UpdatePrewarm();
        Assert.AreEqual(count, engine.CallsOf("remote_prewarm").Count, "no repeated off");
    }

    [TestMethod]
    public async Task Hidden_app_releases_and_returns()
    {
        var (engine, app) = await Company();
        app.Section = AppSection.Devices;
        await app.Devices.UpdatePrewarm();
        Assert.AreEqual(true, LastOn(engine));
        app.AppActive = false;
        await app.Devices.UpdatePrewarm();
        Assert.AreEqual(false, LastOn(engine));
        app.AppActive = true;
        await app.Devices.UpdatePrewarm();
        Assert.AreEqual(true, LastOn(engine));
    }

    [TestMethod]
    public async Task A_role_without_device_access_never_asks()
    {
        var (engine, app) = await Company("member");
        app.Section = AppSection.Devices;
        await app.Devices.UpdatePrewarm();
        Assert.IsFalse(app.Devices.MayConnect);
        Assert.AreNotEqual(true, LastOn(engine));
    }
}
