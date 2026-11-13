using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>The sections of the app (the sidebar).</summary>
public enum AppSection { Remote, Devices, Account, Settings }

/// <summary>
/// The app's state: the account, the connection to Mixel and the parts of the app. Reads the engine a few
/// times a second while something happens, rarely otherwise. Lives on the UI thread: the engine calls
/// return there.
/// </summary>
public sealed class AppModel : Observable
{
    private AppSection section = AppSection.Remote;
    private AccountStatus account = new() { Message = "Sign in with your Mixel account" };
    private RemoteStatus remote = RemoteStatus.Offline;
    private string error = "";
    private string progress = "";
    private bool busy;
    private string? signInLink;
    private string verificationCode = "";
    private string lastRemoteJson = "";
    private string lastAccountJson = "";
    private int autoTries;
    private DateTimeOffset nextAutoTry = DateTimeOffset.MinValue;
    private DateTimeOffset lastAccountRead = DateTimeOffset.MinValue;
    private string accountKey = "";
    private CancellationTokenSource? loop;

    public AppModel(IEngine engine, IPreferences? preferences = null)
    {
        Engine = engine;
        Preferences = preferences ?? new MemoryPreferences();
        Devices = new DevicesModel(this);
        Session = new SessionModel(this);
        Host = new HostModel(this);
        Company = new CompanyModel(this);
        Settings = new SettingsModel(this);
        Enrollment = new EnrollmentModel(this);
        Assistant = new AssistantModel(this);
    }

    public IEngine Engine { get; }
    public IPreferences Preferences { get; }
    public DevicesModel Devices { get; }
    public SessionModel Session { get; }
    public HostModel Host { get; }
    public CompanyModel Company { get; }
    public SettingsModel Settings { get; }
    public EnrollmentModel Enrollment { get; }
    public AssistantModel Assistant { get; }

    public AppSection Section { get => section; set => Set(ref section, value); }
    public AccountStatus Account { get => account; private set => Set(ref account, value); }
    public RemoteStatus Remote { get => remote; private set => Set(ref remote, value); }
    /// <summary>The last failure of an action, shown near the action.</summary>
    public string Error { get => error; set => Set(ref error, value); }
    /// <summary>Progress of a connection being prepared ("Looking up this ID…").</summary>
    public string Progress { get => progress; set => Set(ref progress, value); }
    public bool Busy { get => busy; private set => Set(ref busy, value); }
    /// <summary>The sign-in link to open in another browser.</summary>
    public string? SignInLink { get => signInLink; set => Set(ref signInLink, value); }
    public string VerificationCode { get => verificationCode; set => Set(ref verificationCode, value); }

    /// <summary>The app opens web pages (sign-in, legal texts) through this.</summary>
    public Action<string> OpenUrl { get; set; } = _ => { };
    /// <summary>Puts a text on the clipboard (commands, links).</summary>
    public Action<string> CopyText { get; set; } = _ => { };
    /// <summary>The app's Open dialog: a file to send (absolute path), or null.</summary>
    public Func<Task<string?>> ChooseFile { get; set; } = () => Task.FromResult<string?>(null);
    /// <summary>The app's Save dialog for a suggested name: where to save (absolute path), or null.</summary>
    public Func<string, Task<string?>> ChooseSaveLocation { get; set; } = _ => Task.FromResult<string?>(null);

    /// <summary>The person chose "Go offline": do not come back online by itself until they connect again.</summary>
    internal bool WentOffline { get; set; }
    /// <summary>The last action started with <see cref="Act"/> (tests await it).</summary>
    public Task LastAction { get; private set; } = Task.CompletedTask;

    public EngineInfo Info => Engine.Info;
    public bool SignedIn => Account.SignedIn;

    /// <summary>Starts reading the engine. Idempotent.</summary>
    public void Start()
    {
        if (loop is not null) return;
        loop = new CancellationTokenSource();
        var token = loop.Token;
        _ = RunLoop(token);
    }

    private async Task RunLoop(CancellationToken token)
    {
        await RefreshAccount();
        await Settings.FlushPendingRemoval();
        while (!token.IsCancellationRequested)
        {
            await Tick();
            var fast = Remote.InSession || Remote.Phase is "connecting" or "outgoing" || Account.SigningIn;
            try { await Task.Delay(fast ? 250 : 1000, token); }
            catch (TaskCanceledException) { return; }
        }
    }

    public void Stop()
    {
        loop?.Cancel();
        loop = null;
    }

    public async Task Tick()
    {
        var every = Account.SigningIn || Account.Phase == "mfa_required" ? TimeSpan.FromSeconds(1) : TimeSpan.FromSeconds(10);
        if (DateTimeOffset.UtcNow - lastAccountRead >= every) await RefreshAccount();
        await RefreshRemote();
    }

