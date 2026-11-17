using MixelRemote.Demo;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>The Devices console: views, filters, grouped table, admin badges, and the reports model.</summary>
[TestClass]
public sealed class ConsoleTests
{
    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    private static async Task<(MockEngine Engine, AppModel App)> Company()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        return (engine, await DemoFixtures.App(engine));
    }

    [TestMethod]
    public async Task The_table_groups_by_group_with_no_group_last_and_sorts_by_status()
    {
        var (_, app) = await Company();
        var groups = app.Devices.RowGroups;
        CollectionAssert.AreEqual(new[] { "Front office", "No group" }, groups.Select(group => group.Name).ToArray());
        Assert.AreEqual(1, groups[0].Devices.Count);
        // Online (Accounting Mac) before this computer before offline (Warehouse laptop).
        CollectionAssert.AreEqual(new[] { "Accounting Mac", "This PC", "Warehouse laptop" }, groups[1].Devices.Select(device => device.Name).ToArray());
        Assert.AreEqual(2, groups[1].Online);
    }

    [TestMethod]
    public async Task Views_show_favourites_recent_and_groups()
    {
        var (_, app) = await Company();
        var devices = app.Devices;
        CollectionAssert.AreEqual(new[] { "Accounting Mac" }, devices.DevicesIn(DeviceView.Favorites).Select(device => device.Name).ToArray());
        CollectionAssert.AreEqual(new[] { "Reception PC", "Accounting Mac" }, devices.DevicesIn(DeviceView.Recent).Select(device => device.Name).ToArray());
        CollectionAssert.AreEqual(new[] { "Reception PC" }, devices.DevicesIn(DeviceView.InGroup("Front office")).Select(device => device.Name).ToArray());
        CollectionAssert.AreEqual(new[] { "Front office" }, devices.GroupNames.ToArray());
        devices.View = DeviceView.InGroup("Front office");
        Assert.AreEqual(1, devices.Filtered.Count);
        Assert.AreEqual("All devices", DeviceView.All.Kind == DeviceViewKind.All ? "All devices" : "");
    }

    [TestMethod]
    public async Task Search_filters_name_id_group_and_note_at_once()
    {
        var (_, app) = await Company();
        var devices = app.Devices;
        devices.FilterText = "acc";
        CollectionAssert.AreEqual(new[] { "Accounting Mac" }, devices.Filtered.Select(device => device.Name).ToArray());
        devices.FilterText = "731 204 118";
        CollectionAssert.AreEqual(new[] { "Reception PC" }, devices.Filtered.Select(device => device.Name).ToArray());
        devices.FilterText = "front";
        Assert.AreEqual(1, devices.Filtered.Count, "group name");
        devices.FilterText = "docked";
        CollectionAssert.AreEqual(new[] { "Reception PC" }, devices.Filtered.Select(device => device.Name).ToArray(), "note");
        devices.FilterText = "";
        devices.ToggleSystem("linux");
        CollectionAssert.AreEqual(new[] { "Warehouse laptop" }, devices.Filtered.Select(device => device.Name).ToArray());
        devices.ToggleSystem("linux");
        devices.OnlineOnly = true;
        Assert.IsFalse(devices.Filtered.Any(device => device.Name == "Warehouse laptop"));
    }

    [TestMethod]
    public async Task Admin_data_gives_the_unattended_and_update_badges_and_versions()
    {
        var (_, app) = await Company();
        var devices = app.Devices;
        Assert.IsTrue(devices.AdminAvailable);
        var reception = devices.AllDevices.Single(device => device.Name == "Reception PC");
        var laptop = devices.AllDevices.Single(device => device.Name == "Warehouse laptop");
        var mac = devices.AllDevices.Single(device => device.Name == "Accounting Mac");
        Assert.IsTrue(devices.AdminOf(reception)!.Unattended);
        Assert.IsFalse(devices.AdminOf(laptop)!.Unattended, "off wins");
        Assert.IsFalse(devices.AdminOf(mac)!.Unattended, "default without company default");
        Assert.IsTrue(devices.UpdateAvailable(laptop));
        Assert.IsFalse(devices.UpdateAvailable(reception));
        Assert.AreEqual("2.0.15", devices.VersionOf(laptop));
        Assert.AreEqual(2, devices.UnattendedCount);
        devices.UnattendedOnly = true;
        Assert.AreEqual(2, devices.Filtered.Count);
        devices.UnattendedOnly = false;
        devices.UpdateOnly = true;
        CollectionAssert.AreEqual(new[] { "Warehouse laptop" }, devices.Filtered.Select(device => device.Name).ToArray());
    }

    [TestMethod]
    public async Task Without_engine_support_the_list_has_no_badges_and_no_error()
    {
        var engine = new MockEngine();
        DemoFixtures.Company(engine);
        engine.Fail("remote_admin_devices", "unknown command remote_admin_devices");
        var app = await DemoFixtures.App(engine);
        Assert.IsFalse(app.Devices.AdminAvailable);
        Assert.AreEqual("", app.Devices.Error);
        Assert.AreEqual("—", app.Devices.VersionOf(app.Devices.AllDevices[0]));
        Assert.IsFalse(app.Devices.UpdateAvailable(app.Devices.AllDevices[0]));
    }

    [TestMethod]
    public async Task The_note_and_unattended_setting_go_through_the_admin_commands()
    {
        var (engine, app) = await Company();
        var device = app.Devices.AllDevices.Single(item => item.Name == "Reception PC");
        engine.Reply("remote_admin_set_device_note", "{}");
        engine.Reply("remote_admin_set_device_unattended", "null");
        Assert.IsTrue(await app.Devices.SetNote(device, "  Desk 4  "));
        var note = engine.CallsOf("remote_admin_set_device_note").Single().Object;
        Assert.AreEqual("Desk 4", note["note"]!.GetValue<string>());
        Assert.AreEqual("d1000000-0000-4000-8000-000000000001", note["device"]!.GetValue<string>());
        Assert.IsTrue(await app.Devices.SetUnattended(device, "off"));
        Assert.AreEqual("off", engine.CallsOf("remote_admin_set_device_unattended").Single().Object["setting"]!.GetValue<string>());
        engine.Fail("remote_admin_set_device_unattended", "HTTP 403 not_admin");
        Assert.IsFalse(await app.Devices.SetUnattended(device, "on"));
        Assert.AreEqual("Only an administrator of the company can change devices.", app.Devices.Error);
    }

    [TestMethod]
    public async Task The_engines_typed_failure_text_gives_the_right_sentence()
    {
        var (engine, app) = await Company();
        var device = app.Devices.AllDevices.Single(item => item.Name == "Reception PC");
        // The engine's failure text of a refused remote_admin_* call: {"hint", "message", "status"}.
        engine.Fail("remote_admin_remove_device", """{"status":400,"hint":"confirm_mismatch","message":"The ID does not match"}""");
        Assert.IsFalse(await app.Devices.RemoveConfirmed(device, device.ConnectionId.ToString()));
        Assert.AreEqual("The ID does not match the device.", app.Devices.Error);
        engine.Fail("remote_admin_set_device_unattended", """{"status":403,"hint":"mfa_required","message":"Second step needed"}""");
        Assert.IsFalse(await app.Devices.SetUnattended(device, "off"));
        Assert.AreEqual("Sign in again with the second confirmation step to change devices.", app.Devices.Error);
        engine.Fail("remote_admin_set_device_note", """{"status":409,"hint":"changed","message":"The device changed"}""");
        Assert.IsFalse(await app.Devices.SetNote(device, "x"));
        Assert.AreEqual("The device was changed in the meantime. The list is read again.", app.Devices.Error);
    }

    [TestMethod]
    public async Task Reports_load_the_period_and_export_csv_with_formula_protection()
    {
        var (engine, app) = await Company();
        var reports = app.Reports;
        reports.From = new DateOnly(2026, 11, 1);
        reports.To = new DateOnly(2026, 11, 17);
        await reports.Load();
        var call = engine.CallsOf("saas_session_report").Last().Object;
        Assert.AreEqual("2026-11-01", call["from"]!.GetValue<string>());
        Assert.AreEqual("2026-11-18", call["to"]!.GetValue<string>(), "the end day is included");
        Assert.AreEqual(2, reports.Rows.Count);
        Assert.AreEqual("19:00", ReportsModel.Duration(1140));
        Assert.AreEqual("1:02:03", ReportsModel.Duration(3723));
        var csv = reports.Csv();
        Assert.IsTrue(csv.StartsWith('﻿'));
        StringAssert.Contains(csv, "Reception PC");
        StringAssert.Contains(csv, "\"Printer driver reinstalled, test page OK.\"");
        Assert.IsTrue(csv.EndsWith("\r\n", StringComparison.Ordinal));
        Assert.AreEqual("sessions-muster-2026-11-01-2026-11-17", reports.FileStem);
        Assert.IsTrue(reports.CanEditNote(reports.Rows[0]), "owner edits any note");
    }

    [TestMethod]
    public async Task Reports_need_a_plan_and_the_pdf_is_well_formed()
    {
        var (engine, app) = await Company();
        await app.Reports.Load();
        var pdf = app.Reports.Pdf();
        var text = System.Text.Encoding.Latin1.GetString(pdf);
        StringAssert.StartsWith(text, "%PDF-1.4");
        StringAssert.Contains(text, "%%EOF");
        StringAssert.Contains(text, "(Reception PC)");
        var many = Enumerable.Range(0, 80).Select(index => new[] { "a", "b", "c", "d", "e (x)", "f", "g", "h" + index }).ToList();
        var paged = System.Text.Encoding.Latin1.GetString(ReportPdf.Build("T", "S", ReportsModel.Columns, many));
        StringAssert.Contains(paged, "/Count 3");
        StringAssert.Contains(paged, @"e \(x\)");
        engine.Fail("saas_session_report", "session reports need a paid plan");
        await app.Reports.Load();
        Assert.IsTrue(app.Reports.NeedsPlan);
        Assert.AreEqual(0, app.Reports.Rows.Count);
    }
}

