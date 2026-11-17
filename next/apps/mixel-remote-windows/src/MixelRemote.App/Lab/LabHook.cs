using MixelRemote.ViewModels;
using Microsoft.UI.Xaml;

namespace MixelRemote.App.Lab;

/// <summary>
/// LAB builds only: a command file for the lab scripts, the Windows counterpart of the GTK lab hook. Lines appended to
/// C:\lab\hook\cmd are run once through the same calls the buttons make; answers go to C:\lab\hook\out.log.
///   connect &lt;id&gt; [attended] | end | use &lt;n&gt; | chat &lt;text&gt; | clip push|pull &lt;text&gt; | sendfile &lt;path&gt; | accept &lt;path&gt;
///   | draw | display &lt;n&gt; | move &lt;x&gt; &lt;y&gt; | wheel &lt;dy&gt; | status
/// </summary>
internal static class LabHook
{
    private static readonly string Folder = @"C:\lab\hook";
    private static int target = -1;
    private static DispatcherTimer? timer;

    public static void Start(AppModel app)
    {
        Directory.CreateDirectory(Folder);
        var file = Path.Combine(Folder, "cmd");
        timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
        timer.Tick += async (_, _) =>
        {
            if (!File.Exists(file)) return;
            string[] lines;
            try { lines = File.ReadAllLines(file); File.Delete(file); }
            catch (IOException) { return; }
            foreach (var line in lines.Where(item => item.Trim().Length > 0))
            {
                string answer;
                try { answer = await Run(app, line.Trim()); }
                catch (Exception failure) { answer = "EXCEPTION " + failure.Message; }
                var clock = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() % 100_000_000;
                File.AppendAllText(Path.Combine(Folder, "out.log"), $"{clock} [{line}] {answer}{Environment.NewLine}");
            }
        };
        timer.Start();
    }

    private static SessionModel Pick(AppModel app) => target >= 0 && app.Sessions.ElementAtOrDefault(target) is { } chosen ? chosen : app.Session;

    private static async Task<string> Run(AppModel app, string line)
    {
        var parts = line.Split(' ', 3, StringSplitOptions.RemoveEmptyEntries);
        var session = Pick(app);
        switch (parts[0])
        {
            case "connect":
                await app.Workspace.Connect(uint.Parse(parts[1]), unattended: !(parts.Length > 2 && parts[2] == "attended"));
                return "asked";
            case "end": await session.End(); return "ended";
            case "use": target = int.Parse(parts[1]); return $"target {target}";
            case "chat": session.ChatDraft = line[5..]; await session.SendChat(); return "error=" + session.Error;
            case "clip":
                if (parts[1] == "push") { Shell.Clipboard.Copy(parts.Length > 2 ? parts[2] : "lab"); }
                await session.Clipboard(parts[1] == "push");
                return session.ClipboardResult;
            case "sendfile": await session.SendFile(line[9..]); return "error=" + session.Error;
            case "accept":
                await session.ReadTools();
                if (session.Files?.Incoming is { Phase: "offered" } offer) { await session.AcceptFile(offer.Id, line[7..]); return "accepted " + offer.Name; }
                return "nothing offered";
            case "draw":
                session.Drawing = true; session.BeginStroke(0.3, 0.3);
                for (var step = 1; step <= 20; step++) session.ContinueStroke(0.3 + step * 0.015, 0.3 + Math.Sin(step / 3.0) * 0.05);
                session.EndStroke(); return "drawn canDraw=" + session.CanDraw;
            case "display": session.SelectDisplay(ushort.Parse(parts[1])); return "display " + parts[1];
            case "move": session.MoveTo(double.Parse(parts[1], System.Globalization.CultureInfo.InvariantCulture), double.Parse(parts[2], System.Globalization.CultureInfo.InvariantCulture)); return "moved";
            case "wheel": session.Send(new MixelRemote.Models.InputEvent.Wheel(0, short.Parse(parts[1]))); return "wheel";
            case "status":
                await session.ReadTools();
                var r = app.Remote;
                var files = session.Files;
                return $"phase={r.Phase} sessions={app.Sessions.Count()} tabs={app.Workspace.Tabs.Count} fps={session.Fps} frame={session.Frame?.Width}x{session.Frame?.Height} displays={r.Displays} display={r.Display} actions={string.Join(",", r.HostActions)} " +
                    $"in={files?.Incoming?.Phase}/{files?.Incoming?.Name} out={files?.Outgoing?.Phase}/{files?.Outgoing?.Progress:0.00} chat={string.Join("|", session.Chat.TakeLast(3).Select(m => (m.Mine ? "me:" : "peer:") + m.Text))} clip={session.ClipboardResult} error={session.Error}";
            case "attempt": { var a = app.Workspace.Attempt; return a is null ? "no attempt" : $"step={a.Step} unattended={a.Unattended} failure={a.Failure}"; }
            case "grants":
            {
                // grants <connection id>: the "Who may connect" list of the device
                var device = app.Devices.AllDevices.FirstOrDefault(d => d.ConnectionId == uint.Parse(parts[1]));
                if (device is null) return "no such device";
                var rows = await app.Devices.UnattendedWho(device);
                return rows is null ? "error=" + app.Devices.Error : string.Join(" | ", rows.Select(row => $"{row.UserId}:{row.Name}:{row.Role}:granted={row.Granted}:auto={row.Automatic}"));
            }
            case "grant":
            {
                // grant <connection id> <user id> on|off
                var args = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                var device = app.Devices.AllDevices.FirstOrDefault(d => d.ConnectionId == uint.Parse(args[1]));
                if (device is null) return "no such device";
                return (await app.Devices.SetUnattendedGrant(device, args[2], args[3] == "on")) ? "ok" : "error=" + app.Devices.Error;
            }
            default: return "unknown command";
        }
    }
}