    public async Task RefreshAccount()
    {
        lastAccountRead = DateTimeOffset.UtcNow;
        string json;
        try { json = await Engine.CallAsync("account_status", "{}"); }
        catch (EngineFailure) { return; }
        if (json != lastAccountJson)
        {
            lastAccountJson = json;
            var next = Json.Decode<AccountStatus>(json);
            if (next is null) return;
            Account = next;
        }
        var current = Account;
        var key = current.SignedIn ? $"{current.Account!.Profile}|{current.Account.Organization}|{current.Account.Personal}" : "";
        if (key != accountKey)
        {
            accountKey = key;
            SignInLink = null;
            await AccountChanged();
        }
    }

    public async Task RefreshRemote()
    {
        string json;
        try { json = await Engine.CallAsync("remote_status", "{}"); }
        catch (EngineFailure) { return; }
        var previous = Remote;
        if (json != lastRemoteJson)
        {
            lastRemoteJson = json;
            if (Json.Decode<RemoteStatus>(json) is { } next) Remote = next;
        }
        var now = Remote;
        if (now.Phase == "ready")
        {
            autoTries = 0;
            nextAutoTry = DateTimeOffset.MinValue;
        }
        Session.StatusChanged(previous, now);
        Host.StatusChanged(previous, now);
        Devices.StatusChanged(previous, now);
        await GoOnlineIfNeeded();
    }

    /// <summary>
    /// A set-up computer goes online by itself once someone is signed in, so the person who needs help
    /// never has to look for a button. Not again after "Go offline", with a growing pause.
    /// </summary>
    internal async Task GoOnlineIfNeeded()
    {
        if (!SignedIn || WentOffline || !Remote.Disconnected || Busy || DateTimeOffset.UtcNow < nextAutoTry || Enrollment.NeedsSetup) return;
        int[] pauses = [0, 5, 15, 30, 60];
        autoTries++;
        nextAutoTry = DateTimeOffset.UtcNow.AddSeconds(pauses[Math.Min(autoTries, pauses.Length - 1)]);
        try { await Engine.PerformAsync("remote_register"); }
        catch (EngineFailure failure) { Enrollment.RegisterFailed(failure); }
    }

    private async Task AccountChanged()
    {
        Error = "";
        Devices.Reset();
        Company.Reset();
        Enrollment.Reset();
        if (!SignedIn) return;
        await Company.Load();
        await Enrollment.Check();
        await Devices.Load();
        await Settings.Load();
    }

    /// <summary>Runs an action; its failure shows in <see cref="Error"/>. The engine is read again afterwards.</summary>
    public Task Act(Func<Task> work)
    {
        if (Busy) return Task.CompletedTask;
        Busy = true;
        Error = "";
        LastAction = Run();
        return LastAction;

        async Task Run()
        {
            try { await work(); }
            catch (Exception failure) { Error = Message(failure); }
            Busy = false;
            await RefreshRemote();
        }
    }

    public static string Message(Exception error) => error is EngineFailure failure ? failure.Translated : error.Message;

    // ---- Sign-in ----

    public Task SignIn(bool showLink = false) => Act(async () =>
    {
        var url = await Engine.RunAsync<string>("account_sign_in", ("open_browser", false));
        if (showLink) SignInLink = url;
        else if (!string.IsNullOrEmpty(url)) OpenUrl(url);
        await RefreshAccount();
    });

    public Task CancelSignIn() => Act(async () =>
    {
        await Engine.PerformAsync("account_cancel");
        await RefreshAccount();
    });

    public Task Verify(string factor)
    {
        var code = new string([.. VerificationCode.Where(char.IsAsciiDigit)]);
        if (code.Length != 6)
        {
            Error = L("Enter the 6-digit code");
            return Task.CompletedTask;
        }
        VerificationCode = "";
        return Act(async () =>
        {
            await Engine.PerformAsync("account_verify", ("factor", factor), ("code", code));
            await RefreshAccount();
        });
    }

    public Task SignOut() => Act(async () =>
    {
        try { await Engine.PerformAsync("remote_disconnect"); } catch (EngineFailure) { }
        await Engine.PerformAsync("set_stay_signed_in", ("enabled", false));
        await Engine.PerformAsync("account_cancel");
        await RefreshAccount();
    });

    /// <summary>First sign-in of a personal account: the name shown to its devices (account_setup).</summary>
    public Task SetUpProfile(string name)
    {
        name = name.Trim();
        if (name.Length == 0)
        {
            Error = L("Enter a name");
            return Task.CompletedTask;
        }
        return Act(async () =>
        {
            await Engine.PerformAsync("account_setup", ("name", name));
            await RefreshAccount();
        });
    }

    /// <summary>Whether this service lets people create a Mixel ID from the app.</summary>
    public async Task<bool> SignupAvailable()
    {
        try { return await Engine.RunAsync<bool>("remote_signup_available"); }
        catch (EngineFailure) { return false; }
    }

    public Task SetWorkspace(bool personal) => Act(async () =>
    {
        await Engine.PerformAsync("account_workspace", ("personal", personal));
        await RefreshAccount();
    });

    // ---- The connection ----

    public Task GoOnline()
    {
        WentOffline = false;
        autoTries = 1;
        // The automatic retry waits a little, so it never doubles this request.
        nextAutoTry = DateTimeOffset.UtcNow.AddSeconds(5);
        return Act(() => Engine.PerformAsync("remote_register"));
    }

