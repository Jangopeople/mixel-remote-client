using System.Diagnostics;
using MixelRemote.App.Session;
using MixelRemote.App.Shell;
using MixelRemote.Lab;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Lab;

/// <summary>
/// LAB builds only: the real app (its windows, view models and engine) in a live session with a throwaway
/// QA account, driven through the same calls the buttons make.
///   --live-session &lt;qa-session.json&gt; &lt;suffix&gt; &lt;folder&gt;: technician; waits for a lab computer enrolled with
///     the deploy link written to the folder, connects unattended, uses the toolbar and tools, ends.
///   --live-host &lt;qa-session.json&gt; &lt;suffix&gt; &lt;folder&gt;: this computer is helped; writes host-id.txt and
///     tenant.txt, waits for a technician, shows the consent window, allows with control, waits for the end.
/// The QA company is deleted and this computer removes itself at the end.
/// </summary>
internal static class UiLiveCheck
{
    private static StreamWriter? log;

    private static void Print(string line)
    {
        log?.WriteLine(line);
        log?.Flush();
    }

    private static async Task Shot(nint window, string path)
    {
        try { Print($"picture {Path.GetFileName(path)} {await Screenshots.Capture(window, path)}"); }
        catch (Exception failure) { Print($"picture {Path.GetFileName(path)} not taken: {failure.Message}"); }
    }

    private static async Task<bool> Until(Func<bool> done, int seconds)
    {
        var clock = Stopwatch.StartNew();
        while (clock.Elapsed < TimeSpan.FromSeconds(seconds))
        {
            if (done()) return true;
            await Task.Delay(250);
        }
        return done();
    }

    public static async Task<int> Run(string[] args, AppModel app, MainWindow main, SessionWindow session)
    {
        var (suffix, folder) = (args[2], args[3]);
        Directory.CreateDirectory(folder);
        log = new StreamWriter(Path.Combine(folder, args[0] == "--live-host" ? "live-host.txt" : "live-session.txt"), append: false);
        var result = 1;
        LiveCheck.SetOutput(Print);
        try
        {
            result = args[0] == "--live-host" ? await Host(app, main, suffix, folder) : await Technician(app, main, session, suffix, folder);
        }
        catch (Exception failure)
        {
            Print($"FAIL unexpected {failure}");
        }
        var (line, passed) = LiveCheck.Summary();
        Print(line);
        log.Dispose();
        log = null;
        return passed ? result : 1;
    }

    /// <summary>Signs in, uses the QA account's company or creates one, adds this computer. Returns the company to delete (null: keep it).</summary>
    private static async Task<(bool Ready, string? Created)> Prepare(AppModel app, string suffix, string role)
    {
        await app.SignIn();
        LiveCheck.Check("sign-in through the app (QA session)", await Until(() => app.SignedIn || app.Account.Phase == "profile_setup", 40), app.Account.Phase);
        if (app.Account.Phase == "profile_setup")
        {
            await app.SetUpProfile("QA Windows");
            LiveCheck.Check("first sign-in: Remote account name set up", await Until(() => app.SignedIn, 30), app.Account.Phase);
        }
        await Until(() => app.Company.Loaded, 20);
        string? created = null;
        if (app.Company.Tenant is { } existing)
        {
            Print($"using the QA account's company {existing.Name}");
        }
        else
        {
            created = $"QA Windows UI {suffix}";
            LiveCheck.Check("create a company in Account", await app.Company.Create(created, acceptedTerms: true) && app.Company.Tenant is not null, app.Company.Error);
        }
        if (app.Company.Tenant is null) return (false, created);
        await app.Devices.AddThisComputer($"QA Windows {role} {suffix}", null);
        LiveCheck.Check("Add this computer (Devices)", app.Enrollment.Current is not null, app.Devices.Error);
        LiveCheck.Check("this computer online with its ID", await Until(() => app.Remote.Phase == "ready" && app.Remote.Host == app.Enrollment.Current?.ConnectionId, 40), app.Remote.Phase);
        return (true, created);
    }

