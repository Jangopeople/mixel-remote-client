using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>The sections of the app (the sidebar).</summary>
public enum AppSection { Remote, Devices, Sessions, Reports, Account, Settings }

/// <summary>
/// The app's state: the account, the connection to Mixel and the parts of the app. Reads the engine a few
/// times a second while something happens, rarely otherwise. Lives on the UI thread: the engine calls
/// return there.
/// </summary>
public sealed class AppModel : Observable
{
    private AppSection section = AppSection.Devices;
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
        idle = new SessionModel(this);
        Host = new HostModel(this);
        Company = new CompanyModel(this);
        Settings = new SettingsModel(this);
        Enrollment = new EnrollmentModel(this);
        Assistant = new AssistantModel(this);
        Workspace = new WorkspaceModel(this);
        Reports = new ReportsModel(this);
    }

    public IEngine Engine { get; }
    public IPreferences Preferences { get; }
    public DevicesModel Devices { get; }
    private readonly SessionModel idle;
    private readonly List<SessionModel> models = [];
    private readonly Dictionary<uint, bool> requestedUnattended = [];
    private RemoteStatus idleStatus = RemoteStatus.Offline;
    private string sessionKeys = "";

    /// <summary>The technician sessions the engine reports, oldest first: one model each (engine 2.1 runs up to four at once).</summary>
    public IReadOnlyList<SessionModel> Sessions => [.. models.Where(model => model.Item is not null)];
    /// <summary>The session of a device (by its Remote ID), or null.</summary>
    public SessionModel? SessionFor(uint peer) => models.FirstOrDefault(model => model.Item is not null && model.Key == peer);
    /// <summary>
    /// The session that is in front (the focused one), else the oldest; the model without a session when none is viewed
    /// (it also carries the hosted side). Screens of one session use <see cref="SessionFor"/>.
    /// </summary>
    public SessionModel IdleSession => idle;
    public SessionModel Session => (Workspace.Focused is { } focused ? SessionFor(focused) : null) ?? Sessions.FirstOrDefault() ?? idle;
    /// <summary>After a session to a company computer: the computer the technician may add a note to (Remote ID).</summary>
    public uint? NoteFor { get; internal set; }
    public void NoteTaken() => NoteFor = null;
    public HostModel Host { get; }
    public CompanyModel Company { get; }
    public SettingsModel Settings { get; }
    public EnrollmentModel Enrollment { get; }
    public AssistantModel Assistant { get; }
    public WorkspaceModel Workspace { get; }
    public ReportsModel Reports { get; }

    public AppSection Section { get => section; set { if (Set(ref section, value)) _ = Devices.UpdatePrewarm(); } }
    private bool appActive = true;
    /// <summary>The window is in front and not hidden (the shell sets it; a ready transport is kept only while true).</summary>
    public bool AppActive { get => appActive; set { if (Set(ref appActive, value)) _ = Devices.UpdatePrewarm(); } }
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
    /// <summary>Quick Support mode: gets a 9-digit Connection ID and accepts attended support without signing in.</summary>
    public bool QuickSupportMode { get => quickSupportMode; set => Set(ref quickSupportMode, value); }
    private bool quickSupportMode;
    /// <summary>Launched as standalone Mixel Quick Support (--quick-support or Mixel-Quick-Support.exe).</summary>
    public bool QuickSupportStandalone { get => quickSupportStandalone; set => Set(ref quickSupportStandalone, value); }
    private bool quickSupportStandalone;

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
            var connecting = Remote.Phase is "connecting" or "outgoing" or "negotiating" or "deciding";
            var fast = Remote.InSession || connecting || Account.SigningIn;
            try { await Task.Delay(connecting ? 25 : (fast ? 80 : 1000), token); }
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
        var every = Account.Phase == "verifying"
            ? TimeSpan.FromMilliseconds(250)
            : (Account.SigningIn || Account.Phase == "mfa_required" ? TimeSpan.FromSeconds(1) : TimeSpan.FromSeconds(10));
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

    private string lastSignificant = "";
    private DateTimeOffset lastRemoteRaised = DateTimeOffset.MinValue;

    /// <summary>The status without its running counters (bytes received, picture statistics).</summary>
    internal static string Significant(string json)
    {
        try
        {
            if (System.Text.Json.Nodes.JsonNode.Parse(json) is not System.Text.Json.Nodes.JsonObject root) return json;
            root.Remove("received");
            if (root["sessions"] is System.Text.Json.Nodes.JsonArray sessions)
            {
                foreach (var item in sessions.OfType<System.Text.Json.Nodes.JsonObject>()) { item.Remove("received"); item.Remove("quality"); }
            }
            return root.ToJsonString();
        }
        catch (System.Text.Json.JsonException) { return json; }
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
            if (Json.Decode<RemoteStatus>(json) is { } next)
            {
                // Byte and picture counters change with every poll. They must not rebuild every page 10 times a second (a live session froze
                // the window in the lab VM): the change is announced only when something else changed, else at most once a second.
                var significant = Significant(json);
                var now0 = DateTimeOffset.UtcNow;
                if (significant != lastSignificant || now0 - lastRemoteRaised >= TimeSpan.FromSeconds(1))
                {
                    lastSignificant = significant;
                    lastRemoteRaised = now0;
                    Remote = next;
                }
                else remote = next;
            }
        }
        var now = Remote;
        if (now.Phase == "ready")
        {
            autoTries = 0;
            nextAutoTry = DateTimeOffset.MinValue;
        }
        SyncSessions(now);
        var idleNext = now.TechnicianSessions.Count == 0 ? now : RemoteStatus.Offline;
        idle.StatusChanged(idleStatus, idleNext);
        idleStatus = idleNext;
        Host.StatusChanged(previous, now);
        Devices.StatusChanged(previous, now);
        Workspace.Refresh(now);
        await GoOnlineIfNeeded();
    }

    /// <summary>One model per technician session the engine lists; a session that is gone loses its model (unless a restart reconnects).</summary>
    private void SyncSessions(RemoteStatus now)
    {
        var items = now.TechnicianSessions;
        var changed = false;
        foreach (var item in items)
        {
            var model = models.FirstOrDefault(candidate => candidate.Key == item.Peer);
            if (model is null)
            {
                model = new SessionModel(this) { IdleDelay = idleDelay };
                if (requestedUnattended.TryGetValue(item.Peer, out var unattended)) model.LastRequestUnattended = unattended;
                models.Add(model);
                changed = true;
            }
            else if (model.Item is null) changed = true;
            model.Update(item, now);
        }
        foreach (var model in models.Where(model => model.Item is not null && !items.Any(item => item.Peer == model.Key)).ToList())
        {
            model.Ended();
            changed = true;
        }
        foreach (var model in models.Where(model => model.Item is null).ToList())
        {
            if (!model.Reconnecting)
            {
                models.Remove(model);
                requestedUnattended.Remove(model.Key);
                model.Dispose();
                continue;
            }
            model.ContinueReconnect(now, null, model.Key);
        }
        var keys = string.Join(',', models.Where(model => model.Item is not null).Select(model => model.Key));
        if (changed || keys != sessionKeys)
        {
            sessionKeys = keys;
            Raise(nameof(Sessions));
        }
    }

    private int idleDelay = 4;
    /// <summary>Milliseconds a session waits when no picture is ready (tests make it 1).</summary>
    internal int PictureIdleDelay
    {
        get => idleDelay;
        set
        {
            idleDelay = value;
            idle.IdleDelay = value;
            foreach (var model in models) model.IdleDelay = value;
        }
    }

    /// <summary>
    /// A new technician session may be requested now: this computer is ready, or it holds technician sessions and the
    /// engine's limit (4) is not reached. Never while this computer is shared.
    /// </summary>
    public bool CanRequestSession(RemoteStatus status) =>
        status.Phase == "ready" || (status.Sessions is { Count: > 0 } open && !status.Sharing && open.Count < status.SessionLimit);

    /// <summary>The Connect button may be used: nothing runs, or another session fits.</summary>
    public bool CanStartConnection => !Remote.InSession || CanRequestSession(Remote);

    /// <summary>Cancels every request that still waits for the other side to accept.</summary>
    public Task CancelRequests() => Task.WhenAll(Sessions.Where(model => model.Item is { Asking: true }).Select(model => model.End()));

    /// <summary>
    /// A set-up computer goes online by itself once someone is signed in, so the person who needs help
    /// never has to look for a button. Not again after "Go offline", with a growing pause.
    /// </summary>
    internal async Task GoOnlineIfNeeded()
    {
        if (!SignedIn || WentOffline || !Remote.Disconnected || Busy || DateTimeOffset.UtcNow < nextAutoTry || Enrollment.NeedsSetup) return;
        int[] pauses = [0, 2, 5, 10, 20];
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
        QuickSupportMode = false;
        // Start registration and show devices before fetching administration-only lists.
        await RefreshRemote();
        await Company.Load(includePeople: false);
        await Enrollment.Check();
        await Devices.Load();
        await Settings.Load();
        await Company.LoadPeople();
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

    /// <summary>Stops every session's picture and forgets it.</summary>
    public void ClearSessions()
    {
        idle.Clear();
        foreach (var model in models) model.Clear();
    }

    public static string Message(Exception error) => error is EngineFailure failure ? failure.Translated : error.Message;

    // ---- Sign-in ----

    /// <summary>The outside sign-ins, in the portal's order: (GoTrue provider, portal method code, label).</summary>
    public static readonly (string Provider, string Method, string Label)[] AllProviders =
        [("keycloak", "mixel_id", "Mixel ID"), ("google", "google", "Google"), ("azure", "azure", "Microsoft"), ("apple", "apple", "Apple")];

    private List<string> providers = ["keycloak"];
    private SignInOptions? signInOptions;

    /// <summary>Providers the sign-in service offers now (sign_in_providers).</summary>
    public List<string> Providers { get => providers; private set => SetList(ref providers, value); }
    /// <summary>The company of the e-mail address typed at sign-in, with its allowed methods (null: none known).</summary>
    public SignInOptions? SignInOptions { get => signInOptions; private set => SetList(ref signInOptions, value); }

    /// <summary>The sign-in buttons to show: offered by the service and allowed by the company, if known.</summary>
    public IEnumerable<(string Provider, string Label)> SignInButtons =>
        AllProviders.Where(item => Providers.Contains(item.Provider) && (SignInOptions?.Methods is not { } allowed || allowed.Contains(item.Method)))
            .Select(item => (item.Provider, item.Label));

    public async Task LoadProviders()
    {
        try { Providers = await Engine.RunAsync<List<string>>("account_providers"); }
        catch (EngineFailure) { Providers = ["keycloak"]; }
        if (Providers.Count == 0) Providers = ["keycloak"];
    }

    /// <summary>Looks up the sign-in rules of the company an e-mail address belongs to.</summary>
    public Task LookUpSignIn(string email) => Act(async () =>
    {
        SignInOptions = email.Trim().Length == 0 ? null : await Engine.RunAsync<SignInOptions?>("sign_in_options", ("email", email.Trim()));
        if (SignInOptions is { Company: { } company } && !SignInButtons.Any()) throw new EngineFailure(L("%@ allows no sign-in method this app offers. Sign in on the portal.", company));
    });

    public Task SignIn(bool showLink = false, string? provider = null) => Act(async () =>
    {
        var url = await Engine.RunAsync<string>("account_sign_in", ("open_browser", false), ("provider", provider));
        if (showLink) SignInLink = url;
        else if (!string.IsNullOrEmpty(url)) OpenUrl(url);
        await RefreshAccount();
    });

    public Task CancelSignIn() => Act(async () =>
    {
        await Engine.PerformAsync("account_cancel");
        await RefreshAccount();
    });

    public Task Verify(string factor, string? typed = null)
    {
        var code = new string([.. (typed ?? VerificationCode).Where(char.IsAsciiDigit)]);
        if (code.Length is not (6 or 8))
        {
            Error = L("Enter the 6-digit code");
            return Task.CompletedTask;
        }
        VerificationCode = "";
        return Act(async () =>
        {
            try
            {
                await Engine.PerformAsync("account_verify", ("factor", factor), ("code", code));
            }
            catch (EngineFailure failure) when (failure.Message.Contains("HTTP 422", StringComparison.Ordinal))
            {
                throw new EngineFailure(L("That code was not accepted. Enter the current code from your authenticator."));
            }
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
        if (QuickSupportMode && !SignedIn) return StartQuickSupport();
        WentOffline = false;
        autoTries = 1;
        // The automatic retry waits a little, so it never doubles this request.
        nextAutoTry = DateTimeOffset.UtcNow.AddSeconds(5);
        return Act(() => Engine.PerformAsync("remote_register"));
    }

    public Task GoOffline()
    {
        WentOffline = true;
        ClearSessions();
        return Act(() => Engine.PerformAsync("remote_disconnect"));
    }

    /// <summary>Starts Quick Support mode without requiring sign-in or administrator privileges.</summary>
    public Task StartQuickSupport(bool standalone = false)
    {
        WentOffline = false;
        QuickSupportMode = true;
        if (standalone) QuickSupportStandalone = true;
        Section = AppSection.Account;
        return Act(async () =>
        {
            await Engine.PerformAsync("quick_support_start", ("device_name", Environment.MachineName));
        });
    }

    /// <summary>Leaves Quick Support mode and returns to the Sign-In view.</summary>
    public Task StopQuickSupport()
    {
        QuickSupportMode = false;
        WentOffline = true;
        return Act(async () =>
        {
            try { await Engine.PerformAsync("remote_disconnect"); } catch (EngineFailure) { }
        });
    }

    /// <summary>
    /// Connects to a Remote ID: brings this computer online first if needed; for a phone with Mixel Quick
    /// Support it claims the phone and waits until it is online, so one click is enough.
    /// </summary>
    public Task Connect(uint id, string? password = null, bool unattended = false, bool checkPhone = true)
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
            try { await ConnectSteps(id, password, unattended, checkPhone); }
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

    /// <summary>The engine says this computer has no identity for the signed-in account (first use, or one left by another account).</summary>
    internal static bool NeedsSetup(string text) =>
        new[] { "No saved device identity", "not enrolled", "No saved controller identity", "not registered", "Register this computer" }.Any(part => text.Contains(part, StringComparison.Ordinal));

    internal const string SetupMessage = "This computer is not added to your account yet. Choose Add this computer, then connect again.";

    /// <summary>The last connect failed because this computer is not set up: the failure offers "Add this computer".</summary>
    public bool SetupOffer { get => setupOffer; set => Set(ref setupOffer, value); }
    private bool setupOffer;

    /// <summary>Set by the "Add this computer" button of the failed-connect panel; the Devices page opens its dialog and clears it.</summary>
    public bool AddThisRequested { get => addThisRequested; set => Set(ref addThisRequested, value); }
    private bool addThisRequested;

    /// <summary>The failed-connect panel's "Add this computer": closes the failed attempt, goes to Devices and asks for the dialog.</summary>
    public async Task AddThisFromFailure()
    {
        SetupOffer = false;
        await Workspace.CancelAttempt();
        Section = AppSection.Devices;
        AddThisRequested = true;
    }

    private async Task ConnectSteps(uint id, string? password, bool unattended, bool checkPhone = true)
    {
        WentOffline = false;
        SetupOffer = false;
        if (await Status() is { Disconnected: true })
        {
            Progress = L("Getting this computer ready…");
            try { await Engine.PerformAsync("remote_register"); }
            catch (EngineFailure failure) when (NeedsSetup(failure.Message)) { SetupOffer = true; throw new EngineFailure(SetupMessage); }
            if (!await Wait(20, state => state.Phase == "ready" || NeedsSetup(state.Message)))
            {
                throw new EngineFailure("This computer could not go online. Check the internet connection and try again.");
            }
            if (await Status() is { } now && now.Phase != "ready")
            {
                if (NeedsSetup(now.Message)) { SetupOffer = true; throw new EngineFailure(SetupMessage); }
                throw new EngineFailure(string.IsNullOrEmpty(now.Message) ? "This computer could not go online. Check the internet connection and try again." : now.Message);
            }
        }
        // This computer is just registering: wait for it instead of failing the connect.
        if (await Status() is { Phase: "connecting" })
        {
            Progress = L("Getting this computer ready…");
            await Wait(20, state => state.Phase != "connecting");
        }
        var phone = false;
        if (checkPhone)
        {
            Progress = L("Looking up this ID…");
            try
            {
                phone = await Engine.RunAsync<bool>("quick_phone_claim", ("host", id));
            }
            catch (EngineFailure failure)
            {
                if (System.Text.RegularExpressions.Regex.IsMatch(failure.Message, "MFA|staff|42501"))
                {
                    throw new EngineFailure(L("Connecting to phones needs your Mixel team account with the second confirmation step. Sign in again under Account, then choose Connect."));
                }
                throw new EngineFailure(L("This phone could not be prepared for the session: %@", failure.Translated));
            }
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
        // An engine that lists sessions allows several at once: it answers a request beyond its limit with a typed error,
        // so only an older engine (one session) and a shared computer are turned back here.
        if (await Status() is { } current && current.Phase != "ready" && !(current.Sessions is not null && !current.Sharing && current.Phase is "active" or "outgoing" or "negotiating" or "closing"))
        {
            throw new EngineFailure(current.Phase is "active" or "incoming" or "outgoing"
                ? "End the current session first, then connect again."
                : "This computer is not ready to connect yet. Please try again in a moment.");
        }
        requestedUnattended[id] = unattended && !phone;
        // The reply is the new session's stable id (an older engine answers null).
        await Engine.PerformAsync("remote_request", ("host", id), ("password", string.IsNullOrEmpty(password) ? null : password), ("unattended", !phone && unattended));
        if (SessionFor(id) is { } started) started.LastRequestUnattended = unattended && !phone;
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
                "active" => Remote.ControlPending ? L("Connecting control…") : Remote.Control ? L("Connected. Keyboard and mouse control is enabled.") : L("Connected in view-only mode."),
                _ => Translator.Shared.Translate(Remote.Message),
            };
        }
    }

    /// <summary>Before the app quits: end sessions, go offline.</summary>
    public async Task Shutdown()
    {
        Stop();
        ClearSessions();
        try { await Engine.ShutdownAsync(); } catch (EngineFailure) { }
    }
}