[TestClass]
public sealed class PointerPolicyTests
{
    [TestMethod]
    public void Exactly_one_pointer_is_shown_whatever_the_host_draws()
    {
        foreach (var pointerMode in new[] { false, true })
        {
            foreach (var hostDraws in new[] { false, true })
            {
                Assert.AreEqual(1, PointerPolicy.PointersShown(pointerMode, hostDraws), $"pointerMode={pointerMode} hostDraws={hostDraws}");
            }
        }
    }

    [TestMethod]
    public void The_local_cursor_is_hidden_only_when_the_host_draws_its_own()
    {
        Assert.AreEqual(PointerPolicy.LocalCursor.Arrow, PointerPolicy.For(false, false));
        Assert.AreEqual(PointerPolicy.LocalCursor.Cross, PointerPolicy.For(true, false));
        Assert.AreEqual(PointerPolicy.LocalCursor.Hidden, PointerPolicy.For(false, true));
    }
}

[TestClass]
public sealed class AppearanceTests
{
    [TestMethod]
    public void Appearance_is_system_by_default_and_remembered()
    {
        var preferences = new MemoryPreferences();
        var first = new AppModel(new MockEngine(), preferences).Settings;
        Assert.AreEqual("system", first.Appearance);
        first.Appearance = "dark";
        Assert.AreEqual("dark", new AppModel(new MockEngine(), preferences).Settings.Appearance);
        first.Appearance = "nonsense";
        Assert.AreEqual("system", new AppModel(new MockEngine(), preferences).Settings.Appearance);
    }
}

