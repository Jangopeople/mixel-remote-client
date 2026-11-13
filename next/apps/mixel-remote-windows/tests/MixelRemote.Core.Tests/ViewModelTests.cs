using System.Text.Json.Nodes;
using MixelRemote.Demo;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>The view models against the scripted engine: what each screen calls and shows.</summary>
[TestClass]
public sealed class ViewModelTests
{
    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    private static async Task<(MockEngine Engine, AppModel App)> Company(string role = "owner", string phase = "ready")
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine, phase, role);
        var app = await DemoFixtures.App(engine);
        return (engine, app);
    }

    [TestMethod]
    public async Task Signed_out_app_offers_sign_in_and_stays_offline()
    {
        var engine = new MockEngine();
        DemoFixtures.SignedOut(engine);
        var app = await DemoFixtures.App(engine);
        Assert.IsFalse(app.SignedIn);
        Assert.AreEqual("Sign in to get your connection ID.", app.ConnectionMessage);
        await app.Tick();
        Assert.AreEqual(0, engine.CallsOf("remote_register").Count, "nobody signed in: never goes online");
        Assert.AreEqual(0, app.Devices.Devices.Count);
    }

    [TestMethod]
    public async Task Sign_in_asks_the_engine_for_the_link_and_opens_the_browser()
    {
        var engine = new MockEngine();
        DemoFixtures.SignedOut(engine);
        engine.Reply("account_sign_in", "\"https://auth.mixel.ch/application/o/authorize/?x=1\"");
        var app = await DemoFixtures.App(engine);
        string? opened = null;
        app.OpenUrl = url => opened = url;
        await app.SignIn();
        Assert.AreEqual("https://auth.mixel.ch/application/o/authorize/?x=1", opened);
        Assert.AreEqual(false, engine.CallsOf("account_sign_in")[0].Object["open_browser"]!.GetValue<bool>());
        await app.SignIn(showLink: true);
        Assert.AreEqual("https://auth.mixel.ch/application/o/authorize/?x=1", app.SignInLink);
    }

    [TestMethod]
    public async Task Second_step_needs_six_digits_and_sends_the_factor()
    {
        var engine = new MockEngine();
        DemoFixtures.Mfa(engine);
        var app = await DemoFixtures.App(engine);
        Assert.AreEqual("mfa_required", app.Account.Phase);
        app.VerificationCode = "12 34";
        await app.Verify(app.Account.Factors[0].Id);
        Assert.AreEqual("Enter the 6-digit code", app.Error);
        Assert.AreEqual(0, engine.CallsOf("account_verify").Count);
        app.VerificationCode = "123 456";
        await app.Verify(app.Account.Factors[0].Id);
        var call = engine.CallsOf("account_verify").Single().Object;
        Assert.AreEqual("123456", call["code"]!.GetValue<string>());
        Assert.AreEqual("f0000000-0000-4000-8000-000000000001", call["factor"]!.GetValue<string>());
        Assert.AreEqual("", app.VerificationCode, "the code is not kept");
    }

    [TestMethod]
    public async Task Signed_in_company_owner_sees_plan_usage_members_and_invitations()
    {
        var (_, app) = await Company();
        Assert.IsTrue(app.SignedIn);
        Assert.AreEqual("Muster Treuhand AG", app.Company.Tenant?.Name);
        StringAssert.StartsWith(app.Company.PlanLine, "Business trial until");
        Assert.AreEqual("4 of 2,000 devices · 2 of 15 technicians", app.Company.UsageLine);
        Assert.AreEqual(2, app.Company.Members.Count);
        Assert.AreEqual(1, app.Company.Invites.Count);
        Assert.AreEqual("E-mail sent", CompanyModel.MailState(app.Company.Invites[0]));
        Assert.IsTrue(app.Company.Administers && app.Company.IsOwner && !app.Company.ReadOnly);
    }

    [TestMethod]
    public async Task Devices_show_presence_reasons_and_this_computer()
    {
        var (_, app) = await Company();
        var devices = app.Devices;
        Assert.AreEqual(4, devices.Devices.Count);
        var reception = devices.Devices.First(device => device.Name == "Reception PC");
        var warehouse = devices.Devices.First(device => device.Name == "Warehouse laptop");
        var thisPc = devices.Devices.First(device => device.ConnectionId == DemoFixtures.ThisComputer);
        Assert.AreEqual(Presence.Online, devices.PresenceOf(reception));
        Assert.AreEqual(Presence.Offline, devices.PresenceOf(warehouse));
        Assert.AreEqual(Presence.This, devices.PresenceOf(thisPc));
        StringAssert.Contains(devices.Reason(warehouse), "Last seen 3 hours ago");
        Assert.AreEqual("This device is offline.", devices.ConnectBlocked(warehouse));
        Assert.AreEqual("This is the computer you are using.", devices.ConnectBlocked(thisPc));
        Assert.AreEqual("", devices.ConnectBlocked(reception));
        Assert.IsTrue(devices.Manages && devices.Deploys && devices.GroupsOn && devices.RegisteredHere);
        Assert.AreEqual(1, devices.Groups.Count);
        Assert.AreEqual("Page 1 · 4 devices.", devices.Message);
    }

    [TestMethod]
    public async Task Members_see_no_manage_menu_and_cannot_deploy()
    {
        var (_, app) = await Company(role: "member");
        Assert.IsFalse(app.Devices.Manages);
        Assert.IsFalse(app.Devices.Deploys);
        Assert.AreEqual(EnrollmentPath.CompanyMember, app.Enrollment.Path);
        Assert.AreEqual(0, app.Company.Invites.Count, "only administrators read invitations");
    }

    [TestMethod]
    public async Task Manage_menu_renames_moves_and_removes_with_the_tenant()
    {
        var (engine, app) = await Company();
        var device = app.Devices.Devices[1];
        engine.Reply("saas_rename_device", "\"Accounting Mac 2\"");
        Assert.IsTrue(await app.Devices.Rename(device, "Accounting Mac 2"));
        var rename = engine.CallsOf("saas_rename_device").Single().Object;
        Assert.AreEqual(DemoFixtures.TenantId, rename["tenant"]!.GetValue<string>());
        Assert.AreEqual(device.DeviceId, rename["device"]!.GetValue<string>());
        Assert.IsTrue(await app.Devices.Move(device, "g1000000-0000-4000-8000-000000000001"));
        Assert.AreEqual("g1000000-0000-4000-8000-000000000001", engine.CallsOf("saas_move_device").Single().Object["group"]!.GetValue<string>());
        Assert.IsTrue(await app.Devices.Move(device, null));
        Assert.IsNull(engine.CallsOf("saas_move_device")[1].Object["group"]);
        engine.Fail("saas_remove_device", "Not allowed");
        Assert.IsFalse(await app.Devices.Remove(device));
        Assert.AreEqual("Not allowed", app.Devices.Error);
    }

    [TestMethod]
    public async Task Deploy_link_returns_the_commands_once()
    {
        var (engine, app) = await Company();
        engine.Reply("saas_create_deploy_link", """{"token":"tok","windows_command":"irm x | iex --signin-enroll","macos_command":"curl mac","linux_command":"curl linux"}""");
        Assert.IsTrue(await app.Devices.CreateDeployLink("Rollout", 30, 50, null));
        Assert.AreEqual("irm x | iex --signin-enroll", app.Devices.DeployLink?.WindowsCommand);
        var args = engine.CallsOf("saas_create_deploy_link").Single().Object;
        Assert.AreEqual(30, args["days"]!.GetValue<int>());
        Assert.AreEqual(50, args["computers"]!.GetValue<int>());
    }

    [TestMethod]
    public async Task Add_this_computer_goes_offline_enrols_remembers_and_comes_back_online()
    {
        var preferences = new MemoryPreferences();
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        engine.Reply("saas_add_this_device", """{"device_id":"d9","connection_id":555666777,"generation":4}""");
        var app = await DemoFixtures.App(engine, preferences);
        Assert.IsTrue(await app.Devices.AddThisComputer("ANNA-PC", null));
        await app.LastAction;
        var order = engine.Calls.Select(call => call.Command).Where(command => command is "remote_disconnect" or "saas_add_this_device" or "remote_register").ToList();
        CollectionAssert.AreEqual(new[] { "remote_disconnect", "saas_add_this_device", "remote_register" }, order);
        Assert.AreEqual(555666777u, app.Enrollment.Current?.ConnectionId);
        StringAssert.Contains(preferences.Get("mixel.remote.this-computer.v1"), "555666777");
    }

    [TestMethod]
    public async Task Missing_identity_shows_setup_instead_of_an_error()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine, phase: "offline");
        engine.Fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.");
        var app = await DemoFixtures.App(engine);
        Assert.IsTrue(app.Enrollment.NeedsSetup);
        Assert.AreEqual("", app.Error);
        Assert.AreEqual(EnrollmentPath.Company, app.Enrollment.Path);
        var before = engine.CallsOf("remote_register").Count;
        await app.Tick();
        Assert.AreEqual(before, engine.CallsOf("remote_register").Count, "no retry loop while setup is needed");
    }

    [TestMethod]
    public async Task Going_offline_stops_the_automatic_reconnect()
    {
        var (engine, app) = await Company(phase: "offline");
        Assert.IsTrue(engine.CallsOf("remote_register").Count >= 1, "a set-up computer goes online by itself");
        await app.GoOffline();
        var count = engine.CallsOf("remote_register").Count;
        await app.Tick();
        await app.Tick();
        Assert.AreEqual(count, engine.CallsOf("remote_register").Count);
        await app.GoOnline();
        Assert.AreEqual(count + 1, engine.CallsOf("remote_register").Count);
    }

    [TestMethod]
    public async Task Connect_claims_phones_first_then_requests_with_password()
    {
        var (engine, app) = await Company();
        await app.Connect(731_204_118, password: "4711", unattended: false);
        var request = engine.CallsOf("remote_request").Single().Object;
        Assert.AreEqual(731_204_118u, request["host"]!.GetValue<uint>());
        Assert.AreEqual("4711", request["password"]!.GetValue<string>());
        Assert.IsFalse(request["unattended"]!.GetValue<bool>());
        Assert.AreEqual(1, engine.CallsOf("quick_phone_claim").Count);
        Assert.AreEqual("Waiting for the other person to accept your request.", app.Progress);
    }

    [TestMethod]
    public async Task Connect_to_a_phone_never_asks_for_unattended_access()
    {
        var (engine, app) = await Company();
        engine.Reply("quick_phone_claim", "true");
        await app.Connect(731_204_118, unattended: true);
        Assert.IsFalse(engine.CallsOf("remote_request").Single().Object["unattended"]!.GetValue<bool>());
    }

    [TestMethod]
    public async Task Phone_claim_without_the_second_step_explains_what_to_do()
    {
        var (engine, app) = await Company();
        engine.Fail("quick_phone_claim", "42501 staff only");
        await app.Connect(648_493_658);
        StringAssert.StartsWith(app.Error, "Connecting to phones needs your Mixel team account");
        Assert.AreEqual(0, engine.CallsOf("remote_request").Count);
    }

    [TestMethod]
    public async Task Consent_preselects_the_only_screen_and_sends_exactly_what_was_allowed()
    {
        var engine = new MockEngine();
        DemoFixtures.Incoming(engine);
        var app = await DemoFixtures.App(engine);
        await app.Host.LoadSources();
        Assert.IsTrue(app.Host.Asking);
        Assert.IsTrue(app.Host.ConfirmDisplay && app.Host.AllowControl && app.Host.CanAccept);
        Assert.IsFalse(app.Host.AllowFiles || app.Host.AllowChat || app.Host.AllowClipboard || app.Host.AllowRecording, "nothing else before Accept");
        app.Host.AllowChat = true;
        await app.Host.Decide(accept: true);
        var decision = engine.CallsOf("remote_decide").Single().Object["decision"]!.AsObject();
        Assert.IsTrue(decision["accept"]!.GetValue<bool>());
        Assert.IsTrue(decision["control"]!.GetValue<bool>());
        Assert.IsTrue(decision["chat"]!.GetValue<bool>());
        Assert.IsFalse(decision["files"]!.GetValue<bool>());
        Assert.AreEqual(DemoFixtures.SessionId, decision["session"]!.GetValue<string>());
    }

    [TestMethod]
    public async Task Declining_grants_nothing()
    {
        var engine = new MockEngine();
        DemoFixtures.Incoming(engine);
        var app = await DemoFixtures.App(engine);
        await app.Host.LoadSources();
        app.Host.AllowFiles = true;
        await app.Host.Decide(accept: false);
        var decision = engine.CallsOf("remote_decide").Single().Object["decision"]!.AsObject();
        foreach (var grant in new[] { "accept", "control", "files", "chat", "clipboard", "recording" })
        {
            Assert.IsFalse(decision[grant]!.GetValue<bool>(), grant);
        }
    }

    [TestMethod]
    public async Task Session_reads_pictures_and_sends_input_only_while_controllable()
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        engine.QueueFrame(VideoFrame.Sample(64, 40, 1));
        engine.QueueFrame(VideoFrame.Sample(64, 40, 2));
        var app = new AppModel(engine);
        app.Session.IdleDelay = 1;
        await app.RefreshAccount();
        await app.RefreshRemote();
        for (var round = 0; round < 50 && app.Session.Frame?.Sequence != 2; round++) await Task.Delay(10);
        Assert.AreEqual(2ul, app.Session.Frame?.Sequence);
        Assert.IsTrue(app.Session.Controllable);
        app.Session.MoveTo(0.5, 1.2);
        app.Session.Press(KeyMap.Usage(0x2E, false)!.Value, KeyMap.Control);
        await app.Session.Flushing;
        var sent = engine.SentInput;
        Assert.AreEqual("""{"kind":"pointer","x":32768,"y":65535}""", sent[0]);
        Assert.AreEqual("""{"kind":"key","usage":224,"down":true}""", sent[1]);
        Assert.AreEqual("""{"kind":"key","usage":6,"down":true}""", sent[2]);
        Assert.AreEqual("""{"kind":"key","usage":224,"down":false}""", sent[^1]);
        app.Session.Clear();
    }

    [TestMethod]
    public async Task Toolbar_actions_travel_as_host_actions()
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        var app = await DemoFixtures.App(engine);
        app.Session.ShowSample(VideoFrame.Sample(64, 40, 1));
        app.Session.TogglePrivacy();
        app.Session.ToggleBlockInput();
        app.Session.ToggleLockWhenDone();
        app.Session.CtrlAltDel();
        app.Session.LockNow();
        app.Session.ToggleSound();
        await Task.Delay(20);
        var names = engine.SentInput.Select(json => JsonNode.Parse(json)!["name"]?.GetValue<string>()).ToList();
        CollectionAssert.AreEqual(new[] { "privacy:on", "block:on", "lockend:on", "sas", "lock", "sound:off" }, names);
        Assert.IsTrue(app.Session.Privacy && app.Session.BlockInput && app.Session.LockWhenDone && !app.Session.Sound);
        app.Session.Clear();
    }

    [TestMethod]
    public async Task Restart_needs_a_second_click()
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        var app = await DemoFixtures.App(engine);
        app.Session.RestartComputer();
        Assert.IsTrue(app.Session.RestartArmed);
        await Task.Delay(10);
        Assert.AreEqual(0, engine.SentInput.Count);
        app.Session.RestartComputer();
        await Task.Delay(10);
        Assert.AreEqual("""{"kind":"action","name":"restart"}""", engine.SentInput.Single());
        app.Session.Clear();
    }

    [TestMethod]
    public async Task End_session_uses_the_consent_and_stops_the_picture()
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        var app = await DemoFixtures.App(engine);
        app.Session.ShowSample(VideoFrame.Sample(64, 40, 1));
        await app.Session.End();
        Assert.AreEqual(DemoFixtures.ConsentId, engine.CallsOf("remote_stop_session").Single().Object["consent"]!.GetValue<string>());
        Assert.IsNull(app.Session.Frame);
        Assert.IsFalse(app.Session.Stopping);
    }

    [TestMethod]
    public async Task Chat_files_and_clipboard_use_the_session()
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        var app = await DemoFixtures.App(engine);
        await app.Session.ReadTools();
        Assert.AreEqual(2, app.Session.Chat.Count);
        Assert.AreEqual("driver-setup.exe", app.Session.Files?.Outgoing?.Name);
        Assert.AreEqual(31.0 / 48, app.Session.Files!.Outgoing!.Progress, 1e-9);
        app.Session.ChatDraft = "  Done?  ";
        await app.Session.SendChat();
        Assert.AreEqual("Done?", engine.CallsOf("remote_chat_send").Single().Object["text"]!.GetValue<string>());
        await app.Session.Clipboard(push: true);
        Assert.AreEqual("Remote clipboard updated.", app.Session.ClipboardResult);
        await app.Session.SendFile(@"C:\Temp\report.pdf");
        Assert.AreEqual(DemoFixtures.ConsentId, engine.CallsOf("remote_file_send").Single().Object["consent"]!.GetValue<string>());
        app.Session.Clear();
    }

    [TestMethod]
    public async Task Recording_asks_for_a_place_and_starts()
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        var app = await DemoFixtures.App(engine);
        app.ChooseSaveLocation = name => Task.FromResult<string?>(@"C:\Temp\" + name);
        await app.Session.Record();
        Assert.AreEqual(@"C:\Temp\Mixel Remote recording.mp4", engine.CallsOf("remote_record_start").Single().Object["path"]!.GetValue<string>());
        engine.Reply("remote_record_may_start", "false");
        await app.Session.Record();
        Assert.AreEqual("Start a permitted recording after the remote screen appears", app.Session.Error);
        app.Session.Clear();
    }

    [TestMethod]
    public async Task Hosted_session_shows_colleagues_and_join_requests()
    {
        var engine = new MockEngine();
        DemoFixtures.Hosted(engine);
        var app = await DemoFixtures.App(engine);
        Assert.IsTrue(app.Remote.Sharing);
        Assert.AreEqual(1, app.Remote.Colleagues.Count);
        await app.Host.DecideJoin(accept: true, control: false);
        var join = engine.CallsOf("remote_decide_join").Single().Object;
        Assert.AreEqual("c2", join["session"]!.GetValue<string>());
        Assert.IsFalse(join["control"]!.GetValue<bool>());
        await app.Host.RemoveColleague(app.Remote.Colleagues[0]);
        Assert.AreEqual("c1", engine.CallsOf("remote_remove_colleague").Single().Object["session"]!.GetValue<string>());
        Assert.IsTrue(app.Session.CanSwitchSides);
    }

    [TestMethod]
    public async Task Unattended_sharing_needs_a_confirmed_display_and_is_remembered()
    {
        var preferences = new MemoryPreferences();
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        var app = await DemoFixtures.App(engine, preferences);
        await app.Host.EnableUnattended();
        Assert.AreEqual("Choose a display and confirm it before enabling unattended sharing.", app.Host.Error);
        await app.Host.LoadUnattendedSources();
        app.Host.UnattendedConfirm = true;
        app.Host.UnattendedControl = true;
        await app.Host.EnableUnattended();
        var call = engine.CallsOf("remote_unattended").Single().Object;
        StringAssert.StartsWith(call["source"]!.GetValue<string>(), "dxgi:");
        Assert.IsTrue(call["control"]!.GetValue<bool>());
        StringAssert.Contains(preferences.Get("mixel.remote.unattended.v1"), "dxgi:");
        await app.Host.DisableUnattended();
        Assert.IsNull(preferences.Get("mixel.remote.unattended.v1"));
        Assert.IsNull(engine.CallsOf("remote_unattended")[1].Object["source"]);
    }

    [TestMethod]
    public async Task Company_invite_checks_the_address_and_sends_the_language()
    {
        var (engine, app) = await Company();
        Assert.IsFalse(await app.Company.Invite("no-address", "technician"));
        Assert.AreEqual("Enter an e-mail address", app.Company.Error);
        Translator.Shared.Choice = "de";
        engine.Reply("saas_invite_member", "\"inv-1\"");
        Assert.IsTrue(await app.Company.Invite(" lea@muster.test ", "admin"));
        var call = engine.CallsOf("saas_invite_member").Single().Object;
        Assert.AreEqual("lea@muster.test", call["email"]!.GetValue<string>());
        Assert.AreEqual("de", call["locale"]!.GetValue<string>());
        Translator.Shared.Choice = "en";
    }

    [TestMethod]
    public async Task Creating_a_company_needs_the_terms()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        engine.Reply("saas_my_tenants", "[]");
        engine.Reply("saas_signup", """{"tenant_id":"t1","plan":"business","status":"trial","trial_ends_at":null}""");
        var app = await DemoFixtures.App(engine);
        Assert.IsNull(app.Company.Tenant);
        Assert.IsFalse(await app.Company.Create("Muster AG", acceptedTerms: false));
        Assert.AreEqual(0, engine.CallsOf("saas_signup").Count);
        Assert.IsTrue(await app.Company.Create(" Muster AG ", acceptedTerms: true));
        var call = engine.CallsOf("saas_signup").Single().Object;
        Assert.AreEqual("Muster AG", call["company"]!.GetValue<string>());
        Assert.IsTrue(call["terms"]!.GetValue<bool>());
    }

    [TestMethod]
    public async Task Deleting_the_account_signs_out()
    {
        var (engine, app) = await Company();
        Assert.IsTrue(await app.Company.DeleteAccount("anna@muster.test"));
        await app.LastAction;
        Assert.AreEqual("anna@muster.test", engine.CallsOf("saas_delete_my_account").Single().Object["email"]!.GetValue<string>());
        Assert.AreEqual(1, engine.CallsOf("account_cancel").Count);
        Assert.IsFalse(engine.CallsOf("set_stay_signed_in").Single().Object["enabled"]!.GetValue<bool>());
    }

    [TestMethod]
    public async Task Removing_this_computer_signs_with_its_registration_and_forgets_it()
    {
        var preferences = new MemoryPreferences();
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        engine.Reply("saas_add_this_device", """{"device_id":"d9","connection_id":555666777,"generation":4}""");
        engine.Reply("device_self_remove", "\"removed\"");
        var app = await DemoFixtures.App(engine, preferences);
        await app.Devices.AddThisComputer("ANNA-PC", null);
        Assert.AreEqual(SelfRemovalStatus.Removed, await app.Enrollment.RemoveThisComputer());
        var registration = engine.CallsOf("device_self_remove").Single().Object["registration"]!.AsObject();
        Assert.AreEqual("d9", registration["device"]!.GetValue<string>());
        Assert.AreEqual(4ul, registration["generation"]!.GetValue<ulong>());
        Assert.IsNull(app.Enrollment.Current);
        Assert.IsTrue(app.Enrollment.NeedsSetup);
        Assert.IsNull(preferences.Get("mixel.remote.this-computer.v1"));
    }

    [TestMethod]
    public async Task Settings_read_printing_and_sign_in_screen_and_change_them()
    {
        var (engine, app) = await Company();
        Assert.IsTrue(app.Settings.Printing?.Supported);
        Assert.IsTrue(app.Settings.SignInScreen?.Supported);
        await app.Settings.SetPrinting(true);
        Assert.IsTrue(engine.CallsOf("remote_printing_set").Single().Object["enabled"]!.GetValue<bool>());
        await app.Settings.SetAutostart(true);
        CollectionAssert.AreEqual(new[] { "set_stay_signed_in", "set_autostart" },
            engine.Calls.Select(call => call.Command).Where(command => command is "set_stay_signed_in" or "set_autostart").ToList());
        engine.Reply("update_check", """{"version":"2.1.0","installable":true,"silent":false,"needs_password":false}""");
        await app.Settings.CheckForUpdate();
        Assert.AreEqual("2.1.0", app.Settings.Update?.Version);
        await app.Settings.CheckForUpdateQuietly();
        Assert.AreEqual(0, engine.CallsOf("update_install").Count, "not silent: waits for the person");
        engine.Reply("update_check", """{"version":"2.1.0","installable":true,"silent":true,"needs_password":false}""");
        app.Settings.AutoUpdate = false;
        await app.Settings.CheckForUpdateQuietly();
        Assert.AreEqual(0, engine.CallsOf("update_install").Count, "automatic updates turned off");
        app.Settings.AutoUpdate = true;
        var quit = false;
        app.Settings.QuitForUpdate = () => quit = true;
        await app.Settings.CheckForUpdateQuietly();
        Assert.AreEqual(1, engine.CallsOf("update_install").Count);
        Assert.IsTrue(quit);
    }

    [TestMethod]
    public async Task Language_choice_is_remembered_and_translates_screens()
    {
        var preferences = new MemoryPreferences();
        var engine = new MockEngine();
        DemoFixtures.SignedOut(engine);
        var app = await DemoFixtures.App(engine, preferences);
        app.Settings.SetLanguage("fr");
        Assert.AreEqual("fr", preferences.Get("mixel.remote.language.v1"));
        Assert.AreEqual("Appareils", T.L("Devices"));
        app.Settings.SetLanguage("auto");
        Assert.IsNull(preferences.Get("mixel.remote.language.v1"));
        Translator.Shared.Choice = "en";
    }

    [TestMethod]
    public async Task Engine_failures_are_shown_translated()
    {
        var (engine, app) = await Company();
        Translator.Shared.Choice = "de";
        engine.Fail("remote_register", "Go offline before adding this computer");
        await app.GoOnline();
        Assert.AreNotEqual("Go offline before adding this computer", app.Error);
        Assert.AreNotEqual("", app.Error);
        Translator.Shared.Choice = "en";
    }

    [TestMethod]
    public async Task Every_demo_screen_builds_its_model()
    {
        foreach (var screen in DemoFixtures.Screens)
        {
            var app = await DemoFixtures.Screen(screen);
            Assert.IsNotNull(app, screen);
            app.Session.Clear();
        }
    }
}
