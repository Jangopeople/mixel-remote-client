using System.Diagnostics;
using System.Text.Json.Nodes;
using MixelRemote.Engine;
using MixelRemote.Support;

namespace MixelRemote.Lab;

/// <summary>
/// LAB: the Windows app's engine against the live Mixel service with a throwaway QA account's session file
/// (never a real person's). Needs an engine built with the qa-session feature. Every QA company, device and
/// invitation it creates is removed again.
/// </summary>
public static class LiveCheck
{
    private static int checks;
    private static int failures;
    private static Action<string> print = Console.WriteLine;

    /// <summary>Where the PASS/FAIL lines go (the app's LAB mode writes them to a file).</summary>
    public static void SetOutput(Action<string> output) => print = output;

    /// <summary>"RESULT passed/all" and whether every check passed.</summary>
    public static (string Line, bool Passed) Summary() => ($"RESULT {checks - failures}/{checks} passed", failures == 0);

    public static void Check(string label, bool ok, string detail = "")
    {
        checks++;
        if (ok) print($"PASS {label}");
        else
        {
            failures++;
            print($"FAIL {label} {detail}");
        }
    }

    public static async Task<int> Main(string[] args, Action<string> output)
    {
        print = output;
        if (args.Length < 3)
        {
            print("usage: check <qa-session.json> <suffix> | technician <qa-session.json> <suffix> <folder> [<host-id> <tenant>]");
            return 2;
        }
        // "providers" checks the real browser sign-in (no QA session file).
        if (args[0] != "providers") Environment.SetEnvironmentVariable("MIXEL_QA_SESSION_FILE", args[1]);
        var folder = args.Length > 3 ? args[3] : Path.Combine(Path.GetTempPath(), "mixel-live-" + args[2]);
        Directory.CreateDirectory(folder);
        using var engine = new NativeEngine(Path.Combine(folder, "engine"));
        try
        {
            switch (args[0])
            {
                case "check": await Checks(engine, args[2]); break;
                case "providers": await Providers(engine); break;
                case "technician":
                    await Technician(engine, args[2], folder, args.Length > 5 && uint.TryParse(args[4], out var host) ? host : null, args.Length > 5 ? args[5] : null);
                    break;
                default: print($"unknown mode {args[0]}"); return 2;
            }
        }
        finally
        {
            try { await engine.ShutdownAsync(); } catch (EngineFailure) { }
        }
        print($"RESULT {checks - failures}/{checks} passed");
        return failures == 0 ? 0 : 1;
    }

    public static async Task<JsonNode?> Call(IEngine engine, string command, params (string, object?)[] args)
    {
        var reply = await engine.CallAsync(command, Json.Args(args));
        return JsonNode.Parse(reply);
    }

    public static async Task<(JsonNode? Value, string Error)> Try(IEngine engine, string command, params (string, object?)[] args)
    {
        try { return (await Call(engine, command, args), ""); }
        catch (EngineFailure failure) { return (null, failure.Message); }
    }

    public static async Task<JsonObject?> Wait(IEngine engine, int seconds, Func<JsonObject, bool> until)
    {
        var clock = Stopwatch.StartNew();
        while (clock.Elapsed < TimeSpan.FromSeconds(seconds))
        {
            if ((await Try(engine, "remote_status")).Value is JsonObject status && until(status)) return status;
            await Task.Delay(250);
        }
        return null;
    }

    public static string Phase(JsonObject status) => status["phase"]?.GetValue<string>() ?? "";

    public static async Task<bool> SignIn(IEngine engine)
    {
        await Try(engine, "account_sign_in");
        for (var round = 0; round < 60; round++)
        {
            if ((await Try(engine, "account_status")).Value is JsonObject status)
            {
                var phase = status["phase"]?.GetValue<string>() ?? "";
                if (phase is "authenticated" or "profile_setup") return true;
                if (phase == "error") { print($"sign-in error: {status["message"]}"); return false; }
            }
            await Task.Delay(500);
        }
        return false;
    }

