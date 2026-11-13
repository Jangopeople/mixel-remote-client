using System.Diagnostics;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace MixelRemote.App;

/// <summary>
/// Start of Mixel Remote for Windows. Engine steps without a window (sign-in screen and always-on access,
/// remote printer, mass rollout) are handed to mixel-remote-helper.exe with the same arguments the 0.1 app
/// took, so deployment tools and the installed service keep working after the update.
/// </summary>
public static class Program
{
    public const string AppId = "ch.mixel.remote.desktop";
    private const string InstanceName = @"Local\ch.mixel.remote.desktop.window";
    private const string ShowEventName = @"Local\ch.mixel.remote.desktop.show";

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length > 0 && IsEngineStep(args[0])) return RunHelper(args);
        WinRT.ComWrappersSupport.InitializeComWrappers();
        _ = Shell.Native.SetCurrentProcessExplicitAppUserModelID(AppId);
        var options = StartOptions.Parse(args);
        Mutex? instance = null;
        EventWaitHandle? show = null;
        if (!options.Tooling)
        {
            instance = new Mutex(true, InstanceName, out var first);
            if (!first)
            {
                // Mixel Remote is already running: bring its window forward.
                try { EventWaitHandle.OpenExisting(ShowEventName).Set(); } catch (WaitHandleCannotBeOpenedException) { }
                instance.Dispose();
                return 0;
            }
            show = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName);
        }
        Application.Start(start =>
        {
            var context = new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread());
            SynchronizationContext.SetSynchronizationContext(context);
            _ = new App(options, show);
        });
        GC.KeepAlive(instance);
        return App.ExitCode;
    }

    private static bool IsEngineStep(string first) =>
        first.StartsWith("--signin-", StringComparison.Ordinal) || first.StartsWith("--printer-", StringComparison.Ordinal)
        || first.StartsWith("--validate-", StringComparison.Ordinal) || first == "--version";

    /// <summary>Runs the helper with these arguments; standard input (deploy token) and the environment are inherited.</summary>
    private static int RunHelper(string[] args)
    {
        var helper = Path.Combine(AppContext.BaseDirectory, "mixel-remote-helper.exe");
        if (!File.Exists(helper)) return 2;
        var start = new ProcessStartInfo(helper) { UseShellExecute = false, CreateNoWindow = true };
        foreach (var argument in args) start.ArgumentList.Add(argument);
        using var process = Process.Start(start);
        if (process is null) return 2;
        process.WaitForExit();
        return process.ExitCode;
    }
}

/// <summary>What the app was started for.</summary>
public sealed class StartOptions
{
    /// <summary>Started with Windows (--autostart): stay in the notification area.</summary>
    public bool Hidden { get; private init; }
    /// <summary>--screenshots &lt;folder&gt;: renders every screen with the demo engine in light and dark, then quits.</summary>
    public string? Screenshots { get; private init; }
    /// <summary>--demo &lt;screen&gt;: opens the app with the demo engine on one screen.</summary>
    public string? Demo { get; private init; }
    /// <summary>LAB builds: --live-session / --live-host &lt;qa-session.json&gt; &lt;suffix&gt; &lt;folder&gt; against the live service with a QA account.</summary>
    public string[] Lab { get; private init; } = [];
    public bool Tooling => Screenshots is not null || Lab.Length > 0;

    public static StartOptions Parse(string[] args)
    {
        string? Value(string name)
        {
            var index = Array.IndexOf(args, name);
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        var lab = args.Length > 0 && args[0] is "--live-session" or "--live-host" ? args : [];
        return new StartOptions
        {
            Hidden = args.Contains("--autostart"),
            Screenshots = Value("--screenshots"),
            Demo = Value("--demo"),
            Lab = lab,
        };
    }
}