    public Task GoOffline()
    {
        WentOffline = true;
        Session.Clear();
        return Act(() => Engine.PerformAsync("remote_disconnect"));
    }

    /// <summary>
    /// Connects to a Remote ID: brings this computer online first if needed; for a phone with Mixel Quick
    /// Support it claims the phone and waits until it is online, so one click is enough.
    /// </summary>
    public Task Connect(uint id, string? password = null, bool unattended = false)
    {
        if (Busy)
        {
            Progress = L("Please wait a moment, the previous step is still running.");
            return Task.CompletedTask;
        }
        Busy = true;
        Error = "";
        LastAction = Run();
        return LastAction;

        async Task Run()
        {
            try { await ConnectSteps(id, password, unattended); }
            catch (Exception failure)
            {
                Error = Message(failure);
                Progress = "";
            }
            Busy = false;
            await RefreshRemote();
        }
    }

    private async Task<RemoteStatus?> Status()
    {
        try { return await Engine.RunAsync<RemoteStatus>("remote_status"); }
        catch (EngineFailure) { return null; }
    }

    /// <summary>Seconds between status reads while waiting (tests make it 0).</summary>
    internal int WaitStepMilliseconds { get; set; } = 1000;

    private async Task<bool> Wait(int seconds, Func<RemoteStatus, bool> until, Func<Task>? ask = null)
    {
        for (var round = 0; round < seconds; round++)
        {
            if (await Status() is { } state && until(state)) return true;
            if (ask is not null && round % 2 == 0) await ask();
            await Task.Delay(WaitStepMilliseconds);
        }
        return false;
    }

    private async Task ConnectSteps(uint id, string? password, bool unattended)
    {
        WentOffline = false;
        if (await Status() is { Disconnected: true })
        {
            Progress = L("Getting this computer ready…");
            await Engine.PerformAsync("remote_register");
            if (!await Wait(20, state => state.Phase == "ready"))
            {
                throw new EngineFailure("This computer could not go online. Check the internet connection and try again.");
            }
        }
        Progress = L("Looking up this ID…");
        bool phone;
        try
        {
            phone = await Engine.RunAsync<bool>("quick_phone_claim", ("host", id));
        }
        catch (EngineFailure failure)
        {
            if (System.Text.RegularExpressions.Regex.IsMatch(failure.Message, "MFA|staff|42501"))
            {
                throw new EngineFailure("Connecting to phones needs your Mixel team account with the second confirmation step. Sign in again under Account, then choose Connect.");
            }
            throw new EngineFailure($"This phone could not be prepared for the session: {failure.Message}");
        }
        if (phone)
        {
            Progress = L("Phone found. Waiting for it to come online… On an iPhone the person taps “Start sharing my screen”.");
            var online = await Wait(120, state => state.Online.Contains(id), async () =>
            {
                try { await Engine.PerformAsync("remote_presence", ("hosts", new[] { id })); } catch (EngineFailure) { }
            });
            if (!online)
            {
                throw new EngineFailure("The phone did not come online. Ask the person to keep Mixel Quick Support open, then choose Connect again.");
            }
            Progress = L("Connecting… The person allows the session on their phone.");
        }
        if (await Status() is { } current && current.Phase != "ready")
        {
            throw new EngineFailure(current.Phase is "active" or "incoming" or "outgoing"
                ? "End the current session first, then connect again."
                : "This computer is not ready to connect yet. Please try again in a moment.");
        }
        await Engine.PerformAsync("remote_request", ("host", id), ("password", string.IsNullOrEmpty(password) ? null : password), ("unattended", !phone && unattended));
        Session.LastRequestUnattended = unattended && !phone;
        Progress = L("Waiting for the other person to accept your request.");
    }

    /// <summary>The status line of the connection, as the desktop app words it.</summary>
    public string ConnectionMessage
    {
        get
        {
            if (Session.Stopping) return L("Ending the session…");
            return Remote.Phase switch
            {
                "offline" when Remote.ControllerOnly => SignedIn ? L("Connect this controller to the Mixel network.") : L("Sign in to access managed devices."),
                "offline" => SignedIn ? L("Make this computer available to start or receive a connection.") : L("Sign in to get your connection ID."),
                "ready" => Remote.Message.Length == 0 ? L("Ready for a connection.") : Translator.Shared.Translate(Remote.Message),
                "outgoing" => L("Waiting for the other person to accept your request."),
                "connecting" or "deciding" or "negotiating" => L("Connecting securely…"),
                "active" when Remote.Sharing => L("Sharing your selected screen. You can end the session at any time."),
                "active" => Remote.Control ? L("Connected. Keyboard and mouse control is enabled.") : L("Connected in view-only mode."),
                _ => Translator.Shared.Translate(Remote.Message),
            };
        }
    }

    /// <summary>Before the app quits: end sessions, go offline.</summary>
    public async Task Shutdown()
    {
        Stop();
        Session.Clear();
        try { await Engine.ShutdownAsync(); } catch (EngineFailure) { }
    }
}