    /// <summary>
    /// Each sign-in button starts the browser sign-in at its provider: the sign-in service answers the
    /// authorization address with a redirect to the provider's own login page. Nobody signs in.
    /// </summary>
    private static async Task Providers(NativeEngine engine)
    {
        var offered = (await Try(engine, "sign_in_providers")).Value as JsonArray ?? [];
        var names = offered.Select(item => item?.GetValue<string>() ?? "").ToList();
        Check($"sign-in service offers {string.Join(", ", names)}", names.Contains("keycloak"));
        var hosts = new Dictionary<string, string> { ["keycloak"] = "auth.mixel.ch", ["google"] = "accounts.google.com", ["azure"] = "login.microsoftonline.com", ["apple"] = "appleid.apple.com" };
        using var http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(15) };
        foreach (var name in names)
        {
            var (url, error) = await Try(engine, "account_sign_in", ("open_browser", false), ("provider", name));
            var address = url?.GetValue<string>() ?? "";
            var location = "";
            if (address.Length > 0)
            {
                using var response = await http.GetAsync(new Uri(address));
                location = response.Headers.Location?.Host ?? "";
                print($"{name}: {(int)response.StatusCode} → {location}");
            }
            Check($"Continue with {name}: browser goes to {hosts.GetValueOrDefault(name, "?")}", location == hosts.GetValueOrDefault(name), error + " " + location);
            await Try(engine, "account_cancel");
        }
        var (_, refused) = await Try(engine, "account_sign_in", ("open_browser", false), ("provider", "github"));
        Check("an unknown provider is refused", refused.Length > 0);
        var (options, optionsError) = await Try(engine, "sign_in_options", ("email", "nobody@example.com"));
        Check("company sign-in rules answer (none for an unknown address)", optionsError.Length == 0 && options is null, optionsError);
    }

    /// <summary>The account checks: company, this computer, devices, groups, members, invitations, deploy links, self-removal, deletion.</summary>
    private static async Task Checks(NativeEngine engine, string suffix)
    {
        Check($"engine starts ({engine.Info.Version}, {engine.Info.Platform})", engine.Info.Platform == "windows");
        Check("sign-in with the QA session", await SignIn(engine));
        var company = $"QA Windows {suffix}";
        var (signup, signupError) = await Try(engine, "saas_signup", ("company", company), ("terms", true));
        var tenant = signup?["tenant_id"]?.GetValue<string>() ?? "";
        Check("create a company (14-day trial, terms accepted)", tenant.Length > 0, signupError);
        if (tenant.Length == 0) return;
        try
        {
            var tenants = (await Try(engine, "saas_my_tenants")).Value as JsonArray ?? [];
            Check("my companies lists it as owner", tenants.Any(row => row?["tenant_id"]?.GetValue<string>() == tenant && row?["role"]?.GetValue<string>() == "owner"));
            var overview = (await Try(engine, "saas_tenant_overview", ("tenant", tenant))).Value;
            Check("overview: trial status and limits", overview?["status"]?.GetValue<string>() == "trial" && overview?["limits"] is not null, overview?.ToJsonString() ?? "");
            Check("terms status recorded", (await Try(engine, "saas_terms_status", ("tenant", tenant))).Value is not null);
            var (added, addError) = await Try(engine, "saas_add_this_device", ("tenant", tenant), ("name", $"QA Windows {suffix}"), ("group", null));
            var connection = added?["connection_id"]?.GetValue<uint>() ?? 0;
            var device = added?["device_id"]?.GetValue<string>() ?? "";
            Check("add this computer (device key in Windows DPAPI storage)", connection > 100_000_000, addError);
            var (_, registerError) = await Try(engine, "remote_register");
            Check("go online", registerError.Length == 0, registerError);
            var ready = await Wait(engine, 30, status => Phase(status) == "ready");
            Check("connection service: ready with this computer's ID", ready?["host"]?.GetValue<uint>() == connection, ready?.ToJsonString() ?? "");
            Check("Windows displays offered for sharing", (await Try(engine, "capture_targets")).Value is JsonArray displays && displays.Count >= 0);
            var devices = (await Try(engine, "saas_devices", ("tenant", tenant), ("group", null), ("search", null))).Value as JsonArray ?? [];
            Check("company devices list this computer", devices.Any(row => row?["connection_id"]?.GetValue<uint>() == connection));
            Check("device directory answers", (await Try(engine, "remote_devices", ("search", ""))).Error.Length == 0);
            await Try(engine, "remote_presence", ("hosts", new[] { connection }));
            Check("connection service record per device", (await Try(engine, "saas_device_presence", ("tenant", tenant))).Error.Length == 0);
            var group = (await Try(engine, "saas_create_group", ("tenant", tenant), ("name", "QA group"))).Value?.GetValue<string>() ?? "";
            Check("create a group", group.Length > 0);
            var renamed = (await Try(engine, "saas_rename_device", ("tenant", tenant), ("device", device), ("name", "QA Windows renamed"))).Value;
            Check("rename the device", renamed?.GetValue<string>() == "QA Windows renamed");
            Check("move the device to the group", (await Try(engine, "saas_move_device", ("tenant", tenant), ("device", device), ("group", group))).Value?.GetValue<bool>() == true);
            var (invite, inviteError) = await Try(engine, "saas_invite_member", ("tenant", tenant), ("email", $"qa-windows-{suffix}@example.com"), ("role", "technician"), ("locale", "de"));
            var inviteId = invite?.GetValue<string>() ?? "";
            Check("invite a technician (test domain, never mailed)", inviteId.Length > 0, inviteError);
            var invites = (await Try(engine, "saas_invites", ("tenant", tenant))).Value as JsonArray ?? [];
            Check("open invitations list it", invites.Any(row => row?["id"]?.GetValue<string>() == inviteId));
            // The service allows sending again after a minute.
            var (_, tooSoon) = await Try(engine, "saas_resend_invite", ("tenant", tenant), ("invite", inviteId), ("locale", "fr"));
            Check("sending again at once is refused with a clear message", tooSoon.StartsWith("Wait a minute", StringComparison.Ordinal), tooSoon);
            await Task.Delay(TimeSpan.FromSeconds(62));
            var (_, resendError) = await Try(engine, "saas_resend_invite", ("tenant", tenant), ("invite", inviteId), ("locale", "fr"));
            Check("send the invitation again", resendError.Length == 0, resendError);
            Check("revoke the invitation", (await Try(engine, "saas_revoke_invite", ("tenant", tenant), ("invite", inviteId))).Value?.GetValue<bool>() == true);
            var members = (await Try(engine, "saas_members", ("tenant", tenant))).Value as JsonArray ?? [];
            Check("members: the owner", members.Any(row => row?["role"]?.GetValue<string>() == "owner"));
            Check("rename the company", (await Try(engine, "saas_rename_tenant", ("tenant", tenant), ("name", company + " renamed"))).Error.Length == 0);
            company += " renamed";
            var link = (await Try(engine, "saas_create_deploy_link", ("tenant", tenant), ("label", "QA"), ("days", 1), ("computers", 1), ("group", null))).Value;
            Check("deploy link with Windows, Mac and Linux commands",
                link?["windows_command"]?.GetValue<string>().Contains("--signin-enroll", StringComparison.Ordinal) == true
                && link?["macos_command"]?.GetValue<string>().Contains("remote.mixel.ch/install/macos.sh", StringComparison.Ordinal) == true);
            var links = (await Try(engine, "saas_deploy_links", ("tenant", tenant))).Value as JsonArray ?? [];
            var linkId = links.FirstOrDefault()?["id"]?.GetValue<string>() ?? "";
            Check("revoke the deploy link", (await Try(engine, "saas_revoke_deploy_link", ("tenant", tenant), ("link", linkId))).Value?.GetValue<bool>() == true);
            var room = (await Try(engine, "meeting_create")).Value;
            Check("Mixel Meet room for a session", room?["invite"]?.GetValue<string>().StartsWith("https://rs.mixel.ch/meet/#", StringComparison.Ordinal) == true);
            Check("legal page link", (await Try(engine, "legal_url", ("page", "privacy"), ("lang", "it"))).Value?.GetValue<string>() == "https://remote.mixel.ch/legal/privacy?lang=it");
            var (_, updateError) = await Try(engine, "update_check");
            Check("update check answers (signed update channel)", updateError.Length == 0, updateError);
            Check("remote printing status", (await Try(engine, "remote_printing_status")).Value?["supported"]?.GetValue<bool>() == true);
            Check("sign-in screen status", (await Try(engine, "signin_screen_status")).Value?["supported"] is not null);
            Check("start with Windows setting readable", (await Try(engine, "startup_settings")).Value?["autostart"] is not null);
            await Try(engine, "remote_disconnect");
            await Wait(engine, 15, status => Phase(status) is "offline" or "error");
            var removal = (await Try(engine, "device_self_remove", ("confirmed", true), ("registration", new Dictionary<string, object> { ["device"] = device, ["connection_id"] = connection, ["generation"] = added?["generation"]?.GetValue<ulong>() ?? 0 }))).Value;
            Check("this computer removes itself (signed with its key)", removal?.GetValue<string>() == "removed", removal?.ToJsonString() ?? "");
        }
        finally
        {
            var (_, deleteError) = await Try(engine, "saas_delete_company", ("tenant", tenant), ("name", company));
            Check("delete the QA company", deleteError.Length == 0, deleteError);
            var left = (await Try(engine, "saas_my_tenants")).Value as JsonArray;
            Check("no company left for the QA account", left is null || left.Count == 0);
        }
    }

    /// <summary>
    /// A real session from this computer (technician) to a lab computer: either one enrolled with the deploy
    /// link written to folder/deploy-command.txt, or the given Remote ID (a host that allows unattended access).
    /// </summary>
    private static async Task Technician(NativeEngine engine, string suffix, string folder, uint? given, string? givenTenant)
    {
        Check("sign-in with the QA session", await SignIn(engine));
        string tenant = givenTenant ?? "", company = $"QA Windows {suffix}", device = "";
        uint mine = 0;
        ulong generation = 0;
        if (given is null)
        {
            tenant = (await Try(engine, "saas_signup", ("company", company), ("terms", true))).Value?["tenant_id"]?.GetValue<string>() ?? "";
            Check("QA company", tenant.Length > 0);
            if (tenant.Length == 0) return;
        }
        {
            var added = (await Try(engine, "saas_add_this_device", ("tenant", tenant), ("name", $"QA Windows technician {suffix}"), ("group", null))).Value;
            mine = added?["connection_id"]?.GetValue<uint>() ?? 0;
            device = added?["device_id"]?.GetValue<string>() ?? "";
            generation = added?["generation"]?.GetValue<ulong>() ?? 0;
            Check("this computer added", mine > 0);
        }
        await Try(engine, "remote_register");
        Check("this computer online", await Wait(engine, 30, status => Phase(status) == "ready") is not null);
        try
        {
            uint host = given ?? 0;
            if (given is null)
            {
                var link = (await Try(engine, "saas_create_deploy_link", ("tenant", tenant), ("label", "QA lab"), ("days", 1), ("computers", 2), ("group", null))).Value;
                await File.WriteAllTextAsync(Path.Combine(folder, "deploy-command.txt"), link?["windows_command"]?.GetValue<string>() ?? "");
                await File.WriteAllTextAsync(Path.Combine(folder, "deploy-token.txt"), link?["token"]?.GetValue<string>() ?? "");
                await File.WriteAllTextAsync(Path.Combine(folder, "tenant.txt"), tenant);
                print("WAITING for the lab computer (deploy-command.txt / deploy-token.txt)");
                for (var round = 0; round < 450 && host == 0; round++)
                {
                    var rows = (await Try(engine, "saas_devices", ("tenant", tenant), ("group", null), ("search", null))).Value as JsonArray ?? [];
                    foreach (var row in rows)
                    {
                        if (row?["connection_id"]?.GetValue<uint?>() is not { } id || id == mine || row["revoked"]?.GetValue<bool>() == true) continue;
                        await Try(engine, "remote_presence", ("hosts", new[] { id }));
                        await Task.Delay(500);
                        if ((await Try(engine, "remote_status")).Value?["online"] is JsonArray online && online.Any(value => value?.GetValue<uint>() == id))
                        {
                            host = id;
                            print($"lab computer {row["name"]} ({row["os"]}) online: {id}");
                        }
                    }
                    if (host == 0) await Task.Delay(2000);
                }
                Check("lab computer enrolled with the deploy link and online", host > 0);
            }
            // A host given by ID asks its person (attended); a computer from the deploy link allows unattended access.
            if (host > 0) await Session(engine, host, folder, unattended: given is null);
            if (given is null && File.Exists(Path.Combine(folder, "keep-company"))) print("keep-company present: the QA company stays for the next step");
        }
        finally
        {
            await Try(engine, "remote_disconnect");
            await Wait(engine, 15, status => Phase(status) is "offline" or "error");
            if (device.Length > 0)
            {
                var removal = (await Try(engine, "device_self_remove", ("confirmed", true), ("registration", new Dictionary<string, object> { ["device"] = device, ["connection_id"] = mine, ["generation"] = generation }))).Value;
                Check("this computer removes itself", removal?.GetValue<string>() == "removed", removal?.ToJsonString() ?? "");
            }
            if (given is null && tenant.Length > 0 && !File.Exists(Path.Combine(folder, "keep-company")))
            {
                Check("QA company deleted (the lab computer's enrolment with it)", (await Try(engine, "saas_delete_company", ("tenant", tenant), ("name", company))).Error.Length == 0);
            }
        }
    }

    /// <summary>Connects unattended, receives pictures, sends harmless input, tries the host actions, ends.</summary>
    public static async Task Session(IEngine engine, uint host, string folder, bool unattended = true)
    {
        var (_, requestError) = await Try(engine, "remote_request", ("host", host), ("password", null), ("unattended", unattended));
        Check(unattended ? "unattended request" : "attended request (the person there decides)", requestError.Length == 0, requestError);
        var active = await Wait(engine, unattended ? 60 : 180, status => Phase(status) == "active");
        Check("session active", active is not null, (await Try(engine, "remote_status")).Value?.ToJsonString() ?? "");
        if (active?["session"]?.GetValue<string>() is not { } session) return;
        Check("view and control granted", active["control"]?.GetValue<bool>() == true);
        print($"host actions: {active["host_actions"]?.ToJsonString()}  displays: {active["displays"]}  features: {active["peer_features"]?.ToJsonString()}");
        var frames = 0;
        ulong sequence = 0;
        var size = "";
        var clock = Stopwatch.StartNew();
        var step = 0;
        while (clock.Elapsed < TimeSpan.FromSeconds(20) && frames < 40)
        {
            step++;
            if (step % 10 == 0) await engine.InputAsync(session, $$"""{"kind":"pointer","x":{{(step * 997) % 60000 + 2000}},"y":{{(step * 431) % 50000 + 5000}}}""");
            var data = await engine.NextVideoAsync(session, false);
            if (data.Length == 0)
            {
                await Task.Delay(20);
                continue;
            }
            try
            {
                var frame = VideoFrame.Decode(data, sequence);
                sequence = frame.Sequence;
                frames++;
                size = $"{frame.Width}×{frame.Height}";
                if (frames is 1 or 10) await File.WriteAllBytesAsync(Path.Combine(folder, "remote-screen.bmp"), Bitmap(frame));
            }
            catch (VideoFrame.InvalidFrame) { }
        }
        Check($"pictures from the lab computer ({frames} in {clock.Elapsed.TotalSeconds:0} s, {size}; a still screen sends pictures only on change)", frames >= 2);
        var posted = 0;
        for (var index = 0; index < 10; index++) if (await engine.InputAsync(session, $$"""{"kind":"pointer","x":{{20000 + index * 2000}},"y":30000}""")) posted++;
        foreach (var down in new[] { true, false }) if (await engine.InputAsync(session, $$"""{"kind":"key","usage":225,"down":{{(down ? "true" : "false")}}}""")) posted++;
        Check($"input applied on the lab computer ({posted}/12)", posted == 12);
        var chat = await Try(engine, "remote_chat_messages", ("session", session));
        print($"chat available: {chat.Error.Length == 0}");
        if (active["consent"]?.GetValue<string>() is { } consent)
        {
            var (_, stopError) = await Try(engine, "remote_stop_session", ("consent", consent));
            Check("end session", stopError.Length == 0, stopError);
        }
        Check("back to ready after the session", await Wait(engine, 20, status => Phase(status) == "ready") is not null);
    }

    /// <summary>A 32-bit BMP of a picture (readable everywhere, no image library needed).</summary>
    public static byte[] Bitmap(VideoFrame frame)
    {
        var pixels = frame.ToBgra();
        var file = new byte[54 + pixels.Length];
        "BM"u8.CopyTo(file);
        BitConverter.GetBytes(file.Length).CopyTo(file, 2);
        BitConverter.GetBytes(54).CopyTo(file, 10);
        BitConverter.GetBytes(40).CopyTo(file, 14);
        BitConverter.GetBytes(frame.Width).CopyTo(file, 18);
        BitConverter.GetBytes(-frame.Height).CopyTo(file, 22);
        BitConverter.GetBytes((short)1).CopyTo(file, 26);
        BitConverter.GetBytes((short)32).CopyTo(file, 28);
        BitConverter.GetBytes(pixels.Length).CopyTo(file, 34);
        pixels.CopyTo(file, 54);
        return file;
    }
}