[TestClass]
public sealed class PasswordSessionTests
{
    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    private static async Task<AppModel> Viewing(DateTimeOffset? ends)
    {
        var engine = new MockEngine();
        DemoFixtures.Viewing(engine);
        engine.ReplyValue("remote_status", new RemoteStatus
        {
            Phase = "active", Host = DemoFixtures.ThisComputer, Session = DemoFixtures.SessionId, Peer = 731_204_118, Control = true,
            PasswordSession = ends is null ? null : new PasswordSession { EndsAt = ends.Value.ToString("o") },
        });
        var app = new AppModel(engine);
        await app.RefreshAccount();
        await app.RefreshRemote();
        return app;
    }

    [TestMethod]
    public async Task The_warning_comes_at_five_minutes_and_one_minute()
    {
        var now = DateTimeOffset.UtcNow;
        var app = await Viewing(now.AddMinutes(4).AddSeconds(30));
        StringAssert.Contains(app.Session.PasswordWarning(now), "ends in 5 minutes");
        StringAssert.Contains(app.Session.PasswordWarning(now.AddMinutes(3).AddSeconds(45)), "ends in 1 minute");
        Assert.AreEqual("", app.Session.PasswordWarning(now.AddMinutes(-10)), "10 minutes before: nothing yet");
        Assert.AreEqual("", app.Session.PasswordWarning(now.AddMinutes(10)), "after the end: nothing");
        app.Session.Clear();
    }

    [TestMethod]
    public async Task Without_a_password_session_there_is_no_warning()
    {
        var app = await Viewing(null);
        Assert.AreEqual("", app.Session.PasswordWarning());
        app.Session.Clear();
    }
}