    private static async Task Cleanup(AppModel app, string? company)
    {
        if (app.Remote.InSession) await app.Session.End();
        var removed = await app.Enrollment.RemoveThisComputer();
        LiveCheck.Check("Remove this computer (Settings, signed with its key)", removed == SelfRemovalStatus.Removed, app.Enrollment.Error);
        if (company is not null) LiveCheck.Check("Delete company (Account)", await app.Company.DeleteCompany(company), app.Company.Error);
    }

    private static async Task<int> Technician(AppModel app, MainWindow main, SessionWindow window, string suffix, string folder)
    {
        var (ready, company) = await Prepare(app, suffix, "technician");
        if (!ready) return 1;
        try
        {
            // The lab host: a Remote ID in host-id.txt, or a computer enrolled with a new deploy link.
            uint? wanted = File.Exists(Path.Combine(folder, "host-id.txt")) && uint.TryParse(File.ReadAllText(Path.Combine(folder, "host-id.txt")).Trim(), out var given) ? given : null;
            if (wanted is null)
            {
                LiveCheck.Check("Add other computers: deploy link", await app.Devices.CreateDeployLink("QA lab", 1, 2, null), app.Devices.Error);
                await File.WriteAllTextAsync(Path.Combine(folder, "deploy-command.txt"), app.Devices.DeployLink?.WindowsCommand ?? "");
                await File.WriteAllTextAsync(Path.Combine(folder, "deploy-token.txt"), app.Devices.DeployLink?.Token ?? "");
            }
            app.Section = AppSection.Devices;
            Print("WAITING for the lab computer");
            Device? host = null;
            await Until(() =>
            {
                _ = app.Devices.Load();
                host = app.Devices.Devices.FirstOrDefault(device => device.ConnectionId != app.Remote.Host && (wanted is null || device.ConnectionId == wanted) && app.Devices.PresenceOf(device) == Presence.Online);
                return host is not null;
            }, 900);
            LiveCheck.Check("lab computer enrolled with the deploy link shows Online in Devices", host is not null);
            if (host is null) return 1;
            await Shot(main.Handle, Path.Combine(folder, "devices-with-lab-host.png"));
            await app.Connect(host.ConnectionId, unattended: true);
            LiveCheck.Check("Connect (unattended): session active, view and control", await Until(() => app.Remote is { Phase: "active", Control: true }, 60), app.Error + " " + app.Remote.Phase);
            LiveCheck.Check("session window shows the remote picture", await Until(() => app.Session.Frame is not null && window.Visible, 30));
            await Task.Delay(3000);
            LiveCheck.Check($"pictures arrive ({app.Session.Fps} per second, {app.Session.Frame?.Width}×{app.Session.Frame?.Height})", app.Session.Frame is not null);
            Print($"host actions: {string.Join(",", app.Remote.HostActions)} · features: {string.Join(",", app.Remote.PeerFeatures)} · displays: {app.Remote.Displays}");
            for (var index = 0; index < 20; index++) app.Session.MoveTo(0.3 + index * 0.02, 0.5);
            app.Session.Press(KeyMap.Shift);
            app.Session.Send(new InputEvent.Wheel(0, -1));
            app.Session.Send(new InputEvent.ButtonClick(3, true, 1));
            app.Session.Send(new InputEvent.ButtonClick(3, false, 1));
            app.Session.Press(41);
            await Task.Delay(1500);
            LiveCheck.Check("keyboard, mouse and wheel input delivered", app.Session.Error.Length == 0, app.Session.Error);
            await Shot(window.Handle, Path.Combine(folder, "session-window.png"));
            foreach (var action in new[] { "privacy", "block", "sound" })
            {
                if (!app.Remote.Offers(action)) continue;
                if (action == "privacy") { app.Session.TogglePrivacy(); await Task.Delay(1500); await Shot(window.Handle, Path.Combine(folder, "session-privacy-on.png")); app.Session.TogglePrivacy(); }
                if (action == "block") { app.Session.ToggleBlockInput(); await Task.Delay(800); app.Session.ToggleBlockInput(); }
                if (action == "sound") { app.Session.ToggleSound(); await Task.Delay(800); app.Session.ToggleSound(); }
                await Task.Delay(800);
                LiveCheck.Check($"host action {action} on and off", app.Session.Error.Length == 0, app.Session.Error);
            }
            if (app.Remote.Chat())
            {
                app.Session.ChatDraft = $"QA chat {suffix}";
                await app.Session.SendChat();
                await app.Session.ReadTools();
                // Unattended access (nobody at the computer) has no chat; the app says so.
                LiveCheck.Check("chat: sent, or refused with a clear message for unattended access",
                    app.Session.Chat.Any(message => message.Mine && message.Text.Contains(suffix, StringComparison.Ordinal))
                    || (app.Remote.UnattendedSession && app.Session.Error.Length > 0), app.Session.Error);
                app.Session.Error = "";
            }
            if (app.Remote.Clipboard)
            {
                Shell.Clipboard.Copy($"QA clipboard {suffix}");
                await app.Session.Clipboard(push: true);
                LiveCheck.Check("clipboard sent to the remote computer", app.Session.ClipboardResult == L("Remote clipboard updated."), app.Session.ClipboardResult);
            }
            await app.Session.End();
            LiveCheck.Check("End session: back to ready, session window closed", await Until(() => app.Remote.Phase == "ready" && !window.Visible, 30), app.Remote.Phase);
            return 0;
        }
        finally
        {
            await Cleanup(app, company);
        }
    }

