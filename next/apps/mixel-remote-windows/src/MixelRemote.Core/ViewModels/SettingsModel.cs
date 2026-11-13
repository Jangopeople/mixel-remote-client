using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;

namespace MixelRemote.ViewModels;

/// <summary>
/// Settings: stay signed in, start with Windows, language, this computer (remove it), sign-in screen and
/// always-on access, remote printing, permissions, updates, About and legal.
/// </summary>
public sealed class SettingsModel : Observable
{
    public const string StorePage = "https://store.mixel.ch/app/mixel-remote";
    private const string LanguageKey = "mixel.remote.language.v1";
    private const string AutoUpdateKey = "mixel.remote.autoupdate.v1";
    private bool installing;
    private bool installFailed;
    private readonly AppModel app;
    private StartupSettings? startup;
    private PrintingStatus? printing;
    private SignInScreen? signInScreen;
    private UpdateInfo? update;
    private bool updateChecked;
    private bool removalPending;
    private string language;
    private string error = "";
    private string notice = "";
    private bool working;

    internal SettingsModel(AppModel app)
    {
        this.app = app;
        language = app.Preferences.Get(LanguageKey) ?? "auto";
        Translator.Shared.Choice = language;
    }

    public StartupSettings? Startup { get => startup; private set => Set(ref startup, value); }
    public PrintingStatus? Printing { get => printing; private set => Set(ref printing, value); }
    public SignInScreen? SignInScreen { get => signInScreen; private set => Set(ref signInScreen, value); }
    public UpdateInfo? Update { get => update; private set => Set(ref update, value); }
    public bool UpdateChecked { get => updateChecked; private set => Set(ref updateChecked, value); }
    public bool Installing { get => installing; private set => Set(ref installing, value); }
    public bool InstallFailed { get => installFailed; private set => Set(ref installFailed, value); }
    /// <summary>Install new versions by themselves when nothing would be interrupted (on unless turned off).</summary>
    public bool AutoUpdate
    {
        get => app.Preferences.Get(AutoUpdateKey) != "off";
        set
        {
            app.Preferences.Set(AutoUpdateKey, value ? "on" : "off");
            Raise();
        }
    }
    /// <summary>Called when an installed update is about to replace the app (the app quits).</summary>
    public Action QuitForUpdate { get; set; } = () => { };
    public bool RemovalPending { get => removalPending; private set => Set(ref removalPending, value); }
    public string Language { get => language; private set => Set(ref language, value); }
    public string Error { get => error; set => Set(ref error, value); }
    public string Notice { get => notice; set => Set(ref notice, value); }
    public bool Working { get => working; private set => Set(ref working, value); }

    public async Task Load()
    {
        try { Startup = await app.Engine.RunAsync<StartupSettings>("startup_settings"); } catch (EngineFailure) { }
        try { RemovalPending = await app.Engine.RunAsync<bool>("device_removal_pending"); } catch (EngineFailure) { }
        if (app.Info.ControllerOnly) return;
        try { Printing = await app.Engine.RunAsync<PrintingStatus>("remote_printing_status"); } catch (EngineFailure) { }
        try { SignInScreen = await app.Engine.RunAsync<SignInScreen>("signin_screen_status"); } catch (EngineFailure) { }
    }

    /// <summary>Sends a removal of this computer that was saved while offline.</summary>
    internal async Task FlushPendingRemoval()
    {
        try
        {
            var status = SelfRemoval.Parse(await app.Engine.RunAsync<string?>("device_self_remove_flush"));
            if (status is { } done && done != SelfRemovalStatus.Queued) app.Enrollment.Forget();
        }
        catch (EngineFailure) { }
        try { RemovalPending = await app.Engine.RunAsync<bool>("device_removal_pending"); } catch (EngineFailure) { }
    }

    public Task SetStaySignedIn(bool enabled) => Change(() => app.Engine.PerformAsync("set_stay_signed_in", ("enabled", enabled)));

    public Task SetAutostart(bool enabled) => Change(async () =>
    {
        if (enabled) await app.Engine.PerformAsync("set_stay_signed_in", ("enabled", true));
        await app.Engine.PerformAsync("set_autostart", ("enabled", enabled));
    });

    /// <summary>Adds or removes "Mixel Remote Printer" (one administrator prompt).</summary>
    public Task SetPrinting(bool enabled) => Change(() => app.Engine.PerformAsync("remote_printing_set", ("enabled", enabled)));

    public void SetLanguage(string value)
    {
        Language = value;
        app.Preferences.Set(LanguageKey, value == "auto" ? null : value);
        Translator.Shared.Choice = value;
    }

    /// <summary>
    /// Sign-in screen and always-on access (Mixel team accounts): a device record "name (sign-in screen)"
    /// of the chosen company and the Windows service. Two administrator prompts.
    /// </summary>
    public Task EnableSignInScreen(string company, string name) => Change(async () =>
    {
        await app.Engine.RunAsync<uint>("signin_screen_enable", ("company", company), ("name", name));
        Notice = T.L("Sign-in screen access is on.");
    });

    public Task DisableSignInScreen() => Change(() => app.Engine.PerformAsync("signin_screen_disable"));
    public Task UpdateSignInScreen() => Change(() => app.Engine.PerformAsync("signin_screen_update"));

    public Task CheckForUpdate() => Change(ReadUpdate);

    private async Task ReadUpdate()
    {
        Update = await app.Engine.RunAsync<UpdateInfo?>("update_check");
        UpdateChecked = true;
    }

    /// <summary>The regular check (every 30 minutes): installs by itself when allowed and silent.</summary>
    public async Task CheckForUpdateQuietly()
    {
        if (Installing) return;
        try { await ReadUpdate(); }
        catch (EngineFailure) { return; }
        if (Update is { Installable: true, Silent: true } && AutoUpdate && !InstallFailed) await InstallUpdate();
    }

    /// <summary>Downloads, checks and installs the new version; the app restarts by itself.</summary>
    public async Task InstallUpdate()
    {
        if (Update is not { Installable: true } || Installing) return;
        Installing = true;
        Error = "";
        try
        {
            await app.Engine.PerformAsync("update_install");
            QuitForUpdate();
        }
        catch (EngineFailure failure)
        {
            Installing = false;
            InstallFailed = true;
            Error = failure.Translated;
        }
    }

    public Task OpenLegal(string page) => Change(async () =>
    {
        app.OpenUrl(await app.Engine.RunAsync<string>("legal_url", ("page", page), ("lang", Translator.Shared.Language)));
    });

    public void OpenStorePage() => app.OpenUrl(StorePage);

    private async Task Change(Func<Task> work)
    {
        Error = "";
        Notice = "";
        Working = true;
        try { await work(); }
        catch (EngineFailure failure) { Error = failure.Translated; }
        finally { Working = false; }
        await Load();
    }
}
