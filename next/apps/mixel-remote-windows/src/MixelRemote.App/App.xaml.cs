using System.ComponentModel;
using System.Diagnostics;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using MixelRemote.App.Host;
using MixelRemote.App.Session;
using MixelRemote.App.Shell;
using MixelRemote.Engine;
using MixelRemote.ViewModels;

namespace MixelRemote.App;

/// <summary>
/// The app: one engine, one app model, the main window, the notification-area icon, the consent window
/// and the session window. Closing the main window keeps Mixel Remote running in the notification area,
/// so this computer stays reachable; Quit is in the icon's menu.
/// </summary>
public partial class App : Application
{
    public static int ExitCode { get; private set; }
    public static new App Current => (App)Application.Current;

    private readonly StartOptions options;
    private readonly EventWaitHandle? showRequests;
    private MainWindow? main;
    private TrayIcon? tray;
    private ConsentWindow? consent;
    private SessionWindow? session;
    private DispatcherQueueTimer? updates;
    private bool quitting;

    public App(StartOptions options, EventWaitHandle? show)
    {
        this.options = options;
        showRequests = show;
        InitializeComponent();
        UnhandledException += (sender, error) =>
        {
            Log($"unhandled: {error.Exception}");
            error.Handled = true;
        };
    }

    public AppModel Model { get; private set; } = null!;
    public static string DataFolder => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ch.mixel.remote.desktop");

    public static void Log(string message)
    {
        try
        {
            Directory.CreateDirectory(DataFolder);
            File.AppendAllText(Path.Combine(DataFolder, "windows-app.log"), $"{DateTimeOffset.Now:O} {message}{Environment.NewLine}");
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    protected override async void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            if (options.Screenshots is { } folder)
            {
                ExitCode = await Lab.Screenshots.Run(folder);
                Exit();
                return;
            }
#if LAB
            // LAB: the real app (windows, view models, engine) signs in with a QA session file.
            if (options.Lab.Length >= 4) Environment.SetEnvironmentVariable("MIXEL_QA_SESSION_FILE", options.Lab[1]);
#endif
            if (options.Demo is { } screen)
            {
                Model = await Demo.DemoFixtures.Screen(screen);
            }
            else
            {
                var preferences = new FilePreferences(Path.Combine(DataFolder, "windows-app.json"));
                // Settings of Mixel Remote 0.1 come along once (language, unattended sharing, …).
                LegacySettings.Import(LegacySettings.DefaultFolder(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)), preferences);
                IEngine engine;
                try
                {
                    engine = new NativeEngine(DataFolder);
                }
                catch (Exception failure) when (failure is EngineFailure or DllNotFoundException or EntryPointNotFoundException or BadImageFormatException)
                {
                    Log($"engine: {failure.Message}");
                    Shell.MainWindow.ShowFatal(failure is EngineFailure ? failure.Message : L("Mixel Remote could not start its engine. Please reinstall Mixel Remote."));
                    return;
                }
                Model = new AppModel(engine, preferences);
            }
            Wire(Model);
            main = new MainWindow(Model);
            tray = new TrayIcon(main, Model);
            consent = new ConsentWindow(Model);
            session = new SessionWindow(Model);
            Model.PropertyChanged += OnModelChanged;
            if (!options.Hidden) main.Show();
            Model.Start();
            WatchShowRequests();
            updates = main.DispatcherQueue.CreateTimer();
            updates.Interval = TimeSpan.FromMinutes(30);
            updates.Tick += (_, _) => _ = Model.Settings.CheckForUpdateQuietly();
            updates.Start();
            _ = Model.Settings.CheckForUpdateQuietly();
#if LAB
            if (options.Lab.Length >= 4)
            {
                ExitCode = await Lab.UiLiveCheck.Run(options.Lab, Model, main, session!);
                await Quit();
            }
#endif
        }
        catch (Exception failure)
        {
            Log($"start: {failure}");
            throw;
        }
    }

    /// <summary>The app's system services for the view models: browser, clipboard, file dialogs.</summary>
    private void Wire(AppModel model)
    {
        model.OpenUrl = OpenUrl;
        model.CopyText = Clipboard.Copy;
        model.ChooseFile = () => Dialogs.OpenFile(main?.Handle ?? 0);
        model.ChooseSaveLocation = name => Dialogs.SaveFile(session?.Visible == true ? session.Handle : main?.Handle ?? 0, name);
        model.Settings.QuitForUpdate = () => main?.DispatcherQueue.TryEnqueue(() => _ = Quit());
    }

    public static void OpenUrl(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttps) return;
        try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
        catch (Win32Exception failure) { Log($"browser: {failure.Message}"); }
    }

    private void OnModelChanged(object? sender, PropertyChangedEventArgs change)
    {
        if (change.PropertyName != nameof(AppModel.Remote)) return;
        var remote = Model.Remote;
        // A technician asks: the consent window comes to the front, even from the notification area.
        if (Model.Host.Asking) consent?.Present();
        else consent?.Dismiss();
        var viewing = remote.Viewing || remote.Phase == "outgoing" || (remote.Phase == "negotiating" && !remote.Sharing && remote.Session is not null);
        if (viewing) session?.Present();
        else session?.Dismiss();
        tray?.Update();
    }

    private void WatchShowRequests()
    {
        if (showRequests is null || main is null) return;
        var queue = main.DispatcherQueue;
        var thread = new Thread(() =>
        {
            while (!quitting)
            {
                if (showRequests.WaitOne(1000) && !quitting) queue.TryEnqueue(() => main.Show());
            }
        }) { IsBackground = true, Name = "show requests" };
        thread.Start();
    }

    public void ShowMain() => main?.Show();

    /// <summary>Quit from the notification area: end sessions, go offline, leave.</summary>
    public async Task Quit()
    {
        if (quitting) return;
        quitting = true;
        updates?.Stop();
        tray?.Dispose();
        await Model.Shutdown();
        session?.Close();
        consent?.Close();
        main?.CloseForQuit();
        Exit();
    }
}