    private static async Task<int> Host(AppModel app, MainWindow main, string suffix, string folder)
    {
        var (ready, company) = await Prepare(app, suffix, "host");
        if (!ready) return 1;
        try
        {
            await File.WriteAllTextAsync(Path.Combine(folder, "host-id.txt"), app.Remote.Host?.ToString(System.Globalization.CultureInfo.InvariantCulture) ?? "");
            await File.WriteAllTextAsync(Path.Combine(folder, "tenant.txt"), app.Company.Tenant?.TenantId ?? "");
            app.Section = AppSection.Remote;
            Print("WAITING for a technician (host-id.txt, tenant.txt)");
            LiveCheck.Check("a technician asks: the consent window appears", await Until(() => app.Host.Asking && app.Host.Sources.Count > 0, 900), app.Remote.Phase);
            await Task.Delay(1500);
            var consent = Native.GetForegroundWindow();
            await Shot(consent, Path.Combine(folder, "consent-window.png"));
            app.Host.AllowChat = true;
            app.Host.AllowClipboard = true;
            app.Host.AllowFiles = true;
            app.Host.AllowRecording = true;
            await app.Host.Decide(accept: true);
            LiveCheck.Check("Allow with control: this computer is shared", await Until(() => app.Remote is { Phase: "active", Sharing: true }, 60), app.Error + " " + app.Remote.Phase);
            await Task.Delay(2000);
            main.Show();
            await Task.Delay(800);
            await Shot(main.Handle, Path.Combine(folder, "hosted-session.png"));
            // The person here: answers the chat and saves the file the technician sends.
            app.Session.ChatDraft = "QA hello from the person here";
            await app.Session.SendChat();
            var saved = false;
            var received = false;
            await Until(() =>
            {
                _ = app.Session.ReadTools();
                received |= app.Session.Chat.Any(message => !message.Mine && message.Text.Contains("technician", StringComparison.Ordinal));
                if (!saved && app.Session.Files?.Incoming is { Phase: "offered" } offer)
                {
                    saved = true;
                    _ = app.Session.AcceptFile(offer.Id, Path.Combine(folder, "received-" + offer.Name));
                }
                return !app.Remote.InSession;
            }, 300);
            LiveCheck.Check("chat message from the technician shown", received);
            LiveCheck.Check("file from the technician saved", saved && File.Exists(Path.Combine(folder, "received-qa-transfer.bin")) && new FileInfo(Path.Combine(folder, "received-qa-transfer.bin")).Length == 300_000);
            LiveCheck.Check("the technician ends the session", !app.Remote.InSession, app.Remote.Phase);
            return 0;
        }
        finally
        {
            await Cleanup(app, company);
        }
    }

    private static bool Chat(this RemoteStatus remote) => remote.Phase == "active";
}
